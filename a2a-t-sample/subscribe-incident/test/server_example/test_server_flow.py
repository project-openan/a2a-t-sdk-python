from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
TEST_ROOT = PROJECT_ROOT / "test"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(TEST_ROOT) not in sys.path:
    sys.path.append(str(TEST_ROOT))

from a2a.server.agent_execution.context import RequestContext
from a2a.types import Message, Role, Task, TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent
from server_example.constants_data import NOTIFICATION_T_EXTENSION_URI
from server_example.server_flow import execute_server_flow
from support import FakeEventQueue, FakePromptServer


def _make_request_context(prompt_text: str = "test prompt") -> RequestContext:
    message = Message()
    message.message_id = "msg-1"
    message.role = Role.ROLE_USER
    message.parts.add().text = "create incident subscription"
    message.metadata[NOTIFICATION_T_EXTENSION_URI] = prompt_text

    class FakeCallContext:
        requested_extensions = {NOTIFICATION_T_EXTENSION_URI}
        state: dict = {}

    class FakeRequestContext:
        def __init__(self, msg: object) -> None:
            self.message = msg
            self.current_task = None
            self.task_id = "task-1"
            self.context_id = "ctx-1"
            self.call_context = FakeCallContext()

    return FakeRequestContext(message)


class ExecuteServerFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_valid_prompt_pushes_artifacts_and_no_completed(self) -> None:
        prompt_server = FakePromptServer(check_success=True)
        event_queue = FakeEventQueue()
        request_context = _make_request_context("complete prompt")

        async def fake_sleep(seconds: float) -> None:
            pass

        await execute_server_flow(
            request_context=request_context,
            event_queue=event_queue,
            prompt_server=prompt_server,
            max_artifacts=3,
            sleep_fn=fake_sleep,
        )

        artifact_count = sum(1 for e in event_queue.events if isinstance(e, TaskArtifactUpdateEvent))
        task_events = [e for e in event_queue.events if isinstance(e, Task)]
        status_update_events = [e for e in event_queue.events if isinstance(e, TaskStatusUpdateEvent)]

        self.assertEqual(artifact_count, 3)
        self.assertEqual(len(task_events), 1)  # SUBMITTED
        self.assertEqual(task_events[0].status.state, TaskState.TASK_STATE_SUBMITTED)
        self.assertEqual(len(status_update_events), 1)  # WORKING
        self.assertEqual(status_update_events[0].status.state, TaskState.TASK_STATE_WORKING)

    async def test_prompt_check_failure_emits_rejected(self) -> None:
        prompt_server = FakePromptServer(check_success=False)
        event_queue = FakeEventQueue()
        request_context = _make_request_context("bad prompt")

        await execute_server_flow(
            request_context=request_context,
            event_queue=event_queue,
            prompt_server=prompt_server,
            max_artifacts=1,
        )

        status_events = [e for e in event_queue.events if isinstance(e, TaskStatusUpdateEvent)]
        rejected = [e for e in status_events if e.status.state == TaskState.TASK_STATE_REJECTED]
        self.assertEqual(len(rejected), 1)
        self.assertIn("Prompt validation failed", rejected[0].status.message.parts[0].text)
        self.assertEqual(
            sum(1 for e in event_queue.events if isinstance(e, TaskArtifactUpdateEvent)),
            0,
        )

    async def test_missing_extension_header_raises(self) -> None:
        prompt_server = FakePromptServer(check_success=True)
        event_queue = FakeEventQueue()

        message = Message()
        message.message_id = "msg-1"
        message.role = Role.ROLE_USER
        message.parts.add().text = "scenario"
        message.metadata[NOTIFICATION_T_EXTENSION_URI] = "prompt"

        class EmptyCallContext:
            requested_extensions = set()
            state: dict = {}

        class NoHeaderRequestContext:
            def __init__(self, msg: object) -> None:
                self.message = msg
                self.current_task = None
                self.task_id = "task-1"
                self.context_id = "ctx-1"
                self.call_context = EmptyCallContext()

        with self.assertRaises(ValueError):
            await execute_server_flow(
                request_context=NoHeaderRequestContext(message),
                event_queue=event_queue,
                prompt_server=prompt_server,
                max_artifacts=1,
            )
        self.assertEqual(event_queue.events, [])

    async def test_prompt_extracted_from_metadata(self) -> None:
        prompt_server = FakePromptServer(check_success=True)
        event_queue = FakeEventQueue()
        request_context = _make_request_context("metadata prompt")

        async def fake_sleep(seconds: float) -> None:
            pass

        await execute_server_flow(
            request_context=request_context,
            event_queue=event_queue,
            prompt_server=prompt_server,
            max_artifacts=1,
            sleep_fn=fake_sleep,
        )

        self.assertEqual(prompt_server.check_calls[0]["processed_prompt_text"], "metadata prompt")

    async def test_max_artifacts_none_means_infinite_but_test_cuts_short(self) -> None:
        prompt_server = FakePromptServer(check_success=True)
        event_queue = FakeEventQueue()
        request_context = _make_request_context("complete prompt")

        call_count = 0

        async def counting_sleep(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 5:
                raise InterruptedError("stop")

        with self.assertRaises(InterruptedError):
            await execute_server_flow(
                request_context=request_context,
                event_queue=event_queue,
                prompt_server=prompt_server,
                max_artifacts=None,
                sleep_fn=counting_sleep,
            )

        artifact_count = sum(1 for e in event_queue.events if isinstance(e, TaskArtifactUpdateEvent))
        self.assertGreaterEqual(artifact_count, 4)
        status_events = [e for e in event_queue.events if isinstance(e, TaskStatusUpdateEvent)]
        self.assertIn(
            TaskState.TASK_STATE_FAILED,
            [e.status.state for e in status_events],
        )


if __name__ == "__main__":
    unittest.main()
