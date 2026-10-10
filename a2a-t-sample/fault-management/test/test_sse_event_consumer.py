from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a.types import (
    Role,
    StreamResponse,
    TaskState,
)
from common.sse_event_consumer import normalize_event


class NormalizeStatusUpdateEventTest(unittest.TestCase):
    def test_normalizes_status_update(self) -> None:
        response = StreamResponse()
        response.status_update.task_id = "task-1"
        response.status_update.status.state = TaskState.TASK_STATE_WORKING
        response.status_update.status.message.parts.add().text = "working"

        result = normalize_event(response)

        self.assertEqual(result["kind"], "status")
        self.assertEqual(result["task_id"], "task-1")
        self.assertEqual(result["state"], "TASK_STATE_WORKING")


class NormalizeMessageEventTest(unittest.TestCase):
    def test_normalizes_message(self) -> None:
        response = StreamResponse()
        response.message.message_id = "msg-1"
        response.message.role = Role.ROLE_AGENT
        response.message.parts.add().text = "Subscription created"

        result = normalize_event(response)

        self.assertEqual(result["kind"], "message")
        self.assertEqual(result["text"], "Subscription created")


class NormalizeArtifactUpdateEventTest(unittest.TestCase):
    def test_normalizes_artifact_update(self) -> None:
        response = StreamResponse()
        response.artifact_update.task_id = "task-1"
        response.artifact_update.artifact.artifact_id = "art-1"
        response.artifact_update.artifact.name = "faultManagement.Incident"
        response.artifact_update.last_chunk = True

        result = normalize_event(response)

        self.assertEqual(result["kind"], "artifact")
        self.assertEqual(result["task_id"], "task-1")
        self.assertEqual(result["artifact_id"], "art-1")
        self.assertEqual(result["name"], "faultManagement.Incident")


class NormalizeTaskEventTest(unittest.TestCase):
    def test_normalizes_task_event(self) -> None:
        response = StreamResponse()
        response.task.id = "task-1"
        response.task.status.state = TaskState.TASK_STATE_SUBMITTED

        result = normalize_event(response)

        self.assertEqual(result["kind"], "status")
        self.assertEqual(result["state"], "TASK_STATE_SUBMITTED")


class NormalizeEmptyEventTest(unittest.TestCase):
    def test_empty_stream_response_raises(self) -> None:
        response = StreamResponse()
        with self.assertRaises(ValueError):
            normalize_event(response)


if __name__ == "__main__":
    unittest.main()
