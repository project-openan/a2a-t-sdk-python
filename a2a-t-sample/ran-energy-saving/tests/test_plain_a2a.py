"""Tests of the plain-A2A entry and its dispatch against the A2A-T gate."""

from __future__ import annotations

from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext, UnauthenticatedUser
from a2a.types import Message, Role, SendMessageRequest, TaskState
from a2a_t.core.metadata import TASK_T_EXTENSION_URI
from protocol.plain_a2a import (
    EMPTY_REQUEST_TEXT,
    MISSING_TARGET_TEXT,
    PlainTaskHandler,
    ProtocolDispatchHandler,
    is_plain_request,
)

#: Complete plain request: carries a time-segmented target, so it can be planned.
COMPLETE_REQUEST = (
    "Create a RAN energy saving task in Region A. The guaranteed throughput target is "
    "{00:00~06:00,10Mbps}."
)

#: The missing-parameter request: no target segment.
MISSING_REQUEST = "Create a RAN energy saving task in Region A to save energy."

#: Complete plain request written in the notation the English slot overlay and the vendor use.
VENDOR_NOTATION_REQUEST = (
    "Create a RAN energy saving task in Region A. The guaranteed throughput target is "
    "{00:00-06:00: 10Mbps}."
)


def _context(
    text: str | None,
    *,
    extensions: tuple[str, ...] = (),
    metadata: dict[str, str] | None = None,
) -> RequestContext:
    """Build one inbound A2A request context (no current task)."""
    message = Message(message_id="plain-1", role=Role.ROLE_USER, context_id="ctx-plain")
    if text is not None:
        message.parts.add(text=text)
    for key, value in (metadata or {}).items():
        message.metadata[key] = value  # ty: ignore[invalid-assignment]
    call_context = ServerCallContext(
        state={},
        user=UnauthenticatedUser(),
        requested_extensions=set(extensions),
    )
    return RequestContext(
        call_context=call_context,
        request=SendMessageRequest(message=message),
        task=None,
    )


async def test_a_complete_plain_request_is_handed_to_the_executor() -> None:
    handler = PlainTaskHandler()
    context = _context(COMPLETE_REQUEST)

    outcome = await handler.handle(context)

    assert outcome.short_circuit is False
    assert outcome.task_request is context


async def test_a_plain_request_with_the_vendor_target_notation_is_accepted() -> None:
    """``{00:00-06:00: 10Mbps}`` is a target; the plain gate must not reject it as missing."""
    handler = PlainTaskHandler()

    outcome = await handler.handle(_context(VENDOR_NOTATION_REQUEST))

    assert outcome.short_circuit is False
    assert outcome.task_request is not None


async def test_a_plain_request_without_a_target_is_rejected() -> None:
    """Plain A2A has no template and no negotiation: the sample refuses to guess the target."""
    handler = PlainTaskHandler()

    outcome = await handler.handle(_context(MISSING_REQUEST))

    assert outcome.short_circuit is True
    assert outcome.terminal_task is not None
    assert outcome.terminal_task.status.state == TaskState.TASK_STATE_REJECTED
    status_message = outcome.terminal_task.status.message
    assert status_message is not None
    assert status_message.parts[0].text == MISSING_TARGET_TEXT


async def test_a_plain_request_without_text_is_rejected() -> None:
    handler = PlainTaskHandler()

    outcome = await handler.handle(_context(None))

    assert outcome.short_circuit is True
    assert outcome.message is not None
    assert outcome.message.parts[0].text == EMPTY_REQUEST_TEXT


async def test_dispatch_routes_task_t_requests_to_the_a2a_t_gate() -> None:
    calls: list[str] = []

    class _Gate:
        async def handle(self, context: RequestContext) -> object:
            del context
            calls.append("a2a-t")
            return object()

    class _Plain(PlainTaskHandler):
        async def handle(self, context: RequestContext) -> object:
            del context
            calls.append("plain")
            return object()

    dispatch = ProtocolDispatchHandler(_Gate(), _Plain())
    await dispatch.handle(_context(COMPLETE_REQUEST, extensions=(TASK_T_EXTENSION_URI,)))
    assert calls == ["a2a-t"]

    calls.clear()
    await dispatch.handle(_context(COMPLETE_REQUEST))
    assert calls == ["plain"]


def test_is_plain_request_uses_the_extension_declaration_or_metadata() -> None:
    assert is_plain_request(_context(COMPLETE_REQUEST)) is True
    assert is_plain_request(_context(COMPLETE_REQUEST, extensions=(TASK_T_EXTENSION_URI,))) is False
    assert (
        is_plain_request(_context(COMPLETE_REQUEST, metadata={TASK_T_EXTENSION_URI: "prompt"}))
        is False
    )
