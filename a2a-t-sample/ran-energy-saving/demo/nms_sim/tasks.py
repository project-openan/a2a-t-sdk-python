"""In-memory Task store of the NMS simulator.

The real NMS keeps the latest 50 Tasks and spells cancellation with one ``L``; both behaviors are
part of the contract the client must tolerate. Task documents carry the vendor's millisecond
``timestamp``; event frames carry it inside their protobuf-safe ``metadata`` map instead, because
the A2A types have no top-level timestamp field.
"""

from __future__ import annotations

import builtins
import time
import uuid
from dataclasses import dataclass, field
from typing import Final

from demo.nms_sim.config import TASK_SHAPE_TOPLEVEL

#: Task states used by the simulator (the vendor spelling).
TASK_SUBMITTED: Final[str] = "TASK_STATE_SUBMITTED"
TASK_WORKING: Final[str] = "TASK_STATE_WORKING"
TASK_INPUT_REQUIRED: Final[str] = "TASK_STATE_INPUT_REQUIRED"
TASK_COMPLETED: Final[str] = "TASK_STATE_COMPLETED"
TASK_CANCELED: Final[str] = "TASK_STATE_CANCELED"
TASK_FAILED: Final[str] = "TASK_STATE_FAILED"
TASK_REJECTED: Final[str] = "TASK_STATE_REJECTED"

TERMINAL_STATES: Final[frozenset[str]] = frozenset({TASK_COMPLETED, TASK_CANCELED, TASK_FAILED, TASK_REJECTED})

#: Metadata key carrying the millisecond timestamp of an event frame.
TIMESTAMP_METADATA_KEY: Final[str] = "timestamp"


def now_ms() -> int:
    """Return the current epoch time in milliseconds."""
    return int(time.time() * 1000)


@dataclass
class SimTask:
    """One simulated Task.

    Attributes:
        task_id: Task identifier.
        context_id: Conversation context identifier.
        state: Current Task state.
        message: Text of the status message.
        message_metadata: Metadata carried by the status message (Negotiation-T payloads).
        artifacts: Artifacts published on the Task.
        history: Task history entries.
        timestamp_ms: Millisecond timestamp of the last update.
        shape: Task document shape (``standard`` or ``toplevel``).
    """

    task_id: str
    context_id: str
    state: str = TASK_SUBMITTED
    message: str = ""
    message_metadata: dict[str, object] = field(default_factory=dict)
    artifacts: list[dict[str, object]] = field(default_factory=list)
    history: list[dict[str, object]] = field(default_factory=list)
    timestamp_ms: int = field(default_factory=now_ms)
    shape: str = "standard"
    #: Pure A2A negotiation state: the accumulated request text and the round counter.
    pure_text: str = ""
    pure_round: int = 0
    pure_max_rounds: int = 3

    def touch(self) -> None:
        """Refresh the millisecond timestamp."""
        self.timestamp_ms = now_ms()

    def _status_document(self) -> dict[str, object]:
        """Return the nested ``status`` document."""
        status: dict[str, object] = {"state": self.state}
        if self.message:
            message: dict[str, object] = {"parts": [{"text": self.message}]}
            if self.message_metadata:
                message["metadata"] = dict(self.message_metadata)
            status["message"] = message
        return status

    def to_dict(self, *, embedded: bool = False) -> dict[str, object]:
        """Return the simulator's Task JSON document.

        Args:
            embedded: when true, omit the extra top-level fields so the document still parses as an
                A2A ``Task`` in a ``message:send`` reply or an SSE ``task`` frame.

        Returns:
            The Task document; the standalone form (``GET /tasks``) adds ``timestamp`` and, in
            ``toplevel`` shape, a top-level ``state``.
        """
        document: dict[str, object] = {
            "id": self.task_id,
            "contextId": self.context_id,
            "status": self._status_document(),
            "artifacts": list(self.artifacts),
            "history": list(self.history),
        }
        if embedded:
            # ``Task.metadata`` is a protobuf Struct, so the millisecond timestamp survives the
            # client's ParseDict while a top-level custom field would not.
            document["metadata"] = {TIMESTAMP_METADATA_KEY: self.timestamp_ms}
            return document
        document["timestamp"] = self.timestamp_ms
        if self.shape == TASK_SHAPE_TOPLEVEL:
            document["state"] = self.state
        return document

    def status_frame(self) -> dict[str, object]:
        """Return one ``statusUpdate`` frame of this Task."""
        return {
            "statusUpdate": {
                "taskId": self.task_id,
                "contextId": self.context_id,
                "status": self._status_document(),
                "metadata": {TIMESTAMP_METADATA_KEY: self.timestamp_ms},
            }
        }

    def artifact_frame(self) -> dict[str, object]:
        """Return one ``artifactUpdate`` frame carrying the latest artifact."""
        artifact = self.artifacts[-1]
        return {
            "artifactUpdate": {
                "taskId": self.task_id,
                "contextId": self.context_id,
                "artifact": artifact,
                "metadata": {TIMESTAMP_METADATA_KEY: self.timestamp_ms},
            }
        }


class TaskStore:
    """Small in-memory store keeping the latest ``limit`` Tasks."""

    def __init__(self, limit: int = 50, *, shape: str = "standard") -> None:
        """Create one store."""
        self._limit = max(1, limit)
        self._shape = shape
        self._tasks: dict[str, SimTask] = {}

    def create(self, *, context_id: str, message: str = "") -> SimTask:
        """Create and remember a new Task."""
        task = SimTask(
            task_id=str(uuid.uuid4()),
            context_id=context_id,
            message=message,
            shape=self._shape,
        )
        task.history.append({"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": message}]})
        self._tasks[task.task_id] = task
        while len(self._tasks) > self._limit:
            oldest = next(iter(self._tasks))
            self._tasks.pop(oldest, None)
        return task

    def get(self, task_id: str) -> SimTask | None:
        """Return one Task, or ``None``."""
        return self._tasks.get(task_id)

    def find_by_context(self, context_id: str) -> SimTask | None:
        """Return the newest Task of one context, or ``None``.

        A plain-A2A question is a standalone Message, so the caller's follow-up carries only the
        context id. The simulator still needs the accumulated text and round counter of the
        conversation, which live on the internal Task of that context.
        """
        for task in reversed(tuple(self._tasks.values())):
            if task.context_id == context_id:
                return task
        return None

    def update(
        self,
        task_id: str,
        *,
        state: str,
        message: str = "",
        metadata: dict[str, object] | None = None,
    ) -> SimTask | None:
        """Update the state and status message of one Task."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.state = state
        task.message = message
        task.message_metadata = dict(metadata or {})
        task.touch()
        task.history.append({"role": "ROLE_AGENT", "messageId": str(uuid.uuid4()), "parts": [{"text": message}]})
        return task

    def add_artifact(self, task_id: str, artifact: dict[str, object]) -> SimTask | None:
        """Append one artifact to a Task."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.artifacts.append(artifact)
        task.touch()
        return task

    def list(self) -> builtins.list[dict[str, object]]:
        """Return the latest Tasks, newest first."""
        return [task.to_dict() for task in reversed(tuple(self._tasks.values()))]
