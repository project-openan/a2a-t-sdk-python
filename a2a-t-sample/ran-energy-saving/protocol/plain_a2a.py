"""Plain A2A entry of the sample's NMS agent.

One agent card serves both protocols. A request that declares the Task-T extension is handled by
:class:`~protocol.pre_task_negotiation.PreTaskNegotiationHandler`; a request that does not is served
here: the message text is the whole task request, the NMS planner interprets it, and the same
guarded tools execute the plan.

Plain A2A has no task template and no pre-task negotiation. The sample's planner policy is
therefore explicit: a request that carries no time-segmented guaranteed throughput target cannot be
planned, and it is rejected instead of being guessed. That is the business difference the demo
compares against A2A-T, where the same request opens an information negotiation and the value is
agreed before the task starts.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from a2a.server.agent_execution.context import RequestContext
from a2a.types import Message, Task, TaskState, TaskStatus
from a2a_t.core.metadata import TASK_T_EXTENSION_URI

from protocol.messages import build_agent_message, metadata_to_dict
from protocol.pre_task_negotiation import NegotiationOutcome
from protocol.target_grammar import find_segments

# Target segments are parsed by :mod:`protocol.target_grammar`, which reads every notation the
# sample and the vendor write (the local copy used to accept only ``HH:MM~HH:MM,<rate>Mbps``).

#: Terminal text of a plain request that carries no time-segmented guaranteed throughput target.
MISSING_TARGET_TEXT: Final[str] = (
    "The request does not define the time-segmented guaranteed throughput target. A plain A2A "
    "request carries no task template to validate and no pre-task negotiation to obtain the value, "
    "so the energy-saving task cannot be planned and was rejected."
)

#: Terminal text of a plain request that carries no text at all.
EMPTY_REQUEST_TEXT: Final[str] = "The plain A2A request carries no task description."

_LOGGER = logging.getLogger("ran_energy_saving.plain_a2a")


class PlainTaskHandler:
    """Serve one request that does not use the Task-T extension.

    The handler has the same ``handle`` contract as the A2A-T pre-task handler, so the executor
    shell does not need to know which protocol a request used.
    """

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        """Create one plain handler."""
        self._logger = logger or _LOGGER

    async def handle(self, context: RequestContext) -> NegotiationOutcome:
        """Run the plain entry: reject an unplannable request, otherwise hand it to the executor."""
        context_id = context.context_id or ""
        message = context.message
        if message is None:
            return NegotiationOutcome.short(
                build_agent_message(context_id=context_id, text=EMPTY_REQUEST_TEXT)
            )
        text = _message_text(message)
        if not text:
            return NegotiationOutcome.short(
                build_agent_message(context_id=context_id, text=EMPTY_REQUEST_TEXT)
            )
        if not find_segments(text):
            self._logger.info("plain request rejected: no guaranteed throughput target")
            return NegotiationOutcome.rejected(_build_rejected_task(context, MISSING_TARGET_TEXT))
        self._logger.info("plain request accepted; handing it to the deterministic executor")
        return NegotiationOutcome.execution(context)


class ProtocolDispatchHandler:
    """Route one inbound request to the A2A-T gate or the request form without the extension.

    The fallback handler is the plain entry for a single-protocol deployment, and the pure
    (standard A2A Task lifecycle) handler for the single-agent server that serves both forms.
    """

    def __init__(self, negotiation: Any, fallback: Any) -> None:
        """Create one dispatcher over the extension and fallback entries."""
        self._negotiation = negotiation
        self._plain = fallback

    async def handle(self, context: RequestContext) -> NegotiationOutcome:
        """Handle one request with the entry its protocol selects."""
        if is_plain_request(context):
            return await self._plain.handle(context)
        return await self._negotiation.handle(context)


def is_plain_request(context: RequestContext) -> bool:
    """Return whether one inbound request asks for the plain A2A path.

    The A2A-T client both declares the Task-T extension and carries the Task-T metadata; a plain
    client does neither. Either signal is enough to route the request to the A2A-T handler, so a
    client that declares the extension but forgets the metadata still gets the A2A-T error message.
    """
    declared = getattr(getattr(context, "call_context", None), "requested_extensions", None) or ()
    if any(str(uri) == TASK_T_EXTENSION_URI for uri in declared):
        return False
    message = context.message
    if message is not None and TASK_T_EXTENSION_URI in metadata_to_dict(message.metadata):
        return False
    return True


def _message_text(message: Message) -> str:
    """Return the first non-empty text part of one message."""
    for part in message.parts or ():
        text = getattr(part, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _build_rejected_task(context: RequestContext, text: str) -> Task:
    """Build the terminal ``REJECTED`` Task of an unplannable plain request."""
    context_id = context.context_id or ""
    task_id = context.task_id or ""
    status_message = build_agent_message(context_id=context_id, text=text)
    message = context.message
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(
            state=TaskState.TASK_STATE_REJECTED,
            message=status_message,
        ),
        history=[message] if message is not None else [],
    )


__all__ = [
    "EMPTY_REQUEST_TEXT",
    "MISSING_TARGET_TEXT",
    "PlainTaskHandler",
    "ProtocolDispatchHandler",
    "is_plain_request",
]
