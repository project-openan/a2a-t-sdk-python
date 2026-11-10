"""Client runtime assembly of the RAN energy-saving sample.

:func:`build_client_runtime` wires one run end to end:

* it builds a **per-run** A2A-T SDK ``.env`` from the sample ``.env`` (the SDK reads its
  configuration from a file, never from process environment variables) and never mutates the source
  file;
* it registers the scripted SDK provider before constructing ``A2ATClient`` in offline mode;
* it discovers the server with :func:`protocol.discovery.discover_agent` (registry by default, or
  the direct AgentCard);
* it creates the a2a-sdk client with streaming enabled for SSE and disabled for webhook, owns the
  embedded webhook receiver and its queue, and selects the configured decision provider;
* :meth:`ClientRuntime.run` executes :func:`protocol.client_flow.run_client_flow` with the optional
  live observers used by the Web UI and returns its :class:`~protocol.client_flow.ClientFlowResult`;
* :meth:`ClientRuntime.aclose` stops the receiver and closes every HTTP client and temporary file.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextvars import Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import httpx
from a2a.client import ClientConfig, create_client
from a2a.client.client import ClientCallContext
from a2a.types import GetTaskRequest, TaskState
from a2a.utils.constants import TransportProtocol
from a2a_t.client.a2at_client import A2ATClient
from a2a_t.core.metadata import NegotiationContext
from dotenv import dotenv_values
from shared.agent_card import AGENT_CARD_NAME, AGENT_CARD_ORGANIZATION
from shared.config import (
    DISCOVERY_NMS,
    INTENT_MODE_PROCESSED,
    PURE_MESSAGE_AUTO,
    PURE_MESSAGE_SEND,
    SUPPORTED_TRANSPORTS,
    TRANSPORT_SSE,
    TRANSPORT_WEBHOOK,
    SampleConfig,
    merge_sample_process_env,
)
from shared.model_factory import build_traced_adk_model
from shared.models import NegotiationDecision, NegotiationProposeView
from shared.run_constants import (
    FINAL_STATE_INTENT_NOT_CONFIRMED,
    MAX_ROUNDS,
    PROTOCOL_A2A_T,
    PROTOCOL_PLAIN,
    PROTOCOL_PURE,
    RUN_MODE_CREATE,
    RUN_MODE_MODIFY,
    SUPPORTED_PROTOCOLS,
)
from shared.sdk_env import SdkEnvFile, prepare_sdk_env
from shared.trace import (
    PHASE_TASK_PROMPT,
    ActivitySink,
    TraceRecorder,
    TraceSink,
    current_trace,
    reset_activity_sink,
    reset_task_sink,
    set_activity_sink,
    set_task_sink,
    trace_scope,
)
from shared.trace_propagation import inject_outgoing
from shared.traced_llm import register_traced_providers

from protocol.client_flow import (
    ClientFlowResult,
    ClientTransport,
    DecisionMadeCallback,
    SseClientTransport,
    WebhookClientTransport,
    run_client_flow,
)
from protocol.client_policy import ClientPolicy
from protocol.decision_provider import (
    AdkDecisionProvider,
    ConfirmingDecisionProvider,
    ManualDecisionProvider,
    NegotiationDecisionProvider,
    ScriptedDecisionProvider,
)
from protocol.discovery import DiscoveredAgent, discover_agent
from protocol.intent import (
    IntentProcessor,
    OperatorRequest,
    ProcessedIntent,
    RawIntentProcessor,
)
from protocol.nms_client import (
    NmsClient,
    NmsConfig,
    NmsSseTransport,
    NmsWebhookTransport,
)
from protocol.plain_client_flow import run_plain_client_flow
from protocol.pure_client_flow import pure_proposal_view, run_pure_client_flow
from protocol.run_spec import RunCatalog, RunSpec
from protocol.shared_webhook import SharedWebhook, WebhookSubscription, get_shared_webhook
from protocol.wire_log import HttpWireLog

_LOGGER = logging.getLogger("ran_energy_saving.client_runtime")

#: Client decision modes.
MODE_AUTO: Final[str] = "auto"
MODE_MANUAL: Final[str] = "manual"
MODE_SCRIPTED: Final[str] = "scripted"
MODE_CONFIRM: Final[str] = "confirm"
SUPPORTED_MODES: Final[tuple[str, ...]] = (MODE_AUTO, MODE_MANUAL, MODE_SCRIPTED, MODE_CONFIRM)

#: Discovery key of the direct server base URL.
DIRECT_SERVER_URL_ENV_KEY: Final[str] = "A2AT_SAMPLE_DIRECT_SERVER_URL"

#: Prefix of the temporary SDK ``.env`` directories.
_SDK_ENV_PREFIX: Final[str] = "a2at-ran-client-env-"

#: A2A Task states that refuse a further message. A Modify checks the peer's outer Task too: a
#: previous Modify can fail the outer Task while the vendor Task (the picker's identity) still
#: looks active.
_TERMINAL_A2A_STATES: Final[frozenset[TaskState]] = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
    }
)


class ClientRuntimeError(RuntimeError):
    """Raised when the client runtime cannot be assembled."""


def _env_values(env_path: Path | None) -> dict[str, str]:
    """Read the source ``.env`` values with the shell overlay (empty when the file is missing)."""
    if env_path is None or not env_path.exists():
        values: dict[str, str] = {}
    else:
        values = {
            str(key): str(value)
            for key, value in dotenv_values(env_path).items()
            if key is not None and value is not None
        }
    return merge_sample_process_env(values)


def prepare_client_sdk_env(
    config: SampleConfig,
    env_path: Path | None = None,
) -> SdkEnvFile:
    """Build a per-run SDK ``.env`` without mutating the caller's file.

    The builder lives in :mod:`shared.sdk_env` so the server runtime can use it without importing
    this module; this wrapper keeps the historical client entry point and its directory prefix.

    Args:
        config: resolved sample configuration.
        env_path: source ``.env``; defaults to ``$CWD/.env``.

    Returns:
        The generated file and the temporary directory owning it.
    """
    return prepare_sdk_env(config, env_path, prefix=_SDK_ENV_PREFIX)


def _direct_base_url(config: SampleConfig, env_path: Path | None) -> str:
    """Return the direct-discovery server URL (configured value or the sample bind address)."""
    configured = _env_values(env_path).get(DIRECT_SERVER_URL_ENV_KEY, "").strip()
    return configured or f"http://{config.host}:{config.port}"


def _peer_streams(agent_card: object) -> bool:
    """Return whether one peer AgentCard advertises streaming."""
    capabilities = getattr(agent_card, "capabilities", None)
    return bool(getattr(capabilities, "streaming", False))


def _server_auth_headers(config: SampleConfig) -> dict[str, str]:
    """Return the bearer header of the client-to-server A2A calls, empty when open."""
    token = config.server_auth_token.strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _intent_fields(intent: ProcessedIntent | None) -> dict[str, object]:
    """Map one parsed intent to the Task-T parameter names used by the accept refill.

    The values come from the intent agent, so the refill has an LLM-derived base even when the
    decision model only supplies the negotiated item.
    """
    if intent is None:
        return {}
    fields: dict[str, object] = {
        "operationType": intent.operation_type,
        "region": intent.region,
        "siteList": ",".join(intent.sites),
        "guaranteedThroughputTarget": ", ".join(intent.windows),
        "energySavingGoal": intent.energy_saving_goal,
        "task_description": intent.task_description,
        "expectedOutput": intent.expected_output,
        "backgroundEvent": intent.background_event,
    }
    return {key: value for key, value in fields.items() if value}


def _emit_intent_span(
    *,
    trace_sink: Callable[[dict[str, object]], None] | None,
    intent: ProcessedIntent,
    mode: str,
    scenario: str,
) -> None:
    """Publish the business intent as one Run Explorer trace row."""
    if trace_sink is None:
        return
    recorder = TraceRecorder(current_trace().run_id or "", id_prefix="intent")
    span = recorder.emit(
        kind="intent_processed",
        phase=PHASE_TASK_PROMPT,
        title="Intent processed",
        actor="client",
        side="client",
        direction="internal",
        detail={
            "mode": mode,
            "scenario": scenario,
            "source": intent.source,
            "rawRequest": intent.raw_request,
            "normalizedText": intent.normalized_text,
            "intent": intent.to_dict(),
            "missingFields": list(intent.missing_fields),
            "conflicts": list(intent.conflicts),
            "warnings": list(intent.warnings),
        },
    )
    trace_sink(span.to_dict())


def _emit_intent_span_from_task(
    task: asyncio.Task[ProcessedIntent],
    *,
    trace_sink: Callable[[dict[str, object]], None] | None,
    mode: str,
    scenario: str,
) -> None:
    """Publish the intent row of a concurrently running intent read, ignoring a failed one.

    The callback runs in a copy of the run's context (see :meth:`ClientRuntime.run`), so the
    trace recorder still sees the run id and the task-local sink even though the task finished
    after the flow moved on.
    """
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        _LOGGER.warning("intent read failed; no intent row is published: %s", error)
        return
    _emit_intent_span(
        trace_sink=trace_sink,
        intent=task.result(),
        mode=mode,
        scenario=scenario,
    )


class _IntentFieldsAwaitable:
    """Awaitable adapter turning a concurrent intent task into its Task-T fields.

    A coroutine object would have to be awaited by someone; a run that ends before the flow
    reaches its merge points would leak it with a "never awaited" warning. This object builds the
    coroutine only when it is actually awaited.
    """

    def __init__(self, task: asyncio.Task[ProcessedIntent]) -> None:
        """Wrap one running intent read."""
        self._task = task

    def __await__(self) -> Any:
        """Return the fields read by the wrapped task, ``None`` when the read failed."""
        return self._resolve().__await__()

    async def _resolve(self) -> Mapping[str, object] | None:
        try:
            intent = await self._task
        except Exception as error:  # noqa: BLE001 - the intent layer must not break the run
            _LOGGER.warning("intent fields unavailable; continuing without them: %s", error)
            return None
        return _intent_fields(intent)


def _decision_provider(
    config: SampleConfig,
    run_spec: RunSpec,
    mode: str,
    *,
    model: Any | None = None,
    client_agent_factory: Callable[[Any], Any] | None = None,
    scripted_decisions: Sequence[NegotiationDecision] | None = None,
) -> NegotiationDecisionProvider:
    """Build the decision provider of one client mode.

    The offline model is supplied by the composition root; a scripted provider replays the run
    spec's decisions, or the per-run override when one is supplied. In offline ``auto`` mode the
    model serves the same scripted decisions, so the ADK runner stays in the loop and still
    produces counter/reject/abort decisions.

    Raises:
        ClientRuntimeError: when the mode is not supported.
    """
    if mode == MODE_MANUAL:
        # A2A-T keeps waiting after a local window expires: the window is an observation budget,
        # not a reason to terminate the remote Task. The pure hop constructs its own provider and
        # keeps the legacy single-window policy.
        return ManualDecisionProvider(
            keep_waiting=True,
            timeout_seconds=config.manual_decision_timeout_seconds,
        )
    if mode == MODE_SCRIPTED:
        decisions = scripted_decisions if scripted_decisions is not None else run_spec.scripted_decisions
        return ScriptedDecisionProvider(decisions)
    if mode in (MODE_AUTO, MODE_CONFIRM):
        provider = _build_adk_decision_provider(
            config,
            run_spec,
            model=model,
            client_agent_factory=client_agent_factory,
        )
        if mode == MODE_CONFIRM:
            return ConfirmingDecisionProvider(
                provider,
                timeout_seconds=config.manual_decision_timeout_seconds,
                recommendation_timeout_seconds=config.decision_recommendation_timeout_seconds,
            )
        return provider
    raise ClientRuntimeError(f"Unsupported client mode: {mode}; expected one of {SUPPORTED_MODES}")


def _build_adk_decision_provider(
    config: SampleConfig,
    run_spec: RunSpec,
    *,
    model: Any | None,
    client_agent_factory: Callable[[Any], Any] | None,
) -> AdkDecisionProvider:
    """Build one ADK decision provider for automatic decisions or manual-answer structuring."""
    # The traced wrapper keeps the ADK decision turns visible in the Run Explorer. The original
    # request is carried into every decision prompt: target/feasibility proposals describe the
    # conflict but not the concrete throughput target, so the model cannot resolve them alone.
    return AdkDecisionProvider(
        model=build_traced_adk_model(config, model=model),
        # Only the offline demo supplies a scripted decision model. When an unexpected proposal
        # has no scripted answer, the offline fallback keeps the demo alive. A live run keeps
        # ``offline=False``: an unparseable live decision fails the run instead of inventing an
        # accept the model never made.
        offline=config.is_offline or model is not None,
        user_request=run_spec.user_request,
        session_db_path=config.client_db_path,
        agent_factory=client_agent_factory,
    )


def _announce_pure_proposals(
    answer: Callable[[dict[str, object]], str | Awaitable[str]],
    on_decision_requested: Callable[[NegotiationProposeView, NegotiationContext], None],
    max_rounds: int,
    on_decision_made: DecisionMadeCallback | None = None,
) -> Callable[[dict[str, object]], Awaitable[str]]:
    """Wrap a plain pure answer so every relayed question is reported to the run.

    ``PureConfirmationAnswer`` already publishes through ``set_proposal_sink`` and
    ``set_decision_sink``. Scripted and policy answers do not, which left ``run.proposals`` empty
    and wrote no outbound decision: the tester could not assert the negotiation round and the
    audit stream had only half of each round.

    A pure answer supplies the information the downstream asked for, so it is recorded as an
    ``accept`` decision. That routes through the same decision span and ``negotiation_events``
    writer the A2A-T side uses, which keeps both protocols comparable.
    """

    rounds = 0

    async def announce(event: dict[str, object]) -> str:
        nonlocal rounds
        rounds += 1
        propose, session = pure_proposal_view(
            event,
            default_max_rounds=max_rounds,
            fallback_round=rounds,
        )
        on_decision_requested(propose, session)
        result = answer(event)
        if inspect.isawaitable(result):
            result = await result
        text = result if isinstance(result, str) else ""
        if on_decision_made is not None and text.strip():
            on_decision_made(
                NegotiationDecision(action="accept", rationale=text),
                propose,
                session,
            )
        return result

    return announce


@dataclass
class ClientRuntime:
    """Assembled client runtime of one run.

    Attributes:
        config: resolved sample configuration.
        run_spec: the named run being executed (catalog entry).
        mode: decision mode (``auto`` / ``manual`` / ``scripted``).
        protocol: interaction protocol (``a2a-t`` / ``plain`` / ``pure``).
        transport_name: event transport (``sse`` / ``webhook``).
        prompt_client: A2A-T SDK prompt facade; ``None`` in pure mode.
        a2a_client: a2a-sdk client used by the transports; ``None`` in NMS mode.
        transport: normalized-event transport.
        provider: negotiation decision provider; ``None`` in pure mode.
        discovered: discovered server endpoint.
        sdk_env: generated SDK ``.env`` owned by this runtime; ``None`` in pure mode.
        manual_provider: manual provider when ``mode == "manual"``, else ``None``.
        manual_structurer: ADK provider that structures a manual answer into a decision; ``None``
            when the mode has no live decision model.
        nms_client: raw vendor NMS client; ``None`` outside NMS mode.
        pure_answer: callback answering one pure ``INPUT_REQUIRED`` event.
        intent_processor: business intent understanding step; ``None`` falls back to raw text.
    """

    config: SampleConfig
    run_spec: RunSpec
    mode: str
    protocol: str
    transport_name: str
    prompt_client: A2ATClient | None
    a2a_client: Any | None
    transport: ClientTransport
    provider: NegotiationDecisionProvider | None
    discovered: DiscoveredAgent
    sdk_env: SdkEnvFile | None
    manual_provider: Any | None
    _httpx_client: httpx.AsyncClient | None
    manual_structurer: Any | None = None
    nms_client: NmsClient | None = None
    _closed: bool = False
    trace_sink: Callable[[dict[str, object]], None] | None = None
    #: Liveness sink of this runtime (``None`` when the caller does not show wait states).
    activity_sink: ActivitySink | None = None
    pure_answer: Callable[[dict[str, object]], str | Awaitable[str]] | None = None
    intent_processor: IntentProcessor | None = None
    wire_log: HttpWireLog | None = None
    shared_webhook: SharedWebhook | None = None
    webhook_subscription: WebhookSubscription | None = None
    _sink_token: Token[TraceSink | None | object] | None = None

    def set_recommendation_sink(
        self,
        sink: Callable[[Any, Any, Any], None] | None,
    ) -> None:
        """Install the confirmation recommendation sink when the provider supports one.

        Args:
            sink: callback receiving ``(decision, propose, session)`` before the human wait; the
                decision is ``None`` when the recommendation agent failed (the engineer still
                decides).
        """
        # A pure run has no negotiation decision provider: its confirmation answer owns the human
        # moment and receives the same sink.
        target = self.provider if self.provider is not None else self.pure_answer
        setter = getattr(target, "set_recommendation_sink", None)
        if callable(setter):
            setter(sink)

    def set_decision_wait_sink(self, sink: Callable[[Any], None] | None) -> bool:
        """Install the expired-window observer when the provider supports one.

        Returns:
            ``True`` when the provider accepted the sink. A provider without one must not silently
            keep waiting: its first expired window raises and the run fails without sending.
        """
        # A pure run has no negotiation provider: its confirmation answer owns the human wait and
        # reports the same expired windows to the page.
        target = self.provider if self.provider is not None else self.pure_answer
        setter = getattr(target, "set_wait_sink", None)
        if callable(setter):
            setter(sink)
            return True
        return False

    async def _ensure_outer_task_active(self, task_id: str) -> None:
        """Refuse a pure Modify whose peer Task has already ended.

        The picker validates the vendor Task, but a pure Modify continues the A2A peer's outer
        Task. A previous Modify can fail that outer Task while the vendor Task is still WORKING,
        and the peer then rejects the message with ``InvalidParamsError: ... terminal state``.
        Checking here turns that 0.1 s opaque failure into an actionable message before sending.

        A failed read never blocks the run: the send itself surfaces a real peer error.
        """
        client = self.a2a_client
        if client is None or not task_id:
            return
        try:
            task = await client.get_task(
                GetTaskRequest(id=task_id, history_length=0),
                context=ClientCallContext(timeout=self.config.http_timeout_seconds),
            )
        except Exception as exc:  # noqa: BLE001 - the pre-check is best effort
            _LOGGER.warning("could not read the target Task %s before the Modify: %s", task_id, exc)
            return
        status = getattr(task, "status", None)
        state = getattr(status, "state", None)
        if state in _TERMINAL_A2A_STATES:
            raise ClientRuntimeError(
                f"The target Task {task_id} has already ended ({state}); start a new Task instead "
                "of modifying it."
            )

    def _scenario_raw_request(self) -> str:
        """Return the request of the scenario; it is the text the client sends."""
        return self.run_spec.user_request

    def _raw_mode_request(self, custom_request: str) -> str:
        """Return the request used by the raw (no intent processing) mode."""
        return custom_request or self.run_spec.user_request

    async def _process_intent(self, raw_text: str, *, custom: bool) -> ProcessedIntent:
        """Run the business intent layer, falling back to the scenario intent or raw text."""
        processor = self.intent_processor
        if processor is not None:
            return await processor.process(
                OperatorRequest(
                    text=raw_text,
                    language=self.config.language,
                    scenario=self.run_spec.name,
                    source="client",
                    custom=custom,
                )
            )
        return await RawIntentProcessor().process(
            OperatorRequest(
                text=raw_text,
                language=self.config.language,
                scenario=self.run_spec.name,
                source="client",
                custom=custom,
            )
        )

    async def run(
        self,
        *,
        event_sink: Callable[[dict[str, object]], None] | None = None,
        on_decision_requested: Callable[[NegotiationProposeView, NegotiationContext], None] | None = None,
        on_decision_made: DecisionMadeCallback | None = None,
        on_intent_ready: Callable[[str], Awaitable[bool]] | None = None,
        trace_sink: Callable[[dict[str, object]], None] | None = None,
        activity_sink: ActivitySink | None = None,
        run_id: str | None = None,
        user_request: str | None = None,
        conversation_context: str | None = None,
        validate_before_send: bool = False,
        enforce_required_fields: bool = False,
        site_level_rate_optional: bool = False,
        run_mode: str = RUN_MODE_CREATE,
        target_task_id: str | None = None,
        target_context_id: str | None = None,
        target_server_task_id: str | None = None,
        target_server_context_id: str | None = None,
    ) -> ClientFlowResult:
        """Run the full client flow of the configured scenario.

        The optional trace sink is attached to the process-wide producer hook for the duration of
        the run, and the run id is put into the trace context so the client spans, the LLM/tool
        spans and the ``sampleRunId`` message metadata all agree.

        Args:
            event_sink: Optional live observer of the normalized events (Web UI timeline).
            on_decision_requested: Optional observer invoked before asking the provider.
            on_decision_made: Optional observer invoked immediately after the provider returns,
                before the response is sent.
            on_intent_ready: Optional async approval gate invoked before the Task-T is sent.
            trace_sink: Optional observer of synthetic Run Explorer spans (LLM/tool producers
                included); defaults to the sink the runtime was built with.
            run_id: Optional Run Explorer run id (the Web UI and the CLI pass theirs).
            user_request: Optional natural-language request entered in the Web UI; ``None`` uses
                the scenario's canned request.
            conversation_context: Optional recent-turn block injected into the decision prompt
                and Task-T generation so a follow-up can refer to an earlier run.
            validate_before_send: Validate the rendered Task-T with the SDK content pipeline
                before sending it; only the A2A-T flow implements the gate.
            enforce_required_fields: Run the business-required-field gate before sending an
                operator request. The caller decides the entry points; curated scenarios no longer
                get an exemption.
            run_mode: ``create`` starts a new A2A Task; ``modify`` sends the scenario's Modify
                Task-T against an existing running Task.
            target_task_id: Task a Modify-only run continues.
            target_context_id: Context of the Task a Modify-only run continues.
            target_server_task_id: A2A peer's Task a pure Modify continues (the outer Task owned
                by the OSS Agent). The vendor Task id above is only the picker's identity.
            target_server_context_id: Context paired with ``target_server_task_id``.

        Returns:
            The collected events, decisions and terminal state.
        """
        if self._closed:
            raise ClientRuntimeError("The client runtime is already closed.")
        if self.nms_client is not None:
            self.nms_client.set_wire_path(Path(self.config.runs_dir) / run_id / "wire.jsonl" if run_id else None)
        if self.wire_log is not None:
            # The a2a-sdk HTTP transport records the real SendMessageRequest body for this run, so
            # the wire inspector never has to rebuild the envelope from a normalized event.
            self.wire_log.set_run(self.config.runs_dir, run_id)
        sink = trace_sink if trace_sink is not None else self.trace_sink
        # The UI runs several clients on one loop. A task-local override keeps this run's spans in
        # its own sink instead of whichever run attached a process-wide sink last.
        sink_token = set_task_sink(sink)
        # Liveness is a separate channel: it never enters the trace timeline. A run without an
        # explicit sink still installs ``None`` so a process-wide sink cannot leak into it.
        activity_token = set_activity_sink(
            activity_sink if activity_sink is not None else self.activity_sink
        )
        intent_task: asyncio.Task[ProcessedIntent] | None = None
        try:
            with trace_scope(run_id=run_id, role="client"):
                custom_request = user_request.strip() if isinstance(user_request, str) and user_request.strip() else ""
                processed = self.config.intent_mode == INTENT_MODE_PROCESSED
                modify_mode = run_mode == RUN_MODE_MODIFY
                if modify_mode:
                    if self.protocol == PROTOCOL_PURE:
                        if self.config.discovery == DISCOVERY_NMS:
                            # The direct hop owns the Task the operator picks, so the vendor pair
                            # is the wire identity.
                            if not target_task_id or not target_context_id:
                                raise ClientRuntimeError(
                                    "A direct standard-A2A Modify needs the vendor Task id and "
                                    "its context id."
                                )
                        # Through the OSS Agent only the outer Task exists for the caller; the
                        # vendor Task it forwards to is the OSS hop's own binding.
                        elif not target_server_task_id or not target_server_context_id:
                            raise ClientRuntimeError(
                                "A pure A2A Modify through the OSS Agent needs the outer Task id "
                                "and context of the A2A peer."
                            )
                    elif self.protocol == PROTOCOL_A2A_T:
                        if not target_task_id or not target_context_id:
                            raise ClientRuntimeError(
                                "A Modify-only run needs both the target Task id and its context id."
                            )
                    else:
                        raise ClientRuntimeError("A Modify-only run requires A2A-T or pure A2A.")
                elif target_task_id or target_context_id or target_server_task_id or target_server_context_id:
                    raise ClientRuntimeError("A target Task can only be used by a Modify-only run.")
                raw_text = custom_request or self._scenario_raw_request()
                # The manual-answer structurer is a separate provider (pure and manual mode build
                # one only for the preview); it must see the operator's own request, whichever
                # protocol returns early below. Without this a pure run kept the scenario's canned
                # example request as "Original user request" and the structurer echoed its values.
                structurer = self.manual_structurer
                if structurer is not None:
                    set_structurer_context = getattr(structurer, "set_conversation_context", None)
                    if callable(set_structurer_context):
                        set_structurer_context(conversation_context)
                    set_structurer_request = getattr(structurer, "set_user_request", None)
                    if callable(set_structurer_request):
                        set_structurer_request(raw_text)
                if self.protocol == PROTOCOL_PURE:
                    # Pure A2A uses standard Task states only: the OSS Agent owns the business
                    # intent layer, so the engineer client sends the raw request to it.
                    # A confirmation answer owns the human moment; hand it the same hooks the
                    # A2A-T decision provider receives.
                    direct_hop = self.config.discovery == DISCOVERY_NMS
                    if modify_mode:
                        await self._ensure_outer_task_active(
                            (target_task_id or "") if direct_hop else (target_server_task_id or "")
                        )
                    answer = self.pure_answer
                    # An automatic pure answer keeps its decision provider; hand it the operator
                    # request the run actually executes (the scenario default is only a fallback).
                    set_answer_request = getattr(answer, "set_user_request", None)
                    if callable(set_answer_request):
                        set_answer_request(
                            raw_text if processed else self._raw_mode_request(custom_request)
                        )
                    for setter_name, hook in (
                        ("set_proposal_sink", on_decision_requested),
                        ("set_decision_sink", on_decision_made),
                    ):
                        setter = getattr(answer, setter_name, None)
                        if callable(setter):
                            setter(hook)
                    if (
                        answer is not None
                        and on_decision_requested is not None
                        and not callable(getattr(answer, "set_proposal_sink", None))
                    ):
                        # A scripted/policy answer is a plain callable: wrap it so the run still
                        # reports the relayed question. Without this, ``run.proposals`` stayed empty
                        # and the tester's ``negotiate`` stage waited for the run instead of the round.
                        answer = _announce_pure_proposals(
                            answer,
                            on_decision_requested,
                            MAX_ROUNDS,
                            on_decision_made,
                        )
                    return await run_pure_client_flow(
                        transport=self.transport,
                        user_request=raw_text if processed else self._raw_mode_request(custom_request),
                        answer=answer,
                        max_rounds=MAX_ROUNDS,
                        call_timeout_seconds=self.config.http_timeout_seconds,
                        event_sink=event_sink,
                        trace_sink=sink,
                        # A direct hop continues the vendor Task; the proxy hop continues the
                        # OSS Agent's outer Task and lets it forward to its binding.
                        task_id=(target_task_id if direct_hop else target_server_task_id)
                        if modify_mode
                        else None,
                        context_id=(target_context_id if direct_hop else target_server_context_id)
                        if modify_mode
                        else None,
                        # The vendor answers a standard-A2A request with a standalone ROLE_AGENT
                        # message; only a plain-question-aware flow can answer it, and that
                        # message must not end a Task that is still working.
                        plain_questions=direct_hop,
                        message_ends_flow=not direct_hop,
                    )
                intent: ProcessedIntent | None = None
                if processed:
                    # The intent is an ADK/LLM reading of the request for the intent panel and the
                    # approval step. It never rewrites the request: what the operator wrote is what
                    # the client sends. It is opt-in (``A2AT_SAMPLE_INTENT_MODE=processed``) because
                    # a Modify run pays the same display-only model turn as a Create run. It starts
                    # here and is awaited only where its result is needed, so it overlaps Task-T
                    # generation on runs without a pre-generation approval gate.
                    intent_task = asyncio.create_task(
                        self._process_intent(raw_text, custom=bool(custom_request)),
                        name=f"intent-{run_id or 'run'}",
                    )
                request_text = raw_text
                if self.protocol == PROTOCOL_PLAIN:
                    # Plain A2A has no Task-T, no intent gate and no negotiation: one message in,
                    # task events out. The intent row still describes the request, so wait for it.
                    if intent_task is not None:
                        intent = await intent_task
                        _emit_intent_span(
                            trace_sink=sink,
                            intent=intent,
                            mode=self.config.intent_mode,
                            scenario=self.run_spec.name,
                        )
                    return await run_plain_client_flow(
                        transport=self.transport,
                        user_request=request_text,
                        call_timeout_seconds=self.config.http_timeout_seconds,
                        event_sink=event_sink,
                        trace_sink=sink,
                    )
                set_context = getattr(self.provider, "set_conversation_context", None)
                if callable(set_context):
                    set_context(conversation_context)
                set_request = getattr(self.provider, "set_user_request", None)
                if callable(set_request):
                    set_request(request_text)
                flow_on_intent_ready = on_intent_ready
                # A raw run keeps the historical empty mapping; the parallel path replaces it with
                # the awaitable before the flow starts.
                flow_intent_fields: Mapping[str, object] | Awaitable[Mapping[str, object]] | None = (
                    _intent_fields(None)
                )
                if processed and on_intent_ready is not None:
                    # Approval 1 belongs to the business intent, before the protocol mapping, so
                    # the intent must be ready before the operator is asked. Task-T generation
                    # still waits for the answer: nobody pays for a prompt they rejected.
                    if intent_task is None:
                        raise ClientRuntimeError("Processed intent mode did not start an intent read.")
                    intent = await intent_task
                    _emit_intent_span(
                        trace_sink=sink,
                        intent=intent,
                        mode=self.config.intent_mode,
                        scenario=self.run_spec.name,
                    )
                    if not await on_intent_ready(request_text):
                        blocked = ClientFlowResult()
                        blocked.final_state = FINAL_STATE_INTENT_NOT_CONFIRMED
                        return blocked
                    flow_on_intent_ready = None
                    flow_intent_fields = _intent_fields(intent)
                elif intent_task is not None:
                    # No pre-generation gate: the intent read and Task-T generation overlap. The
                    # span is published when the read finishes, and the flow awaits the fields
                    # after rendering the prompt.
                    intent_context = contextvars.copy_context()
                    intent_task.add_done_callback(
                        lambda task: intent_context.run(
                            _emit_intent_span_from_task,
                            task,
                            trace_sink=sink,
                            mode=self.config.intent_mode,
                            scenario=self.run_spec.name,
                        )
                    )
                    flow_intent_fields = _IntentFieldsAwaitable(intent_task)
                prompt_client = self.prompt_client
                provider = self.provider
                if prompt_client is None or provider is None:
                    raise ClientRuntimeError(
                        "The A2A-T client flow needs a prompt client and a decision provider."
                    )
                return await run_client_flow(
                    prompt_client=prompt_client,
                    transport=self.transport,
                    user_request=request_text,
                    provider=provider,
                    language=self.config.language,
                    intent_fields=flow_intent_fields,
                    event_sink=event_sink,
                    on_decision_requested=on_decision_requested,
                    on_decision_made=on_decision_made,
                    on_intent_ready=flow_on_intent_ready,
                    trace_sink=sink,
                    call_timeout_seconds=self.config.http_timeout_seconds,
                    conversation_context=conversation_context,
                    single_extension_per_message=(self.config.discovery == DISCOVERY_NMS),
                    infer_missing_negotiation_kind=(
                        self.config.discovery == DISCOVERY_NMS
                        and self.config.nms_infer_negotiation_kind
                    ),
                    client_policy=ClientPolicy.from_config(self.config),
                    validate_before_send=validate_before_send,
                    enforce_required_fields=enforce_required_fields,
                    site_level_rate_optional=site_level_rate_optional,
                    run_mode=run_mode,
                    target_task_id=target_task_id,
                    target_context_id=target_context_id,
                )
        finally:
            if intent_task is not None and not intent_task.done():
                # The run ended before the intent read was consumed (a rejected approval or a
                # generation failure). Let it finish so its trace row still lands; the processor
                # degrades to a raw reading instead of raising, so this never masks the outcome.
                try:
                    await intent_task
                except Exception as error:  # noqa: BLE001 - the intent layer is best effort
                    _LOGGER.warning("intent read failed after the run ended: %s", error)
            reset_activity_sink(activity_token)
            reset_task_sink(sink_token)

    async def aclose(self) -> None:
        """Close the HTTP clients and remove the temporary SDK env.

        The webhook receiver is process-owned (see :mod:`protocol.shared_webhook`), so a run never
        stops it; the Web UI application lifespan does.
        """
        if self._closed:
            return
        self._closed = True
        if self.webhook_subscription is not None:
            # Release this run's queue; the process-level receiver itself stays alive.
            self.webhook_subscription.unsubscribe()
            self.webhook_subscription = None
        if self._sink_token is not None:
            # The construction-time sink is task-local (one runtime per run), so clearing it here
            # cannot silence a concurrent run.
            try:
                reset_task_sink(self._sink_token)
            except ValueError:
                # ``aclose`` ran in a different task context; the token is already unreachable.
                pass
            self._sink_token = None
        close_client = getattr(self.a2a_client, "close", None)
        if callable(close_client):
            await close_client()
        if self.nms_client is not None:
            await self.nms_client.aclose()
        if self._httpx_client is not None:
            await self._httpx_client.aclose()
        if self.sdk_env is not None:
            shutil.rmtree(self.sdk_env.directory, ignore_errors=True)


async def build_client_runtime(
    config: SampleConfig,
    scenario: str,
    mode: str = MODE_AUTO,
    transport: str = TRANSPORT_SSE,
    *,
    protocol: str = PROTOCOL_A2A_T,
    catalog: RunCatalog,
    run_id: str = "",
    env_path: Path | None = None,
    trace_sink: Callable[[dict[str, object]], None] | None = None,
    offline_model_factory: Callable[[RunSpec], Any] | None = None,
    install_offline_provider: Callable[[], None] | None = None,
    client_agent_factory: Callable[[Any], Any] | None = None,
    pure_answer: Callable[[dict[str, object]], str | Awaitable[str]] | None = None,
    scripted_decisions: Sequence[NegotiationDecision] | None = None,
    intent_processor_factory: Callable[[SampleConfig], IntentProcessor] | None = None,
) -> ClientRuntime:
    """Assemble the client runtime of one run.

    Args:
        config: resolved sample configuration (language and model mode are process-level).
        scenario: run name resolved through ``catalog``.
        mode: decision mode (``auto`` / ``manual`` / ``scripted``).
        transport: event transport (``sse`` / ``webhook``).
        protocol: interaction protocol (``a2a-t`` / ``plain`` / ``pure``).
        catalog: run catalog supplied by the composition root.
        run_id: client run id; webhook runs use it as the scope of their private callback queue.
        env_path: source ``.env`` used for discovery and for the per-run SDK ``.env``.
        trace_sink: optional Run Explorer sink attached **before** the SDK and ADK clients are
            built, so their LLM/tool producers are traced from the first call.
        offline_model_factory: builder of the deterministic ADK model; required in offline mode.
        install_offline_provider: optional installer of the deterministic SDK LLM routing.
        client_agent_factory: optional builder of the ADK decision agent from its model.
        pure_answer: callback answering one pure ``INPUT_REQUIRED`` event. Pure mode skips the
            A2A-T SDK facade and the negotiation decision provider entirely.
        scripted_decisions: per-run A2A-T decision script used by ``MODE_SCRIPTED``; ``None``
            replays the scenario's catalog script.
        intent_processor_factory: optional builder of the business intent layer; when omitted,
            processed mode falls back to the raw text.

    Returns:
        The assembled runtime; the caller owns it and must call :meth:`ClientRuntime.aclose`.

    Raises:
        ClientRuntimeError: on an unsupported mode/transport, a missing offline factory, or when
            the runtime cannot assemble.
        KeyError: when the run name is unknown to the catalog.
        DiscoveryError: when no usable server endpoint is found.
    """
    if mode not in SUPPORTED_MODES:
        raise ClientRuntimeError(f"Unsupported client mode: {mode}; expected one of {SUPPORTED_MODES}")
    if transport not in SUPPORTED_TRANSPORTS:
        raise ClientRuntimeError(f"Unsupported transport: {transport}; expected one of {SUPPORTED_TRANSPORTS}")
    if protocol not in SUPPORTED_PROTOCOLS:
        raise ClientRuntimeError(f"Unsupported protocol: {protocol}; expected one of {SUPPORTED_PROTOCOLS}")

    run_spec = catalog.spec(scenario, language=config.language)
    intent_processor = intent_processor_factory(config) if intent_processor_factory is not None else None
    register_traced_providers()
    if config.is_offline:
        if offline_model_factory is None:
            raise ClientRuntimeError(
                "Offline mode needs a deterministic model factory; configure live mode or pass "
                "offline_model_factory from the composition root."
            )
        if install_offline_provider is not None:
            install_offline_provider()
    # Task-local: a concurrent run built on the same loop gets its own sink, and `aclose` clears
    # only this runtime's override.
    sink_token = set_task_sink(trace_sink) if trace_sink is not None else None
    if protocol == PROTOCOL_PURE:
        # A pure A2A client does not need the A2A-T SDK facade or its generated .env.
        sdk_env: SdkEnvFile | None = None
        prompt_client: A2ATClient | None = None
    else:
        sdk_env = prepare_client_sdk_env(config, env_path)
        prompt_client = A2ATClient(env_path=sdk_env.path, logger=None)

    discovered = await discover_agent(
        discovery=config.discovery,
        organization=AGENT_CARD_ORGANIZATION,
        name=AGENT_CARD_NAME,
        registry_center_url=config.registry_url,
        direct_base_url=_direct_base_url(config, env_path),
        nms_a2a_base=config.nms_a2a_base,
        env_path=env_path,
    )
    _LOGGER.info("agent discovered: url=%s discovery=%s", discovered.url, config.discovery)

    nms_client: NmsClient | None = None
    a2a_client: Any | None = None
    httpx_client: httpx.AsyncClient | None = None
    shared: SharedWebhook | None = None
    webhook_subscription: WebhookSubscription | None = None
    wire_log = HttpWireLog()
    if config.discovery == DISCOVERY_NMS:
        # The vendor NMS has no AgentCard and no a2a-sdk-compatible transport. The real machine
        # uses the non-streaming ``message:send`` + webhook; the SSE/stream path remains available
        # for the offline simulator and contract tests.
        nms_client = NmsClient(NmsConfig.from_config(config))
        if transport == TRANSPORT_WEBHOOK:
            # Process-level receiver: the callback port is fixed by the launch script and reused
            # by every run, so a slow NMS push never blocks the next start.
            shared = await get_shared_webhook(config)
            webhook_subscription = shared.subscribe(run_id)
            _LOGGER.info("NMS webhook mode: push_url=%s run=%s", shared.url, webhook_subscription.scope)
            flow_transport: ClientTransport = NmsWebhookTransport(
                nms_client,
                queue=webhook_subscription.queue,
                push_url=shared.url,
                push_token=config.webhook_token,
                push_auth_credentials=config.webhook_auth_credentials,
                protocol=protocol,
                idle_timeout=float(config.webhook_idle_timeout),
                bind=webhook_subscription.bind,
                context_id=webhook_subscription.key,
            )
        else:
            flow_transport = NmsSseTransport(
                nms_client,
                protocol=protocol,
                request_timeout=config.nms_request_timeout,
                read_after_send=config.nms_read_after_send,
            )
    else:
        # One budget for both the transport and the per-call context (the SDK applies the context
        # timeout to every request, overriding the httpx client default).
        httpx_client = httpx.AsyncClient(
            timeout=config.http_timeout_seconds,
            trust_env=False,
            headers=_server_auth_headers(config),
            # Inject the Langfuse trace context first, then capture the exact request (including
            # the traceparent header) for the wire inspector.
            event_hooks={"request": [inject_outgoing, wire_log.record_request]},
        )
        # Pick the RPC method from the peer's AgentCard, not from the deployment transport: the
        # local OSS/RAN Agent advertises streaming and benefits from the live event stream, while a
        # peer without streaming support gets one non-streaming ``message/send``. ``send`` remains
        # an explicit conformance override for tests.
        streaming = transport == TRANSPORT_SSE
        if protocol == PROTOCOL_PURE:
            if config.pure_message_mode == PURE_MESSAGE_SEND:
                streaming = False
            elif config.pure_message_mode == PURE_MESSAGE_AUTO:
                streaming = streaming and _peer_streams(discovered.agent_card)
        client_config = ClientConfig(
            streaming=streaming,
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
            use_client_preference=True,
        )
        try:
            a2a_client = await create_client(discovered.agent_card, client_config=client_config)
            if transport == TRANSPORT_WEBHOOK:
                # One receiver per process, one queue per run: concurrent webhook runs share the
                # callback port and are demultiplexed by task/context identity.
                shared = await get_shared_webhook(config)
                webhook_subscription = shared.subscribe(run_id)
                _LOGGER.info("webhook mode: push_url=%s run=%s", shared.url, webhook_subscription.scope)
                flow_transport = WebhookClientTransport(
                    a2a_client,
                    queue=webhook_subscription.queue,
                    push_url=shared.url,
                    push_token=config.webhook_token,
                    idle_timeout=float(config.webhook_idle_timeout),
                    bind=webhook_subscription.bind,
                    context_id=webhook_subscription.key,
                )
            else:
                flow_transport = SseClientTransport(a2a_client)
        except Exception:
            if webhook_subscription is not None:
                webhook_subscription.unsubscribe()
            if sink_token is not None:
                reset_task_sink(sink_token)
            await httpx_client.aclose()
            if sdk_env is not None:
                shutil.rmtree(sdk_env.directory, ignore_errors=True)
            raise

    try:
        manual_structurer: Any | None = None
        if protocol == PROTOCOL_PURE:
            provider: NegotiationDecisionProvider | None = None
            if mode in (MODE_CONFIRM, MODE_MANUAL) and not config.is_offline:
                # The pure form has no decision provider; the confirmation answer still offers the
                # free-text box, so build the same structurer the A2A-T manual mode uses.
                try:
                    manual_structurer = _build_adk_decision_provider(
                        config,
                        run_spec,
                        model=None,
                        client_agent_factory=client_agent_factory,
                    )
                except Exception as exc:  # noqa: BLE001 - optional preview capability
                    _LOGGER.warning("manual answer parsing unavailable: %s", exc)
        else:
            # Only offline mode runs the deterministic decision model. Passing it in live mode
            # made every negotiation decision (and every manual-answer parse) run the fake model,
            # which has no script for an unexpected proposal and fell back to a canned accept.
            offline_model = (
                offline_model_factory(run_spec)
                if config.is_offline and offline_model_factory is not None
                else None
            )
            provider = _decision_provider(
                config,
                run_spec,
                mode,
                model=offline_model,
                client_agent_factory=client_agent_factory,
                scripted_decisions=scripted_decisions,
            )
            if isinstance(provider, ConfirmingDecisionProvider):
                # The confirmation mode already builds an ADK provider for its recommendation;
                # reuse it for the manual-answer preview.
                manual_structurer = provider.structurer
            elif isinstance(provider, ManualDecisionProvider) and not config.is_offline:
                # Manual mode has no automatic decision agent; build one only to structure the
                # engineer's free-text answer. Offline mode requires a live model for this step.
                # A model misconfiguration must not stop the run: the quick decision buttons still
                # work, and the parse endpoint reports that structuring is unavailable.
                try:
                    manual_structurer = _build_adk_decision_provider(
                        config,
                        run_spec,
                        model=offline_model,
                        client_agent_factory=client_agent_factory,
                    )
                except Exception as exc:  # noqa: BLE001 - optional preview capability
                    _LOGGER.warning("manual answer parsing unavailable: %s", exc)
    except BaseException:
        if webhook_subscription is not None:
            webhook_subscription.unsubscribe()
        if sink_token is not None:
            reset_task_sink(sink_token)
        raise
    return ClientRuntime(
        config=config,
        run_spec=run_spec,
        mode=mode,
        protocol=protocol,
        transport_name=transport,
        prompt_client=prompt_client,
        a2a_client=a2a_client,
        transport=flow_transport,
        provider=provider,
        discovered=discovered,
        sdk_env=sdk_env,
        manual_provider=(
            provider
            if protocol == PROTOCOL_A2A_T
            and isinstance(provider, (ManualDecisionProvider, ConfirmingDecisionProvider))
            else pure_answer
            if pure_answer is not None and callable(getattr(pure_answer, "submit", None))
            else None
        ),
        manual_structurer=manual_structurer,
        _httpx_client=httpx_client,
        nms_client=nms_client,
        wire_log=wire_log if httpx_client is not None else None,
        shared_webhook=shared,
        webhook_subscription=webhook_subscription,
        trace_sink=trace_sink,
        _sink_token=sink_token,
        pure_answer=pure_answer,
        intent_processor=intent_processor,
    )


@dataclass
class PollRuntime:
    """Minimal runtime that only polls an already-created remote Task.

    A UI restart rehydrates a run from SQLite; replaying the client flow would send a second
    request. This runtime exposes the same ``a2a_client`` / ``nms_client`` surface that
    :meth:`RunManager._poll_remote_task` reads, and nothing else.
    """

    a2a_client: Any | None = None
    nms_client: NmsClient | None = None
    httpx_client: httpx.AsyncClient | None = None
    manual_provider: Any | None = None

    async def run(self, **kwargs: object) -> ClientFlowResult:
        """Refuse to replay a recovered run."""
        del kwargs
        raise RuntimeError("A recovered run can only poll its existing remote Task.")

    async def aclose(self) -> None:
        """Close the polling HTTP client."""
        if self.httpx_client is not None:
            await self.httpx_client.aclose()


async def build_poll_runtime(
    config: SampleConfig,
    *,
    discovery: str,
    env_path: Path | None = None,
) -> PollRuntime:
    """Build the client used to poll a remote Task after a UI restart."""
    if discovery == DISCOVERY_NMS:
        return PollRuntime(nms_client=NmsClient(NmsConfig.from_config(config)))
    discovered = await discover_agent(
        discovery=discovery,
        organization=AGENT_CARD_ORGANIZATION,
        name=AGENT_CARD_NAME,
        registry_center_url=config.registry_url,
        direct_base_url=_direct_base_url(config, env_path),
        nms_a2a_base=config.nms_a2a_base,
        env_path=env_path,
    )
    httpx_client = httpx.AsyncClient(
        timeout=config.http_timeout_seconds,
        trust_env=False,
        headers=_server_auth_headers(config),
        event_hooks={"request": [inject_outgoing]},
    )
    try:
        a2a_client = await create_client(
            discovered.agent_card,
            client_config=ClientConfig(
                streaming=False,
                httpx_client=httpx_client,
                supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
                use_client_preference=True,
            ),
        )
    except Exception:
        await httpx_client.aclose()
        raise
    return PollRuntime(a2a_client=a2a_client, httpx_client=httpx_client)
