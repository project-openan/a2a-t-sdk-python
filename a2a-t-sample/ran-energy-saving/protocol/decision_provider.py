"""Client-side negotiation decision providers of the RAN energy-saving sample.

The deterministic client flow never decides a negotiation answer itself: it hands the inbound
``Propose`` to a :class:`NegotiationDecisionProvider` and turns the returned
:class:`~shared.models.NegotiationDecision` into protocol content. Three providers cover the three
integration modes of the design:

* :class:`AdkDecisionProvider` runs the ``agents.client_agent`` ADK agent (the default, automatic
  mode);
* :class:`ScriptedDecisionProvider` replays the offline scenario decisions;
* :class:`ManualDecisionProvider` waits for a decision submitted by the Web UI.

Every provider is asynchronous. The flow calls ``respond`` once per inbound proposal and uses the
negotiation session for round/performative context; providers never advance rounds themselves.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

from a2a_t.core.metadata import NegotiationContext
from google.adk.apps import App
from google.adk.events import Event as AdkEvent
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import Runner
from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types
from shared.adk_tracing import TracePlugin
from shared.json_text import iter_json_objects
from shared.model_factory import build_traced_adk_model
from shared.models import NegotiationDecision, NegotiationProposeView
from shared.trace import trace_scope

from protocol.manual_answer import asks_for_confirmation, missing_item_labels

_LOGGER = logging.getLogger("ran_energy_saving.decision_provider")

#: Sentinel distinguishing "this task never set a value" from an explicit ``None``.
_UNSET: Final[object] = object()

#: Per-task prompt inputs of the shared decision provider. The OSS Agent serves concurrent inbound
#: requests with one provider instance, and ``set_user_request`` used to be plain instance state, so
#: one request could overwrite the prompt of another. Context variables give every asyncio task its
#: own value; the instance attributes remain the fallback for callers that set them once.
_USER_REQUEST: ContextVar[object] = ContextVar("a2at_decision_user_request", default=_UNSET)
_CONVERSATION_CONTEXT: ContextVar[object] = ContextVar("a2at_decision_conversation_context", default=_UNSET)

#: ADK application name of the client decision agent.
CLIENT_APP_NAME = "ran_energy_client"

#: ADK user id used for every client decision session.
CLIENT_USER_ID = "ran-energy-client"

#: Default budget of one manual decision (design §12: five minutes).
DEFAULT_MANUAL_TIMEOUT_SECONDS = 300.0

#: Default budget of the agent recommendation before the page opens the manual decision surface.
#: It must stay shorter than the manual window: a slow model must not hide the operator's turn.
DEFAULT_RECOMMENDATION_TIMEOUT_SECONDS = 120.0


class DecisionTimeoutError(Exception):
    """The local wait window expired without an operator decision.

    This is not a protocol decision. A provider that keeps waiting uses this exception only to
    notify the UI that another window expired; the flow must never turn it into a message. If it
    escapes to the flow, the wait sink was never installed, which is a wiring error: fail the run
    loudly instead of sending an abort the operator never chose.
    """

    def __init__(
        self,
        *,
        round_number: int,
        window_seconds: float,
        expired_windows: int,
    ) -> None:
        """Describe one expired local window."""
        self.round_number = round_number
        self.window_seconds = window_seconds
        self.expired_windows = expired_windows
        super().__init__(
            f"no operator decision within {window_seconds:g}s (round {round_number}); "
            f"window {expired_windows} expired"
        )

#: Decision returned by the offline ADK provider when the model output cannot be parsed.
OFFLINE_FALLBACK_DECISION = NegotiationDecision(
    action="accept",
    rationale="The offline model returned no parseable decision; the proposal is accepted.",
)

#: Corrective user turn sent once when a live model answers with prose instead of a decision.
DECISION_RETRY_PROMPT = (
    "Your previous message did not contain a valid NegotiationDecision JSON object. "
    "Return exactly one JSON object with the decision fields and nothing else: no reasoning, "
    "markdown fences, or prose outside the JSON."
)

#: Default retention of a decision session. Sessions are kept as an audit record of what the
#: decision agent saw and answered; the sweep removes the ones older than this.
DEFAULT_SESSION_RETENTION_DAYS = 7

#: Seconds in one day (retention is configured in days, the sweep compares timestamps).
SECONDS_PER_DAY = 24 * 60 * 60


def build_session_service(db_path: str | None) -> tuple[BaseSessionService, bool]:
    """Return the ADK session service of the client decision agent.

    A configured database makes decision sessions durable across a restart; an unset path (unit
    tests, an ephemeral run) keeps them in memory, matching the "no client database" behavior of
    the rest of the sample. When the configured database cannot be opened, the provider degrades to
    an in-memory service instead of failing the run: a decision must not depend on the audit store.

    Args:
        db_path: Absolute SQLite path of the client database, or ``None``.

    Returns:
        The session service and whether it is durable.
    """
    if not db_path:
        return InMemorySessionService(), False
    try:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA user_version")
        return SqliteSessionService(str(path)), True
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        _LOGGER.warning("decision sessions fall back to in-memory storage: cannot open %s: %s", db_path, exc)
        return InMemorySessionService(), False


def _has_session_tables(db_path: str) -> bool:
    """Whether the client database already holds ADK session tables.

    Starting the UI or the gateway must not create the ADK schema when no decision session was ever
    stored; the tables appear on the first run that persists one.
    """
    try:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
            ).fetchone()
    except sqlite3.Error as exc:
        _LOGGER.warning("decision session sweep skipped: %s", exc)
        return False
    return row is not None


async def sweep_stale_sessions(
    db_path: str | None,
    *,
    app_name: str = CLIENT_APP_NAME,
    retention_days: float = DEFAULT_SESSION_RETENTION_DAYS,
) -> int:
    """Delete decision sessions older than the retention window.

    A session is the audit record of one negotiation: the prompts the decision agent received, what
    it answered, and the tool results it saw. The client keeps them for ``retention_days`` and then
    removes them, so the database grows with recent runs only. The age guard also matters because
    the Web UI and the gateway may share one client database: one process must never delete the live
    sessions of the other.

    Args:
        db_path: Absolute SQLite path of the client database, or ``None``.
        app_name: ADK application name owning the sessions.
        retention_days: How long a session is kept after its last update.

    Returns:
        Number of deleted sessions.
    """
    if not db_path or not Path(db_path).is_file():
        return 0
    if not await asyncio.to_thread(_has_session_tables, db_path):
        return 0
    cutoff = time.time() - retention_days * SECONDS_PER_DAY
    try:
        service = SqliteSessionService(str(db_path))
        listing = await service.list_sessions(app_name=app_name)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        _LOGGER.warning("decision session sweep skipped: %s", exc)
        return 0
    deleted = 0
    for session in listing.sessions:
        if session.last_update_time >= cutoff:
            continue
        try:
            await service.delete_session(app_name=app_name, user_id=session.user_id, session_id=session.id)
        except Exception as exc:  # noqa: BLE001 - a stale row must not break the startup
            _LOGGER.warning("decision session %s could not be deleted: %s", session.id, exc)
            continue
        deleted += 1
    if deleted:
        _LOGGER.info("deleted %d stale decision session(s) from %s", deleted, db_path)
    return deleted


class DecisionProviderError(RuntimeError):
    """Raised when a provider cannot produce a decision."""


@runtime_checkable
class NegotiationDecisionProvider(Protocol):
    """Decide how the client answers one inbound negotiation proposal.

    Implementations may call an LLM, read a script, or wait for a human. They must return a
    :class:`NegotiationDecision` and must not send protocol messages or change the round.
    """

    async def respond(
        self,
        propose: NegotiationProposeView,
        session: NegotiationContext,
    ) -> NegotiationDecision: ...


def build_decision_prompt(
    propose: NegotiationProposeView,
    session: NegotiationContext,
    *,
    user_request: str | None = None,
    conversation_context: str | None = None,
) -> str:
    """Build the user message handed to the ADK decision agent for one proposal.

    The original user request matters for target/feasibility proposals: their rendered content
    states the conflict but not the concrete throughput target, so a model without the request
    cannot propose a corrected, non-overlapping target and tends to loop through the tools. The
    conversation context carries earlier turns so a follow-up keeps the same intent.
    """
    original = f"Original user request:\n{user_request}\n\n" if user_request else ""
    history = (
        f"Conversation context (earlier turns):\n{conversation_context.strip()}\n\n"
        if conversation_context and conversation_context.strip()
        else ""
    )
    options = ""
    if propose.options is not None and propose.options.options:
        values = "; ".join(option.value for option in propose.options.options if option.value)
        options = f"Server options for {propose.options.field}: {values}\n\n"
    pure_confirmation = _pure_confirmation(propose)
    pure_rule = (
        "- This is a terse standard-A2A confirmation question and it names no alternative plan. "
        "Return accept with every value the original request states that the question asks about "
        "(exact Task-T parameter names in filled_params), or counter with a concrete revised plan "
        "in counter_proposal. A bare accept with no value is never an answer; if the request "
        "states no usable value, return reject so the caller can ask the operator.\n"
        if pure_confirmation
        else ""
    )
    return (
        "Answer the following A2A-T negotiation proposal.\n"
        f"Negotiation type: {propose.kind}\n"
        f"Round: {session.round} of {session.max_rounds}\n"
        f"Proposal template: {propose.template_uri or 'unknown'}\n"
        f"{original}"
        f"{history}"
        "Proposal content:\n"
        f"{propose.content}\n\n"
        f"{options}"
        "Rules:\n"
        "- The server is authoritative for region, site and cell names, and for whether a goal is "
        "feasible. Never add, rename, correct or reject a region, site or cell on your own. Use the "
        "value the original request states or a value this proposal offers.\n"
        "- Call get_client_policy() when you need the client's own acceptance limits.\n"
        "- If the proposal lists Required Information Items, answer every item in filled_params "
        "using the exact Task-T parameter name. A value must appear in the original request or in "
        "this proposal; do not invent one. If an item cannot be answered from those sources, "
        "return reject and name the missing item in the rationale.\n"
        "- An accept means the client agrees with the server's proposal. Do not restate values the "
        "negotiation did not change. On a feasibility proposal, accept the server's feasible "
        "alternative; the flow renders the agreed value itself.\n"
        "- A2A-T has no counter performative. On a target proposal, return accept with the corrected "
        "target in confirmed_intent and filled_params.guaranteedThroughputTarget. On a feasibility "
        "proposal, return accept with the accepted target in filled_params.guaranteedThroughputTarget "
        "and feasibility_summary. Reject only when the request cannot be clarified or accepted.\n"
        f"{pure_rule}"
        "- Omit fields that do not apply; never pass the string \"None\".\n"
        "Return exactly one NegotiationDecision JSON object and nothing else: no reasoning, "
        "markdown, or prose outside the JSON."
    )


def _pure_confirmation(propose: NegotiationProposeView) -> bool:
    """Return whether the proposal is a plan-less pure-A2A confirmation question."""
    parameters = propose.parameters
    return isinstance(parameters, Mapping) and parameters.get("pureConfirmation") is True


def build_manual_answer_prompt(
    propose: NegotiationProposeView,
    session: NegotiationContext,
    answer: str,
    *,
    user_request: str | None = None,
    conversation_context: str | None = None,
) -> str:
    """Build the prompt that structures one engineer's manual answer into a decision.

    The engineer's words are the source of truth; the model only maps them onto the proposal's
    negotiation shape. For an information proposal it must put every supplied value into
    ``filled_params`` under the exact Task-T parameter name (the client flow merges them into the
    agreed Task-T on Accept). For target/feasibility it uses an Accept whose
    ``confirmed_intent`` / ``feasibility_summary`` carries the engineer's wording and whose
    ``filled_params`` carries any new target. A2A-T has no counter performative.
    """
    original = f"Original user request:\n{user_request}\n\n" if user_request else ""
    history = (
        f"Conversation context (earlier turns):\n{conversation_context.strip()}\n\n"
        if conversation_context and conversation_context.strip()
        else ""
    )
    required_items = missing_item_labels(propose.content)
    items_block = (
        "Parsed required items:\n" + "\n".join(f"- {item}" for item in required_items) + "\n\n"
        if required_items
        else ""
    )
    if propose.kind == "information":
        action_rule = (
            "The engineer is supplying missing information: use action=accept and put every value "
            "the engineer supplied into filled_params, keyed by the exact Task-T parameter name "
            "(for example guaranteedThroughputTarget). Write guaranteedThroughputTarget in the "
            "Task-T form {HH:MM-HH:MM,NMbps}; a full-period rate becomes {00:00-24:00,NMbps}. If "
            "the engineer explicitly refuses, use action=reject. Do not use action=counter for an "
            "information proposal."
        )
    else:
        action_rule = (
            "The engineer is answering a target/feasibility proposal. Use action=accept with the "
            "engineer's wording in confirmed_intent for a target proposal or feasibility_summary for "
            "a feasibility proposal. Put any new guaranteedThroughputTarget in filled_params. There "
            "is no counter action in A2A-T: a revised plan is still an accept. If the engineer "
            "refuses, use action=reject."
        )
    return (
        "Structure the engineer's manual answer to an A2A-T negotiation proposal.\n"
        f"Negotiation type: {propose.kind}\n"
        f"Round: {session.round} of {session.max_rounds}\n"
        f"Proposal template: {propose.template_uri or 'unknown'}\n"
        f"{original}"
        f"{history}"
        "Proposal content:\n"
        f"{propose.content}\n\n"
        f"{items_block}"
        "Engineer's manual answer:\n"
        f"{answer.strip()}\n\n"
        f"{action_rule}\n"
        "Use only values the engineer actually supplied; never invent a missing value. "
        "The Original user request is context only: never copy a value from it into "
        "filled_params unless the engineer's manual answer above also states that value. "
        "Return exactly one NegotiationDecision JSON object and nothing else: no reasoning, "
        "markdown, or prose outside the JSON."
    )


class AdkDecisionProvider:
    """Decision provider backed by the ``agents.client_agent`` ADK agent.

    The provider keeps one ADK session per negotiation session id, so the agent sees the previous
    proposals and decisions of the same negotiation. Sessions live in the client database when one
    is configured and in memory otherwise; a durable session stays as an audit record for the
    configured retention window (see :func:`sweep_stale_sessions`). The structured
    ``NegotiationDecision`` is read from the final event's ``output`` when ADK parsed it and from the
    final text content otherwise; in offline mode an unparseable output falls back to
    :data:`OFFLINE_FALLBACK_DECISION`.
    """

    def __init__(
        self,
        agent: Any | None = None,
        *,
        model: Any | None = None,
        app_name: str = CLIENT_APP_NAME,
        user_id: str = CLIENT_USER_ID,
        fallback_decision: NegotiationDecision | None = None,
        offline: bool = False,
        user_request: str | None = None,
        conversation_context: str | None = None,
        session_db_path: str | None = None,
        agent_factory: Callable[[BaseLlm], Any] | None = None,
    ) -> None:
        """Create one ADK-backed provider.

        Args:
            agent: Pre-built ADK agent; defaults to ``agents.client_agent.agent.build_client_agent``
                with the configured model.
            model: Model override used when ``agent`` is not supplied (the fake model in tests).
            app_name: ADK application name.
            user_id: ADK user id of every decision session.
            fallback_decision: Decision returned when the model output cannot be parsed; ``None``
                raises :class:`DecisionProviderError` instead.
            offline: When ``True`` and no explicit fallback is supplied, use
                :data:`OFFLINE_FALLBACK_DECISION` (the deterministic fake model cannot fail, but a
                malformed offline response must not abort the demo).
            user_request: Original natural-language request of the run; included in every decision
                prompt so target/feasibility proposals can be resolved without guessing.
            conversation_context: Recent turns of the same client conversation; included in every
                decision prompt so a follow-up keeps the earlier intent.
            session_db_path: Absolute SQLite path of the client database holding the decision
                sessions; ``None`` keeps them in memory. An unopenable path degrades to memory
                with a logged warning (see :func:`build_session_service`).
            agent_factory: builder of the decision agent from the configured model, supplied by
                the composition root (the sample's client agent lives with the demo assets).

        Raises:
            ValueError: when neither ``agent`` nor ``agent_factory`` is supplied.
        """
        if agent is None:
            if agent_factory is None:
                raise ValueError(
                    "AdkDecisionProvider needs an agent or an agent_factory; the sample client "
                    "agent is part of the demo package."
                )
            agent = agent_factory(build_traced_adk_model(model=model))
        # The trace plugin is attached through the ``App`` (the ``plugins=`` runner argument is
        # deprecated). Nothing in the decision path depends on it.
        app = App(name=app_name, root_agent=agent, plugins=[TracePlugin()])
        session_service, durable = build_session_service(session_db_path)
        # ``auto_create_session`` replaces the previous "remember which ids I created" set: a
        # durable store may already hold the session (a restarted process), and creating it again
        # raises ``AlreadyExistsError``.
        self._runner = Runner(
            app=app,
            app_name=app_name,
            session_service=session_service,
            auto_create_session=True,
        )
        self._user_id = user_id
        self._fallback = fallback_decision or (OFFLINE_FALLBACK_DECISION if offline else None)
        self._offline = offline
        self._user_request = user_request
        self._conversation_context = conversation_context
        self._durable_sessions = durable

    def set_conversation_context(self, conversation_context: str | None) -> None:
        """Set the recent-turn block used by the next decision prompts in this task."""
        self._conversation_context = conversation_context
        _CONVERSATION_CONTEXT.set(conversation_context)

    def set_user_request(self, user_request: str | None) -> None:
        """Set the natural-language request used by the next decision prompts in this task."""
        self._user_request = user_request
        _USER_REQUEST.set(user_request)

    def _current_user_request(self) -> str | None:
        """Return this task's request, falling back to the construction-time value."""
        value = _USER_REQUEST.get()
        if value is _UNSET:
            return self._user_request
        return value if isinstance(value, str) else None

    def _current_conversation_context(self) -> str | None:
        """Return this task's recent-turn block, falling back to the instance value."""
        value = _CONVERSATION_CONTEXT.get()
        if value is _UNSET:
            return self._conversation_context
        return value if isinstance(value, str) else None

    def grounding_text(self) -> str:
        """Return the operator text a pure A2A answer may use as a grounding source.

        The pure hop has no structured baseline: the answer is sanitized against the operator's
        original request and the vendor's question only.
        """
        request = self._current_user_request() or ""
        context = self._current_conversation_context() or ""
        return "\n".join(part for part in (request, context) if part.strip())

    @property
    def runner(self) -> Runner:
        """The ADK runner serving the decision agent (test hook)."""
        return self._runner

    @property
    def durable_sessions(self) -> bool:
        """Whether decision sessions are persisted in the client database."""
        return self._durable_sessions

    @property
    def is_offline(self) -> bool:
        """Whether this provider serves the deterministic offline demo rather than a live model."""
        return self._offline

    async def respond(
        self,
        propose: NegotiationProposeView,
        session: NegotiationContext,
    ) -> NegotiationDecision:
        """Run the ADK agent over one proposal and return its structured decision.

        The run is executed under ``trace_scope`` (``client`` role, negotiation context, decision
        agent stage) so its LLM/tool spans are tagged with the negotiation they belong to.
        """
        with trace_scope(
            role="client",
            context_id=propose.context_id or propose.negotiation_id,
            stage="client_decision_agent",
        ):
            return await self._respond(propose, session)

    async def _respond(
        self,
        propose: NegotiationProposeView,
        session: NegotiationContext,
    ) -> NegotiationDecision:
        """Run the decision agent and parse its structured decision."""
        session_id = self._session_id(propose.negotiation_id)
        # The runner creates the session on first use (``auto_create_session``) and reuses it on
        # later rounds, so the agent still sees the previous proposals of this negotiation.

        final_event = await self._run_turn(
            session_id,
            build_decision_prompt(
                propose,
                session,
                user_request=self._current_user_request(),
                conversation_context=self._current_conversation_context(),
            ),
        )
        if final_event is None:
            return self._fallback_or_raise("The ADK agent produced no final response.")

        decision = _decision_from_event(final_event)
        if decision is not None:
            return decision
        _log_unparseable_decision("decision model", final_event)

        # A live model can answer with prose even though the request carries a JSON schema (some
        # OpenAI-compatible gateways drop ``response_format``). One corrective turn in the same ADK
        # session is much cheaper than failing the run; the offline fallback still applies when the
        # retry cannot produce a decision either.
        retry_event = await self._run_turn(session_id, DECISION_RETRY_PROMPT)
        if retry_event is not None:
            decision = _decision_from_event(retry_event)
            if decision is not None:
                return decision
            _log_unparseable_decision("decision retry", retry_event)
        return self._fallback_or_raise("The ADK agent produced no parseable NegotiationDecision.")

    async def structure_answer(
        self,
        propose: NegotiationProposeView,
        session: NegotiationContext,
        answer: str,
    ) -> NegotiationDecision | None:
        """Structure one engineer's manual answer into a ``NegotiationDecision``.

        Returns ``None`` when the model cannot produce a usable decision. The caller shows the
        result as a preview; this method never submits anything on its own.
        """
        if not isinstance(answer, str) or not answer.strip():
            return None
        with trace_scope(
            role="client",
            context_id=propose.context_id or propose.negotiation_id,
            stage="client_manual_answer",
        ):
            return await self._structure_answer(propose, session, answer)

    async def _structure_answer(
        self,
        propose: NegotiationProposeView,
        session: NegotiationContext,
        answer: str,
    ) -> NegotiationDecision | None:
        """Run the decision agent over the proposal plus the engineer's manual answer."""
        # A separate session keeps the manual-answer turn from being replayed as an automatic
        # decision turn on the next proposal; the negotiation id still scopes the audit trace.
        session_id = f"{self._session_id(propose.negotiation_id)}-manual-answer-{session.round}"
        final_event = await self._run_turn(
            session_id,
            build_manual_answer_prompt(
                propose,
                session,
                answer,
                user_request=self._current_user_request(),
                conversation_context=self._current_conversation_context(),
            ),
        )
        if final_event is None:
            return None
        decision = _decision_from_event(final_event)
        if decision is None:
            retry_event = await self._run_turn(session_id, DECISION_RETRY_PROMPT)
            if retry_event is not None:
                decision = _decision_from_event(retry_event)
        return _validated_manual_decision(propose, decision)

    async def _run_turn(self, session_id: str, text: str) -> AdkEvent | None:
        """Run one user turn in an existing session and return its final event."""
        final_event: AdkEvent | None = None
        async for event in self._runner.run_async(
            user_id=self._user_id,
            session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part(text=text)]),
        ):
            if event.is_final_response():
                final_event = event
        return final_event

    def _session_id(self, negotiation_id: str) -> str:
        """Return the ADK session id of one negotiation session."""
        return f"ran-client-{negotiation_id}"

    def _fallback_or_raise(self, message: str) -> NegotiationDecision:
        """Return the offline fallback decision or raise a provider error."""
        if self._fallback is not None:
            return self._fallback
        raise DecisionProviderError(message)


def _log_unparseable_decision(label: str, event: AdkEvent) -> None:
    """Log the raw model text of an unparseable decision (truncated for the log)."""
    raw = _event_text(event)
    if raw:
        _LOGGER.warning("%s returned no parseable JSON: %s", label, raw[:300])


def _event_text(event: AdkEvent) -> str:
    """Return the text content of one ADK final event."""
    content = event.content
    if content is None:
        return ""
    return " ".join(part.text for part in content.parts or () if part.text).strip()


def _validated_manual_decision(
    propose: NegotiationProposeView,
    decision: NegotiationDecision | None,
) -> NegotiationDecision | None:
    """Return the structured decision only when it can actually answer the proposal kind.

    An information answer must become an Accept carrying the supplied values; a counter text is
    not merged by the sample server, so returning it would loop the negotiation. A question that
    only asks the client to confirm a plan is the exception: the answer is the agreement itself and
    carries no Task-T value. A target or feasibility counter must carry a concrete plan (or at
    least the engineer's proposal text).
    """
    if decision is None:
        return None
    kind = propose.kind
    if kind == "information":
        if decision.action == "counter":
            return None
        if (
            decision.action == "accept"
            and not decision.filled_params
            and not asks_for_confirmation(propose.content)
        ):
            return None
    elif kind in {"target", "feasibility"}:
        if decision.action == "counter" and not (decision.counter_params or decision.counter_proposal):
            return None
    return decision


def _decision_from_event(event: AdkEvent) -> NegotiationDecision | None:
    """Read a ``NegotiationDecision`` from one ADK final event, ``None`` when absent."""
    output = event.output
    if isinstance(output, NegotiationDecision):
        return output
    if isinstance(output, dict):
        try:
            return NegotiationDecision.model_validate(output)
        except ValueError:
            return None
    content = event.content
    if content is None:
        return None
    for part in content.parts or ():
        if not part.text:
            continue
        parsed = _parse_decision_json(part.text)
        if parsed is not None:
            return parsed
    return None


def _parse_decision_json(text: str) -> NegotiationDecision | None:
    """Parse one JSON decision text, tolerating code fences and surrounding prose."""
    for payload, _raw in iter_json_objects(text):
        try:
            return NegotiationDecision.model_validate(payload)
        except (ValueError, TypeError):
            continue
    return None


class ScriptedDecisionProvider:
    """Decision provider replaying a pre-defined sequence of decisions.

    The offline scenarios of :mod:`demo.scenario` carry their decisions in order; each call to
    :meth:`respond` consumes the next one. Exhausting the sequence is an error, so a scenario whose
    server behavior drifts from its script fails loudly instead of silently accepting.
    """

    def __init__(self, decisions: Sequence[NegotiationDecision]) -> None:
        """Create one scripted provider over an ordered decision list."""
        self._decisions = tuple(decisions)
        self._index = 0

    @property
    def remaining(self) -> int:
        """Number of scripted decisions not consumed yet."""
        return len(self._decisions) - self._index

    async def respond(
        self,
        propose: NegotiationProposeView,
        session: NegotiationContext,
    ) -> NegotiationDecision:
        """Return the next scripted decision.

        Raises:
            DecisionProviderError: when the script has no decision left.
        """
        del propose, session
        if self._index >= len(self._decisions):
            raise DecisionProviderError("The scripted decision sequence is exhausted.")
        decision = self._decisions[self._index]
        self._index += 1
        return decision


class ConfirmingDecisionProvider:
    """Wrap a decision provider with a human confirmation step.

    The inner provider (the ADK decision agent) produces the recommendation. The recommendation is
    published to the Web UI, which then submits the engineer's confirmed or overridden decision
    through :meth:`submit`. The decision that finally leaves the client is always the submitted one.

    A failed recommendation must not lock the engineer out: the recommendation is advisory, so an
    inner-provider error is reported through the sink (``decision=None``) and the provider still
    waits for the manual decision.

    Attributes:
        timeout_seconds: Confirmation budget, forwarded to the manual provider.
        pending: Whether a confirmation is expected or already submitted.
        last_recommendation_error: Reason the last recommendation failed, ``None`` when it worked.
    """

    def __init__(
        self,
        inner: NegotiationDecisionProvider,
        *,
        timeout_seconds: float = DEFAULT_MANUAL_TIMEOUT_SECONDS,
        recommendation_timeout_seconds: float = DEFAULT_RECOMMENDATION_TIMEOUT_SECONDS,
        keep_waiting: bool = True,
    ) -> None:
        """Create one confirming provider around the agent provider.

        ``keep_waiting`` is the A2A-T default: the local window is only an observation budget and
        never ends the negotiation. The pure hop keeps its legacy single-window behaviour by
        constructing ``ManualDecisionProvider`` directly.

        ``recommendation_timeout_seconds`` bounds the advisory model turn. When it expires the page
        is told the recommendation is unavailable and the operator decides immediately.
        """
        if recommendation_timeout_seconds <= 0:
            raise ValueError("recommendation_timeout_seconds must be positive")
        self._inner = inner
        self._manual = ManualDecisionProvider(
            timeout_seconds=timeout_seconds,
            keep_waiting=keep_waiting,
        )
        self._recommendation_timeout_seconds = recommendation_timeout_seconds
        self._sink: Callable[[NegotiationDecision | None, NegotiationProposeView, Any], None] | None = None
        self._last_recommendation_error: str | None = None

    @property
    def last_recommendation_error(self) -> str | None:
        """Reason the last recommendation failed, ``None`` when it produced a decision."""
        return self._last_recommendation_error

    @property
    def timeout_seconds(self) -> float:
        """Confirmation budget in seconds."""
        return self._manual.timeout_seconds

    @property
    def recommendation_timeout_seconds(self) -> float:
        """Budget of the advisory recommendation before the operator decides alone."""
        return self._recommendation_timeout_seconds

    @property
    def pending(self) -> bool:
        """Whether a confirmation is expected or already submitted."""
        return self._manual.pending

    @property
    def structurer(self) -> AdkDecisionProvider | None:
        """The inner ADK provider when this confirmation wraps one, else ``None``."""
        return self._inner if isinstance(self._inner, AdkDecisionProvider) else None

    def set_recommendation_sink(
        self,
        sink: Callable[[NegotiationDecision | None, NegotiationProposeView, Any], None] | None,
    ) -> None:
        """Install the callback invoked before the human wait.

        The callback receives the agent recommendation, or ``None`` when the recommendation failed;
        :attr:`last_recommendation_error` carries the reason in that case.
        """
        self._sink = sink

    def set_wait_sink(self, sink: Callable[[DecisionTimeoutError], None] | None) -> None:
        """Install the callback invoked when a local wait window expires (advisory only)."""
        self._manual.set_wait_sink(sink)

    def set_conversation_context(self, conversation_context: str | None) -> None:
        """Forward the conversation context to the inner decision provider."""
        setter = getattr(self._inner, "set_conversation_context", None)
        if callable(setter):
            setter(conversation_context)

    def set_user_request(self, user_request: str | None) -> None:
        """Forward the natural-language request to the inner decision provider."""
        setter = getattr(self._inner, "set_user_request", None)
        if callable(setter):
            setter(user_request)

    def submit(self, decision: NegotiationDecision) -> None:
        """Hand the engineer's decision to the waiting flow."""
        self._manual.submit(decision)

    async def respond(
        self,
        propose: NegotiationProposeView,
        session: NegotiationContext,
    ) -> NegotiationDecision:
        """Race the recommendation against the operator, then publish the outcome.

        The two waits run concurrently, so the operator can decide before the model answers and a
        slow model cannot hide the decision surface. A submission wins over the recommendation; a
        recommendation that finishes first is published and the operator still decides. When the
        recommendation budget expires, the page receives ``recommendationUnavailable`` and the
        operator keeps the full manual window.
        """
        self._last_recommendation_error = None
        recommendation_task = asyncio.create_task(self._recommend(propose, session))
        submission_task = asyncio.create_task(self._manual.wait_for_operator(propose, session))
        try:
            done, _pending = await asyncio.wait(
                {recommendation_task, submission_task},
                timeout=self._recommendation_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if submission_task in done:
                # The operator outran the model. Discard the late recommendation: publishing it
                # now would reopen a card the operator already resolved.
                await self._discard(recommendation_task)
                return submission_task.result()
            if recommendation_task in done:
                recommendation = recommendation_task.result()
                if self._sink is not None:
                    self._sink(recommendation, propose, session)
                return await submission_task
            await self._discard(recommendation_task)
            self._last_recommendation_error = (
                f"RecommendationTimeout: no recommendation within "
                f"{self._recommendation_timeout_seconds:g}s"
            )
            if self._sink is not None:
                self._sink(None, propose, session)
            return await submission_task
        finally:
            await self._discard(recommendation_task)
            if not submission_task.done():
                await self._discard(submission_task)

    async def _recommend(
        self,
        propose: NegotiationProposeView,
        session: NegotiationContext,
    ) -> NegotiationDecision | None:
        """Run one advisory recommendation turn; failures become ``None`` with the reason."""
        try:
            return await self._inner.respond(propose, session)
        except Exception as exc:  # noqa: BLE001 - the engineer is the authority, not the model
            _LOGGER.warning(
                "decision recommendation unavailable: %s: %s",
                type(exc).__name__,
                exc,
            )
            self._last_recommendation_error = f"{type(exc).__name__}: {exc}"
            return None

    @staticmethod
    async def _discard(task: asyncio.Task[object]) -> None:
        """Cancel one loser task and swallow its terminal exception."""
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


class ManualDecisionProvider:
    """Decision provider waiting for a decision submitted by a human (Web UI).

    ``submit`` may be called before or while ``respond`` waits. ``keep_waiting`` separates the two
    policies: A2A-T keeps waiting after a window expires and only notifies the page, while the pure
    hop keeps its legacy single-window abort (which it maps to an empty answer and never sends).
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_MANUAL_TIMEOUT_SECONDS,
        keep_waiting: bool = False,
    ) -> None:
        """Create one manual provider with the given decision budget."""
        self._timeout_seconds = timeout_seconds
        self._keep_waiting = keep_waiting
        self._queue: asyncio.Queue[NegotiationDecision] = asyncio.Queue()
        self._waiting = False
        self._on_window_expired: Callable[[DecisionTimeoutError], None] | None = None

    @property
    def timeout_seconds(self) -> float:
        """Decision budget in seconds."""
        return self._timeout_seconds

    @property
    def keep_waiting(self) -> bool:
        """Whether an expired window only notifies the page instead of producing a decision."""
        return self._keep_waiting

    @property
    def pending(self) -> bool:
        """Whether a decision is submitted and not consumed yet, or a decision is expected."""
        return self._waiting or not self._queue.empty()

    def set_wait_sink(self, sink: Callable[[DecisionTimeoutError], None] | None) -> None:
        """Install the callback invoked when a local wait window expires.

        The callback is advisory: a failing observer must not change the wait, so callers catch
        everything it raises.
        """
        self._on_window_expired = sink

    def submit(self, decision: NegotiationDecision) -> None:
        """Hand one decision to the waiting flow."""
        self._queue.put_nowait(decision)

    async def respond(
        self,
        propose: NegotiationProposeView,
        session: NegotiationContext,
    ) -> NegotiationDecision:
        """Wait for the next submitted decision, following the configured timeout policy."""
        if self._keep_waiting:
            return await self.wait_for_operator(propose, session)
        del propose
        self._waiting = True
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=self._timeout_seconds)
        except TimeoutError:
            return NegotiationDecision(
                action="abort",
                rationale=(
                    f"The manual decision for negotiation round {session.round} timed out after "
                    f"{self._timeout_seconds:g}s; the negotiation is aborted."
                ),
            )
        finally:
            self._waiting = False

    async def wait_for_operator(
        self,
        propose: NegotiationProposeView,
        session: NegotiationContext,
    ) -> NegotiationDecision:
        """Wait for a decision, notifying the page after each expired window.

        The wait never ends the run and never produces a protocol decision. With no sink the first
        expired window raises :class:`DecisionTimeoutError`, because silently waiting forever would hide
        a wiring error; the flow turns that into a failed run with nothing sent.
        """
        del propose
        expired_windows = 0
        self._waiting = True
        try:
            while True:
                try:
                    return await asyncio.wait_for(self._queue.get(), timeout=self._timeout_seconds)
                except TimeoutError:
                    expired_windows += 1
                    timeout = DecisionTimeoutError(
                        round_number=session.round,
                        window_seconds=self._timeout_seconds,
                        expired_windows=expired_windows,
                    )
                    if self._on_window_expired is None:
                        raise timeout from None
                    try:
                        self._on_window_expired(timeout)
                    except Exception:  # noqa: BLE001 - observation must not change the wait
                        _LOGGER.warning(
                            "decision wait notification failed",
                            exc_info=True,
                        )
        finally:
            self._waiting = False
