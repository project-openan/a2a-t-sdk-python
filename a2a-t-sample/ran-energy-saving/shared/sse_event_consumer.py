"""Normalize A2A StreamResponse events into simple dicts for client-side consumption."""

from __future__ import annotations

from datetime import UTC, datetime

from a2a.types import StreamResponse, TaskState, TaskStatus
from google.protobuf.json_format import MessageToDict


def _status_message_metadata(status: TaskStatus) -> dict[str, object] | None:
    """Return the status message's metadata map, ``None`` when the message carries none."""
    if not status.HasField("message"):
        return None
    metadata = MessageToDict(status.message.metadata)
    return metadata or None


def _merge_metadata(*maps: dict[str, object] | None) -> dict[str, object] | None:
    """Merge metadata maps, dropping the empty ones; ``None`` when every map is empty."""
    merged: dict[str, object] = {}
    for item in maps:
        if item:
            merged.update(item)
    return merged or None


def normalize_event(stream_response: StreamResponse) -> dict[str, object]:
    """Convert a StreamResponse (status/message/artifact/task) into a flat dict with a 'kind' field."""
    if stream_response.HasField("status_update"):
        update = stream_response.status_update
        status = update.status
        state_name = TaskState.Name(status.state)
        text = ""
        if status.HasField("message") and status.message.parts:
            text = status.message.parts[0].text
        event: dict[str, object] = {
            "kind": "status",
            "task_id": update.task_id,
            "context_id": update.context_id,
            "state": state_name,
            "text": text,
        }
        # The vendor often reuses an older status snapshot. Keep its own timestamp so the client
        # can tell a late replay from a fresh transition instead of trusting arrival order.
        if status.HasField("timestamp") and (status.timestamp.seconds or status.timestamp.nanos):
            event["timestamp"] = (
                datetime.fromtimestamp(
                    status.timestamp.seconds + status.timestamp.nanos / 1e9,
                    UTC,
                )
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
        # A2A carries extension metadata on the event and on its status message. Keep both so a
        # status update can show the Task-T / Negotiation-T payload it actually carried.
        metadata = _merge_metadata(
            MessageToDict(update.metadata) or None,
            _status_message_metadata(status),
        )
        if metadata is not None:
            event["metadata"] = metadata
        return event

    if stream_response.HasField("message"):
        message = stream_response.message
        text = ""
        if message.parts:
            text = message.parts[0].text
        event: dict[str, object] = {
            "kind": "message",
            "text": text,
            "context_id": message.context_id,
            "message_id": message.message_id,
        }
        if message.task_id:
            # A message that continues a Task is an observation of that Task, not a free-standing
            # vendor question; dropping the id made the pump relay progress text as a question.
            event["task_id"] = message.task_id
        metadata = MessageToDict(message.metadata)
        if metadata:
            event["metadata"] = metadata
        return event

    if stream_response.HasField("artifact_update"):
        update = stream_response.artifact_update
        artifact = update.artifact
        event: dict[str, object] = {
            "kind": "artifact",
            "task_id": update.task_id,
            "artifact_id": artifact.artifact_id,
            "name": artifact.name,
        }
        if artifact.parts:
            part = artifact.parts[0]
            if part.HasField("data"):
                event["data"] = MessageToDict(part.data)
            elif part.HasField("text"):
                event["text"] = part.text
        metadata = _merge_metadata(
            MessageToDict(update.metadata) or None,
            MessageToDict(artifact.metadata) if artifact.HasField("metadata") else None,
        )
        if metadata is not None:
            event["metadata"] = metadata
        return event

    if stream_response.HasField("task"):
        task = stream_response.task
        state_name = ""
        text = ""
        message_metadata: dict[str, object] | None = None
        if task.HasField("status"):
            state_name = TaskState.Name(task.status.state)
            if task.status.HasField("message") and task.status.message.parts:
                text = task.status.message.parts[0].text
            message_metadata = _status_message_metadata(task.status)
        event: dict[str, object] = {
            "kind": "status",
            "task_id": task.id,
            "context_id": task.context_id,
            "state": state_name,
        }
        if text:
            event["text"] = text
        if message_metadata is not None:
            event["metadata"] = message_metadata
        # The Task envelope is itself the creation payload: its history carries the original user
        # message, including the Task-T that created the task. Keep it so the wire view can show
        # the accepted creation parameters instead of only the state name.
        task_payload = MessageToDict(task)
        if task_payload:
            event["task"] = task_payload
        return event

    raise ValueError("Unsupported StreamResponse: no recognized event field set")
