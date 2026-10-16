"""Adapter helpers for building A2A protobuf messages (artifacts, status updates, messages)."""

from __future__ import annotations

import uuid

from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    Artifact,
    Message,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)


def build_status_message(*, context_id: str, task_id: str, text: str) -> Message:
    message = Message(
        context_id=context_id,
        task_id=task_id,
        role=Role.ROLE_AGENT,
    )
    message.parts.add(text=text)
    return message


def build_status(
    *,
    context_id: str,
    task_id: str,
    state: TaskState,
    text: str,
    metadata: dict[str, object] | None = None,
) -> TaskStatus:
    """Build one TaskStatus whose message carries the text part and the optional metadata map."""
    status = TaskStatus(
        state=state,
        message=build_status_message(context_id=context_id, task_id=task_id, text=text),
    )
    if metadata:
        for key, value in metadata.items():
            status.message.metadata[key] = value
    return status


def build_metadata_artifact(
    *,
    name: str,
    text: str,
    extension_uri: str,
    metadata_text: str,
) -> Artifact:
    """Build an A2A Artifact with a TextPart label and an A2A-T metadata body.

    The A2A-T energy-saving intent report is carried in ``artifact.metadata[extension_uri]``; the
    artifact parts only carry a label text.
    """
    artifact = Artifact(
        artifact_id=str(uuid.uuid4()),
        name=name,
    )
    artifact.parts.add(text=text)
    artifact.metadata[extension_uri] = metadata_text
    return artifact


async def emit_status_update(
    *,
    request_context: RequestContext,
    event_queue: EventQueue,
    context_id: str,
    task_id: str,
    state: TaskState,
    text: str,
    metadata: dict[str, object] | None = None,
) -> None:
    """Build and enqueue a TaskStatusUpdateEvent, also updating the request context's current_task."""
    status = build_status(
        context_id=context_id,
        task_id=task_id,
        state=state,
        text=text,
        metadata=metadata,
    )
    current_task = Task()
    if request_context.current_task is not None:
        current_task.CopyFrom(request_context.current_task)
    current_task.id = task_id
    current_task.context_id = context_id
    current_task.status.CopyFrom(status)
    request_context.current_task = current_task
    await event_queue.enqueue_event(
        TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=status,
        )
    )
