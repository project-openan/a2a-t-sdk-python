"""Adapter helpers for building A2A protobuf messages (artifacts, status updates, messages)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

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
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Value


def build_status_message(*, context_id: str, task_id: str, text: str) -> Message:
    message = Message(
        context_id=context_id,
        task_id=task_id,
        role=Role.ROLE_AGENT,
    )
    message.parts.add(text=text)
    return message


def build_status(*, context_id: str, task_id: str, state: TaskState, text: str) -> TaskStatus:
    return TaskStatus(
        state=state,
        message=build_status_message(context_id=context_id, task_id=task_id, text=text),
    )


def build_artifact(*, artifact_data: Mapping[str, object]) -> Artifact:
    """Build an A2A Artifact with a DataPart containing the given JSON data."""
    artifact = Artifact(
        artifact_id=str(uuid.uuid4()),
        name="faultManagement.Incident",
    )
    artifact.parts.add(data=ParseDict(dict(artifact_data), Value()))
    return artifact


async def emit_status_update(
    *,
    request_context: RequestContext,
    event_queue: EventQueue,
    context_id: str,
    task_id: str,
    state: TaskState,
    text: str,
) -> None:
    """Build and enqueue a TaskStatusUpdateEvent, also updating the request context's current_task."""
    status = build_status(
        context_id=context_id,
        task_id=task_id,
        state=state,
        text=text,
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
