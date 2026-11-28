"""Plain A2A client flow: one natural-language request, no Task-T and no negotiation.

The flow exists for the protocol comparison in the demo. It sends the scenario's natural-language
request as the message text, declares no extension, and consumes the server's task events exactly
like any A2A client. There is no structured intent to approve, no proposal to answer and no
negotiation to run; the server either plans the request or rejects it.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from a2a.client.client import ClientCallContext
from a2a.types import Role, SendMessageRequest
from shared.config import DEFAULT_HTTP_TIMEOUT_SECONDS
from shared.trace import PHASE_TASK_PROMPT, TraceRecorder, current_trace

# The plain flow shares the result shape and the event-based report selection with the A2A-T flow;
# importing the same helpers keeps the two protocols' transcripts comparable.
from protocol.client_flow import (
    ClientFlowResult,
    ClientTransport,
    _fill_result,
    _final_state,
)

_LOGGER = logging.getLogger("ran_energy_saving.plain_client_flow")

#: Short text part of the plain request (the task itself is in the same part).
DEFAULT_PLAIN_LABEL: str = "RAN energy saving task"


async def run_plain_client_flow(
    *,
    transport: ClientTransport,
    user_request: str,
    label: str = DEFAULT_PLAIN_LABEL,
    call_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    event_sink: Callable[[dict[str, object]], None] | None = None,
    trace_sink: Callable[[dict[str, object]], None] | None = None,
) -> ClientFlowResult:
    """Send one plain A2A message and collect its task events.

    Args:
        transport: SSE or webhook transport (the demo uses SSE).
        user_request: Natural-language request; it is both the message text and the task.
        label: Unused short label kept for signature parity with the A2A-T flow.
        call_timeout_seconds: HTTP budget of the send.
        event_sink: Optional live observer of the normalized events (Web UI timeline).
        trace_sink: Optional observer of the synthetic ``message_sent`` span.

    Returns:
        The collected events, task id and report (no decisions: plain A2A has none).
    """
    del label
    transport_emits_events = False
    if event_sink is not None:
        set_event_sink = getattr(transport, "set_event_sink", None)
        if callable(set_event_sink):
            set_event_sink(event_sink)
            transport_emits_events = True

    request = _plain_request(user_request)
    _emit_message_sent(request, user_request, trace_sink)
    batch = await transport.send(
        request,
        ClientCallContext(
            timeout=call_timeout_seconds,
            service_parameters={"A2A-Extensions": ""},
        ),
    )
    if event_sink is not None and not transport_emits_events:
        for event in batch:
            event_sink(event)

    result = ClientFlowResult()
    result.events.extend(batch)
    result.final_state = _final_state(batch)
    _fill_result(result, _context_id_of(batch))
    _LOGGER.info("plain client flow finished: state=%s task_id=%s", result.final_state, result.task_id)
    return result


def _plain_request(text: str) -> SendMessageRequest:
    """Build one user-role A2A message without any extension metadata."""
    request = SendMessageRequest()
    request.message.message_id = str(uuid.uuid4())
    request.message.role = Role.ROLE_USER
    request.message.parts.add().text = text
    return request


def _emit_message_sent(
    request: SendMessageRequest,
    text: str,
    trace_sink: Callable[[dict[str, object]], None] | None,
) -> None:
    """Emit the outbound plain message so the wire inspector has it."""
    if trace_sink is None:
        return
    recorder = TraceRecorder(current_trace().run_id or "", id_prefix="flow")
    span = recorder.emit(
        kind="message_sent",
        phase=PHASE_TASK_PROMPT,
        title="Plain A2A message sent",
        actor="client",
        side="client",
        direction="client->server",
        detail={
            "protocol": "plain",
            "performative": "MESSAGE",
            "summary": text,
            "metadataKeys": [],
            "templateUri": None,
            "extensionUri": None,
            "messageId": request.message.message_id,
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


__all__ = ["DEFAULT_PLAIN_LABEL", "run_plain_client_flow"]
