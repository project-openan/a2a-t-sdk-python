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

from a2a.client.client import ClientCallContext
from a2a.types import Role, SendMessageRequest
from shared.config import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    PURE_FOLLOWUP_BOTH,
    PURE_FOLLOWUP_CONTEXT,
    PURE_FOLLOWUP_TASK,
)
from shared.trace import PHASE_TASK_PROMPT, TraceRecorder, current_trace

from protocol.client_flow import (
    ClientFlowResult,
    ClientTransport,
    _fill_result,
    _final_state,
)

_LOGGER = logging.getLogger("ran_energy_saving.pure_client_flow")

#: Short text part of the pure request (the task itself is in the same part).
DEFAULT_PURE_LABEL: str = "RAN energy saving task"


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

    The downstream correlation travels on the ``X-A2AT-Run-Id``/``traceparent`` headers (the
    propagation context), so the message itself carries no sample-local metadata.

    Returns:
        The collected events, task id and report.
    """
    transport_emits_events = False
    if event_sink is not None:
        set_event_sink = getattr(transport, "set_event_sink", None)
        if callable(set_event_sink):
            set_event_sink(event_sink)
            transport_emits_events = True

    result = ClientFlowResult()
    request = _pure_request(user_request)
    _emit_message_sent(request, user_request, trace_sink)
    batch = await transport.send(request, _call_context(call_timeout_seconds))
    result.events.extend(batch)
    if event_sink is not None and not transport_emits_events:
        for event in batch:
            event_sink(event)

    rounds = 0
    while rounds < max_rounds:
        pending = _pending_negotiation(batch)
        if pending is None:
            break
        text = await _answer_for(pending, answer)
        if not text.strip():
            break
        if on_answer is not None:
            await on_answer(pending, text)
        follow = _follow_up_request(
            text,
            task_id=_text(pending.get("task_id")),
            context_id=_text(pending.get("context_id")),
            followup=followup,
        )
        _emit_message_sent(follow, text, trace_sink)
        batch = await transport.send(follow, _call_context(call_timeout_seconds))
        result.events.extend(batch)
        if event_sink is not None and not transport_emits_events:
            for event in batch:
                event_sink(event)
        rounds += 1

    result.final_state = _final_state(result.events)
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
) -> dict[str, object] | None:
    """Return the newest ``INPUT_REQUIRED`` status the client must answer.

    The local sample tags a negotiation with its ``pure`` metadata; a real NMS returns a standard
    A2A status without that tag. An MIE ``approval`` phase belongs to the RAN Agent and is not
    answered by the OSS Agent.
    """
    for event in reversed(events):
        if event.get("kind") != "status":
            continue
        if event.get("state") != "TASK_STATE_INPUT_REQUIRED":
            continue
        pure = pure_metadata(event)
        if pure.get("kind") == "approval":
            continue
        if pure and pure.get("kind") not in (None, "negotiation"):
            continue
        return event
    return None


def pure_metadata(event: Mapping[str, object]) -> dict[str, object]:
    """Return the sample-local ``pure`` metadata of one normalized event."""
    metadata = event.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    raw = metadata.get("pure")
    return dict(raw) if isinstance(raw, Mapping) else {}


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
    recorder = TraceRecorder(
        trace.run_id or "", id_prefix=f"flow-{trace.role or 'client'}"
    )
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


__all__ = ["DEFAULT_PURE_LABEL", "pure_metadata", "run_pure_client_flow"]
