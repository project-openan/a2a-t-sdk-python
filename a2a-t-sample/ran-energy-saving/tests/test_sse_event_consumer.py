"""Normalized A2A events keep the extension payloads the presentation renders.

The wire inspector must be able to show the structured Task-T that created a task and the
Negotiation-T / Task-T metadata of later status and artifact updates. The normalizer is the only
place where the protobuf payload is still available, so these tests pin that it does not drop it.
"""

from __future__ import annotations

from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.types import (
    Artifact,
    Message,
    Role,
    StreamResponse,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a_t.core.metadata import TASK_T_EXTENSION_URI, TEMPLATE_URI_METADATA_KEY
from shared.sse_event_consumer import normalize_event

TASK_T_TEXT = "## Operation Type\n\nCreate\n\n## Task Object\n\nThe area is Region A."


def _user_message() -> Message:
    message = Message(role=Role.ROLE_USER, message_id="message-1", context_id="context-1")
    message.parts.add(text="create ran energy saving task")
    message.metadata[TASK_T_EXTENSION_URI] = TASK_T_TEXT
    message.metadata[TEMPLATE_URI_METADATA_KEY] = "urn:task-t-template"
    return message


def test_task_creation_event_keeps_the_task_history_with_its_task_t_metadata() -> None:
    task = new_task_from_user_message(_user_message())
    task.id = "task-1"

    event = normalize_event(StreamResponse(task=task))

    assert event["kind"] == "status"
    assert event["state"] == "TASK_STATE_SUBMITTED"
    task_payload = event["task"]
    assert isinstance(task_payload, dict)
    assert task_payload["id"] == "task-1"
    history = task_payload["history"]
    assert isinstance(history, list) and len(history) == 1
    assert history[0]["metadata"][TASK_T_EXTENSION_URI] == TASK_T_TEXT


def test_status_update_keeps_the_event_metadata_and_the_status_message() -> None:
    message = Message(role=Role.ROLE_AGENT, message_id="message-2")
    message.parts.add(text="Region: Region A; Progress: the report is ready.")

    status = TaskStatus(state=TaskState.TASK_STATE_WORKING)
    status.message.CopyFrom(message)
    response = StreamResponse(
        status_update=TaskStatusUpdateEvent(
            task_id="task-1",
            context_id="context-1",
            status=status,
            metadata={
                TASK_T_EXTENSION_URI: "1. Basic information\n- Region: Region A",
                TEMPLATE_URI_METADATA_KEY: "urn:task-t-template",
            },
        )
    )

    event = normalize_event(response)

    assert event["kind"] == "status"
    assert event["text"] == "Region: Region A; Progress: the report is ready."
    metadata = event["metadata"]
    assert isinstance(metadata, dict)
    assert metadata[TASK_T_EXTENSION_URI] == "1. Basic information\n- Region: Region A"


def test_artifact_update_keeps_the_event_metadata() -> None:
    artifact = Artifact(artifact_id="artifact-1", name="energy saving intent report")
    artifact.parts.add(text="report")
    response = StreamResponse(
        artifact_update=TaskArtifactUpdateEvent(
            task_id="task-1",
            context_id="context-1",
            artifact=artifact,
            metadata={TASK_T_EXTENSION_URI: "report metadata"},
        )
    )

    event = normalize_event(response)

    assert event["kind"] == "artifact"
    metadata = event["metadata"]
    assert isinstance(metadata, dict)
    assert metadata[TASK_T_EXTENSION_URI] == "report metadata"
