"""Client flow of the pure A2A mode: standard Task negotiation, no A2A-T extension.

The flow sends a plain natural-language message, reads the server's ``INPUT_REQUIRED`` status,
asks the caller for an answer, and continues the same Task with ``taskId``/``contextId``. It owns
no business rule: the answer callback decides what to say, and the server's policy decides whether
the answer resolves the negotiation.
"""

from __future__ import annotations

import inspect
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Final

from a2a.client.client import ClientCallContext
from a2a.types import Role, SendMessageRequest
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from shared.config import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    PURE_FOLLOWUP_BOTH,
    PURE_FOLLOWUP_CONTEXT,
    PURE_FOLLOWUP_TASK,
)
from shared.models import NegotiationProposeView
from shared.server_intent import parse_server_intent
from shared.trace import PHASE_NEGOTIATION, PHASE_TASK_PROMPT, TraceRecorder, current_trace

from protocol.client_flow import (
    ClientFlowResult,
    ClientTransport,
    _fill_result,
    _final_state,
)
from protocol.grounding import server_target_rates
from protocol.manual_answer import requires_operator_decision
from protocol.messages import is_negotiation_message, metadata_to_dict

_LOGGER = logging.getLogger("ran_energy_saving.pure_client_flow")

#: Short text part of the pure request (the task itself is in the same part).
DEFAULT_PURE_LABEL: str = "RAN energy saving task"

#: Negotiation kinds the A2A-T side already uses; the OSS Agent forwards one on the pure hop.
_NEGOTIATION_KINDS: frozenset[str] = frozenset({"information", "target", "feasibility"})

#: ``pure.phase`` values that describe the OSS hop rather than the negotiation itself.
_RELAY_PHASES: frozenset[str] = frozenset({"question", "answer"})

#: Prefixes of the downstream's success copy. A real NMS reports a completed Create/Modify as a
#: status with this text (usually followed by its structured Task payload) and no negotiation
#: metadata; that frame is a receipt, not a question the client must answer.
_RECEIPT_PREFIXES: Final[tuple[str, ...]] = (
    "task creation succeeded",
    "task modification succeeded",
    "任务创建成功",
    "任务修改成功",
)

#: Reason recorded when the caller's answer script cannot answer a vendor question any more.
_UNANSWERED_QUESTION_REASON: Final[str] = (
    "厂商第 {round} 轮追问没有可用答案（用例提供的答案已耗尽或为空）；"
    "未再作答，下游回执与意图已保留。"
)


async def run_pure_client_flow(
    *,
    transport: ClientTransport,
    user_request: str,
    answer: Callable[[dict[str, object]], str | Awaitable[str]] | None = None,
    on_answer: Callable[[dict[str, object], str], Awaitable[None]] | None = None,
    max_rounds: int = 3,
    call_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    event_sink: Callable[[dict[str, object]], None] | None = None,
    trace_sink: Callable[[dict[str, object]], None] | None = None,
    followup: str = PURE_FOLLOWUP_BOTH,
    plain_questions: bool = False,
    message_ends_flow: bool = True,
    task_id: str | None = None,
    context_id: str | None = None,
) -> ClientFlowResult:
    """Send one pure A2A request and answer its ``INPUT_REQUIRED`` rounds.

    Args:
        transport: SSE or webhook transport (the demo uses SSE).
        user_request: Natural-language request.
        answer: Callback receiving one ``INPUT_REQUIRED`` event and returning the follow-up text
            (or an awaitable of text); defaults to the server proposal carried by the event
            metadata.
        max_rounds: Maximum negotiation rounds.
        call_timeout_seconds: HTTP budget of one send.
        event_sink: Optional live observer of normalized events.
        trace_sink: Optional observer of synthetic ``message_sent`` spans.
        followup: Conversation fields of the follow-up message (``both``/``task``/``context``).
        plain_questions: Whether a plain agent message without negotiation metadata asks the
            client for information. The real vendor NMS answers a pure request this way; the
            local demo server never does, so the default stays false.
        task_id: Existing Task the message continues (a pure-A2A Modify). Standard A2A lets a
            non-terminal Task be continued; the peer forwards the text to its own downstream.
        context_id: Context paired with ``task_id``.

    The downstream correlation travels on the ``X-A2AT-Run-Id``/``traceparent`` headers (the
    propagation context), so the message itself carries no sample-local metadata.

    Returns:
        The collected events, task id and report.
    """
    transport_emits_events = _attach_event_sink(transport, event_sink)
    result = ClientFlowResult()
    request = (
        _follow_up_request(
            user_request,
            task_id=task_id,
            context_id=context_id,
            followup=PURE_FOLLOWUP_BOTH,
        )
        if task_id or context_id
        else _pure_request(user_request)
    )
    _emit_message_sent(request, user_request, trace_sink)
    batch = await transport.send(request, _call_context(call_timeout_seconds))
    _collect(result, batch, event_sink, transport_emits_events)
    return await _drive_negotiation(
        transport=transport,
        result=result,
        batch=batch,
        answer=answer,
        on_answer=on_answer,
        max_rounds=max_rounds,
        call_timeout_seconds=call_timeout_seconds,
        event_sink=event_sink,
        trace_sink=trace_sink,
        followup=followup,
        plain_questions=plain_questions,
        transport_emits_events=transport_emits_events,
        default_task_id=task_id,
        default_context_id=context_id,
        message_ends_flow=message_ends_flow,
    )


async def continue_pure_client_flow(
    *,
    transport: ClientTransport,
    text: str,
    task_id: str | None,
    context_id: str | None,
    answer: Callable[[dict[str, object]], str | Awaitable[str]] | None = None,
    on_answer: Callable[[dict[str, object], str], Awaitable[None]] | None = None,
    max_rounds: int = 3,
    call_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    event_sink: Callable[[dict[str, object]], None] | None = None,
    trace_sink: Callable[[dict[str, object]], None] | None = None,
    followup: str = PURE_FOLLOWUP_BOTH,
    plain_questions: bool = False,
    message_ends_flow: bool = True,
) -> ClientFlowResult:
    """Answer a question the downstream asked after the original request phase ended.

    The OSS Agent relays a downstream text question to its caller, then resumes the same
    downstream context with the operator's answer. This function owns that second phase; it
    shares the negotiation loop with :func:`run_pure_client_flow`.
    """
    transport_emits_events = _attach_event_sink(transport, event_sink)
    result = ClientFlowResult()
    request = _follow_up_request(
        text,
        task_id=task_id,
        context_id=context_id,
        followup=followup,
    )
    _emit_message_sent(request, text, trace_sink)
    batch = await transport.send(request, _call_context(call_timeout_seconds))
    _collect(result, batch, event_sink, transport_emits_events)
    return await _drive_negotiation(
        transport=transport,
        result=result,
        batch=batch,
        answer=answer,
        on_answer=on_answer,
        max_rounds=max_rounds,
        call_timeout_seconds=call_timeout_seconds,
        event_sink=event_sink,
        trace_sink=trace_sink,
        followup=followup,
        plain_questions=plain_questions,
        transport_emits_events=transport_emits_events,
        default_task_id=task_id,
        default_context_id=context_id,
        message_ends_flow=message_ends_flow,
    )


def _attach_event_sink(
    transport: ClientTransport,
    event_sink: Callable[[dict[str, object]], None] | None,
) -> bool:
    """Attach the live event sink when the transport supports it."""
    if event_sink is None:
        return False
    set_event_sink = getattr(transport, "set_event_sink", None)
    if callable(set_event_sink):
        set_event_sink(event_sink)
        return True
    return False


def _collect(
    result: ClientFlowResult,
    batch: list[dict[str, object]],
    event_sink: Callable[[dict[str, object]], None] | None,
    transport_emits_events: bool,
) -> None:
    """Store one send phase's events and forward them when the transport does not."""
    result.events.extend(batch)
    if event_sink is not None and not transport_emits_events:
        for event in batch:
            event_sink(event)


async def _drive_negotiation(
    *,
    transport: ClientTransport,
    result: ClientFlowResult,
    batch: list[dict[str, object]],
    answer: Callable[[dict[str, object]], str | Awaitable[str]] | None,
    on_answer: Callable[[dict[str, object], str], Awaitable[None]] | None,
    max_rounds: int,
    call_timeout_seconds: float,
    event_sink: Callable[[dict[str, object]], None] | None,
    trace_sink: Callable[[dict[str, object]], None] | None,
    followup: str,
    plain_questions: bool,
    transport_emits_events: bool,
    default_task_id: str | None = None,
    default_context_id: str | None = None,
    message_ends_flow: bool = True,
) -> ClientFlowResult:
    """Answer the negotiated rounds of one already-sent pure request."""
    rounds = 0
    while rounds < max_rounds:
        pending = _pending_negotiation(batch, allow_plain_question=plain_questions)
        if pending is None:
            break
        text = await _answer_for(pending, answer)
        if not text.strip():
            # The question is real but the caller has no answer left. Leaving it unanswered is
            # the honest outcome; the receipt and its intent payload stay in the recorded events.
            result.error = _UNANSWERED_QUESTION_REASON.format(round=rounds + 1)
            _emit_flow_note(
                trace_sink,
                kind="answer_unavailable",
                title="Pure A2A question left unanswered",
                round_number=rounds + 1,
                context_id=_text(pending.get("context_id")),
                detail={"reason": result.error},
            )
            break
        if on_answer is not None:
            await on_answer(pending, text)
        # A vendor question may carry no conversation ids (a standalone ROLE_AGENT message).
        # Fall back to the Task this run is continuing so the answer keeps the same identity as
        # the request that provoked it.
        follow = _follow_up_request(
            text,
            task_id=_text(pending.get("task_id")) or default_task_id,
            context_id=_text(pending.get("context_id")) or default_context_id,
            followup=followup,
        )
        _emit_message_sent(follow, text, trace_sink)
        batch = await transport.send(follow, _call_context(call_timeout_seconds))
        _collect(result, batch, event_sink, transport_emits_events)
        rounds += 1

    # Only the last send phase decides the state; a prior round's message must not look like the
    # end of the flow. A direct vendor hop keeps answering standalone messages, so a plain
    # message there is a question/observation, not a terminal signal.
    result.final_state = _final_state(batch, message_ends_flow=message_ends_flow)
    _fill_result(result, _context_id_of(result.events))
    _LOGGER.info(
        "pure client flow finished: state=%s task_id=%s rounds=%s",
        result.final_state,
        result.task_id,
        rounds,
    )
    return result


def _pure_request(text: str) -> SendMessageRequest:
    """Build one user-role A2A message with no metadata."""
    return _message_request(text)


def build_follow_up_request(
    text: str,
    *,
    task_id: str | None,
    context_id: str | None,
    followup: str = PURE_FOLLOWUP_BOTH,
) -> SendMessageRequest:
    """Build the continuation message that resumes an ``INPUT_REQUIRED`` Task.

    Public so a caller that only observed the Task by polling (the Web UI long-task watcher) sends
    the operator's answer with the same fields the streaming flow uses.
    """
    return _follow_up_request(text, task_id=task_id, context_id=context_id, followup=followup)


def _follow_up_request(
    text: str,
    *,
    task_id: str | None,
    context_id: str | None,
    followup: str = PURE_FOLLOWUP_BOTH,
) -> SendMessageRequest:
    """Build the continuation message that resumes an ``INPUT_REQUIRED`` Task."""
    request = _message_request(text)
    if task_id and followup in (PURE_FOLLOWUP_BOTH, PURE_FOLLOWUP_TASK):
        request.message.task_id = task_id
    if context_id and followup in (PURE_FOLLOWUP_BOTH, PURE_FOLLOWUP_CONTEXT):
        request.message.context_id = context_id
    return request


def _message_request(text: str) -> SendMessageRequest:
    """Build one user-role A2A message with no metadata.

    The run correlation travels on ``X-A2AT-Run-Id``/``traceparent`` headers, so a standard A2A
    message stays exactly that: no sample-local key and no A2A-T extension.
    """
    request = SendMessageRequest()
    request.message.message_id = str(uuid.uuid4())
    request.message.role = Role.ROLE_USER
    request.message.parts.add().text = text
    return request


def _call_context(timeout: float) -> ClientCallContext:
    """Return the no-extension call context of one pure send."""
    return ClientCallContext(
        timeout=timeout,
        service_parameters={"A2A-Extensions": ""},
    )


def _pending_negotiation(
    events: list[dict[str, object]],
    *,
    allow_plain_question: bool = False,
) -> dict[str, object] | None:
    """Return the newest event the client must answer.

    The local sample tags a negotiation with its ``pure`` metadata; a real NMS returns a standard
    A2A status without that tag. An MIE ``approval`` phase belongs to the RAN Agent and is not
    answered by the OSS Agent.

    A vendor that does not negotiate at the protocol level answers a pure request with a plain
    agent ``message`` (no Task, no negotiation metadata). ``allow_plain_question`` makes that
    text an answerable question; a message that belongs to a Task is still an observation.
    """
    for event in reversed(events):
        kind = event.get("kind")
        if kind == "status":
            if event.get("state") != "TASK_STATE_INPUT_REQUIRED":
                continue
            pure = pure_metadata(event)
            if pure.get("kind") == "approval":
                continue
            if pure and pure.get("kind") not in (None, "negotiation"):
                continue
            if _is_downstream_receipt(event):
                # The vendor reports a completed Modify as INPUT_REQUIRED plus its structured
                # Task payload (and no negotiation metadata). Answering it would spend a round the
                # case never recorded and leave the Task waiting for an answer nobody asked for.
                return None
            return event
        if allow_plain_question and is_plain_question(event):
            return event
    return None


def is_plain_question(event: Mapping[str, object]) -> bool:
    """Return whether one normalized pure event is a vendor text question.

    A vendor that does not negotiate at the protocol level answers a pure request with a plain
    agent ``message`` (no Task, no negotiation metadata). A message that belongs to a Task is an
    observation: the Task state carries the protocol meaning.
    """
    if event.get("kind") != "message":
        return False
    text = event.get("text")
    if not isinstance(text, str) or not text.strip():
        return False
    metadata = event.get("metadata")
    if isinstance(metadata, Mapping) and is_negotiation_message(metadata_to_dict(metadata)):
        return False
    return not event.get("task_id")


def _is_downstream_receipt(event: Mapping[str, object]) -> bool:
    """Return whether one pure status is the downstream's success receipt, not a question.

    A real NMS stops a pure Modify at ``INPUT_REQUIRED`` while the copy says the change already
    succeeded (``Task modification succeeded{...}``) and carries no negotiation metadata. The
    structured Task payload, or the localized success prefix, identifies the receipt.
    """
    text = event.get("text")
    if not isinstance(text, str) or not text.strip():
        return False
    if parse_server_intent(text) is not None:
        return True
    lowered = text.lstrip().lower()
    return any(lowered.startswith(prefix) for prefix in _RECEIPT_PREFIXES)


def pure_metadata(event: Mapping[str, object]) -> dict[str, object]:
    """Return the sample-local ``pure`` metadata of one normalized event."""
    metadata = event.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    raw = metadata.get("pure")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _positive_int(value: object, default: int) -> int:
    """Return one positive int metadata value, or the default."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def pure_negotiation_kind(pure: Mapping[str, object]) -> str:
    """Return the negotiation kind of one sample-local ``pure`` metadata blob.

    ``negotiationKind`` is the explicit tag the OSS Agent forwards from its downstream hop. The
    local RAN Agent tags its own questions with ``phase``. An OSS relay only carries
    ``phase="question"``/``"answer"``, which describes the hop rather than the negotiation, so it
    falls back to the generic ``information`` kind. A question with no tag at all is likewise a
    request for information.
    """
    for key in ("negotiationKind", "phase"):
        raw = pure.get(key)
        if not isinstance(raw, str):
            continue
        value = raw.strip().lower()
        if value in _NEGOTIATION_KINDS:
            return value
        if value in _RELAY_PHASES:
            return "information"
        if value:
            # Unknown is not a business value: record it as unknown instead of guessing a kind.
            _LOGGER.warning("unknown pure negotiation kind %r; recording it as unknown", raw)
            return "unknown"
    return "information"


def pure_proposal_view(
    event: Mapping[str, object],
    *,
    default_max_rounds: int = 3,
    fallback_round: int | None = None,
) -> tuple[NegotiationProposeView, NegotiationContext]:
    """Build the proposal view of one relayed pure A2A question.

    The pure hop has no Negotiation-T metadata, so the view is derived from the question text and
    the sample-local ``pure`` tags. Both the confirmation card and the plain scripted answer use
    this view, which keeps the run's negotiation panel and the tester's round assertion honest.

    Args:
        event: One normalized downstream question.
        default_max_rounds: Round budget used when the event carries none.
        fallback_round: Round number for a peer that does not number its rounds. Callers pass the
            count of questions already answered in this run.
    """
    pure = pure_metadata(event)
    text = str(event.get("text") or "").strip() or (
        "The downstream requested additional information."
    )
    context_id = str(event.get("context_id") or event.get("task_id") or "pure-question")
    round_number = _positive_int(pure.get("round"), fallback_round if fallback_round is not None else 1)
    max_rounds = _positive_int(pure.get("maxRounds"), default_max_rounds)
    kind = pure_negotiation_kind(pure)
    if kind == "information" and requires_operator_decision(text):
        # The vendor's plain-A2A front end does not tag its questions. "Not feasible, please
        # confirm" is a feasibility objection, not a missing-value request: labelling it
        # information would send the decision agent down the wrong rule set.
        kind = "feasibility"
    propose = NegotiationProposeView(
        negotiation_id=context_id,
        kind=kind,
        round=round_number,
        max_rounds=max_rounds,
        content=text,
        template_uri=None,
        context_id=context_id,
    )
    if kind in ("feasibility", "target") and not server_target_rates(propose):
        # A plan-less confirmation names nothing to agree to. Mark it so the decision prompt asks
        # for a concrete value instead of following the "accept the server's alternative" rule.
        propose = propose.model_copy(
            update={"parameters": {"pureConfirmation": True}}
        )
    session = NegotiationContext(
        id=context_id,
        round=round_number,
        max_rounds=max_rounds,
        performative=NegotiationPerformative.PROPOSE,
    )
    return propose, session


async def _answer_for(
    event: dict[str, object],
    answer: Callable[[dict[str, object]], str | Awaitable[str]] | None,
) -> str:
    """Return the follow-up text for one negotiation event (sync or async callback)."""
    if answer is None:
        proposal = pure_metadata(event).get("proposal")
        return proposal if isinstance(proposal, str) else ""
    result = answer(event)
    if inspect.isawaitable(result):
        result = await result
    return result if isinstance(result, str) else ""


def _emit_flow_note(
    trace_sink: Callable[[dict[str, object]], None] | None,
    *,
    kind: str,
    title: str,
    round_number: int | None = None,
    context_id: str | None = None,
    detail: Mapping[str, object] | None = None,
) -> None:
    """Record one client-side flow note so the drawer can explain an unfinished run."""
    if trace_sink is None:
        return
    recorder = TraceRecorder(current_trace().run_id or "", id_prefix="flow-note")
    span = recorder.emit(
        kind=kind,
        phase=PHASE_NEGOTIATION,
        title=title,
        actor="client",
        side="client",
        direction="internal",
        round=round_number,
        context_id=context_id,
        detail=detail,
    )
    trace_sink(span.to_dict())


def _emit_message_sent(
    request: SendMessageRequest,
    text: str,
    trace_sink: Callable[[dict[str, object]], None] | None,
) -> None:
    """Emit the outbound pure message so the wire inspector has it."""
    if trace_sink is None:
        return
    # The engineer client and the OSS Agent both send pure messages for one run. Their span ids
    # must not collide, otherwise the UI deduplicates the OSS->NMS row against the client row and
    # the downstream hop disappears from the trace.
    trace = current_trace()
    recorder = TraceRecorder(trace.run_id or "", id_prefix=f"flow-{trace.role or 'client'}")
    span = recorder.emit(
        kind="message_sent",
        phase=PHASE_TASK_PROMPT,
        title="Pure A2A message sent",
        actor="client",
        side="client",
        direction="client->server",
        detail={
            "protocol": "pure",
            "performative": "MESSAGE",
            "summary": text,
            # Who sent this hop: the engineer client (UI -> OSS) or the OSS Agent (OSS -> downstream).
            "role": trace.role or "",
            "metadataKeys": sorted(request.message.metadata.keys()),
            "templateUri": None,
            "extensionUri": None,
            "messageId": request.message.message_id,
            "taskId": request.message.task_id or None,
        },
    )
    trace_sink(span.to_dict())


def _context_id_of(events: list[dict[str, object]]) -> str | None:
    """Return the A2A context id carried by the first event that has one."""
    for event in events:
        context_id = event.get("context_id")
        if isinstance(context_id, str) and context_id:
            return context_id
    return None


def _text(value: object) -> str | None:
    """Return a non-empty string value, or ``None``."""
    return value if isinstance(value, str) and value else None


__all__ = [
    "DEFAULT_PURE_LABEL",
    "build_follow_up_request",
    "continue_pure_client_flow",
    "is_plain_question",
    "pure_metadata",
    "pure_negotiation_kind",
    "pure_proposal_view",
    "run_pure_client_flow",
]
