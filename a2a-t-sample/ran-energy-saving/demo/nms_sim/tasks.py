"""In-memory Task store of the NMS simulator.

The real NMS keeps the latest 50 Tasks and spells cancellation with one ``L``; both behaviors are
part of the contract the client must tolerate.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Final

#: Task states used by the simulator (the vendor spelling).
TASK_SUBMITTED: Final[str] = "TASK_STATE_SUBMITTED"
TASK_WORKING: Final[str] = "TASK_STATE_WORKING"
TASK_INPUT_REQUIRED: Final[str] = "TASK_STATE_INPUT_REQUIRED"
TASK_COMPLETED: Final[str] = "TASK_STATE_COMPLETED"
TASK_CANCELED: Final[str] = "TASK_STATE_CANCELED"
TASK_FAILED: Final[str] = "TASK_STATE_FAILED"
TASK_REJECTED: Final[str] = "TASK_STATE_REJECTED"

TERMINAL_STATES: Final[frozenset[str]] = frozenset({TASK_COMPLETED, TASK_CANCELED, TASK_FAILED, TASK_REJECTED})


@dataclass
class SimTask:
    """One simulated Task.

    Attributes:
        task_id: Task identifier.
        context_id: Conversation context identifier.
        state: Current Task state.
        message: Text of the status message.
        artifacts: Artifacts published on the Task.
        history: Task history entries.
    """

    task_id: str
    context_id: str
    state: str = TASK_SUBMITTED
    message: str = ""
    artifacts: list[dict[str, object]] = field(default_factory=list)
    history: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return the simulator's Task JSON document."""
        status: dict[str, object] = {"state": self.state}
        if self.message:
            status["message"] = {"parts": [{"text": self.message}]}
        return {
            "id": self.task_id,
            "contextId": self.context_id,
            "status": status,
            "artifacts": list(self.artifacts),
            "history": list(self.history),
        }

    def status_frame(self) -> dict[str, object]:
        """Return one ``statusUpdate`` frame of this Task."""
        status: dict[str, object] = {"state": self.state}
        if self.message:
            status["message"] = {"parts": [{"text": self.message}]}
        return {
            "statusUpdate": {
                "taskId": self.task_id,
                "contextId": self.context_id,
                "status": status,
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
            }
        }


class TaskStore:
    """Small in-memory store keeping the latest ``limit`` Tasks."""

    def __init__(self, limit: int = 50) -> None:
        """Create one store."""
        self._limit = max(1, limit)
        self._tasks: dict[str, SimTask] = {}

    def create(self, *, context_id: str, message: str = "") -> SimTask:
        """Create and remember a new Task."""
        task = SimTask(
            task_id=str(uuid.uuid4()),
            context_id=context_id,
            message=message,
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

    def update(self, task_id: str, *, state: str, message: str = "") -> SimTask | None:
        """Update the state and status message of one Task."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.state = state
        task.message = message
        task.history.append({"role": "ROLE_AGENT", "messageId": str(uuid.uuid4()), "parts": [{"text": message}]})
        return task

    def add_artifact(self, task_id: str, artifact: dict[str, object]) -> SimTask | None:
        """Append one artifact to a Task."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.artifacts.append(artifact)
        return task

    def list(self) -> list[dict[str, object]]:
        """Return the latest Tasks, newest first."""
        return [task.to_dict() for task in reversed(tuple(self._tasks.values()))]
