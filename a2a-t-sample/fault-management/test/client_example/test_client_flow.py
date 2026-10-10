from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
TEST_ROOT = PROJECT_ROOT / "test"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(TEST_ROOT) not in sys.path:
    sys.path.append(str(TEST_ROOT))

from a2a.types import (
    Role,
    StreamResponse,
    TaskState,
)
from client_example.client_flow import (
    _NOTIFICATION_T_EXTENSION_URI,
    run_client_flow,
)
from client_example.scenario_data import (
    NATURAL_LANGUAGE_PROMPT_INPUT_EN,
    NATURAL_LANGUAGE_PROMPT_INPUT_ZH,
)
from support import FakePromptClient


class FakeStreamA2AClient:
    def __init__(self, *, events: list[StreamResponse]) -> None:
        self._events = list(events)
        self.send_calls: list[object] = []

    async def send_message(self, request: object, *, context: object = None):
        self.send_calls.append((request, context))
        for event in self._events:
            yield event


def _make_status_event(state: TaskState, text: str, task_id: str = "task-1") -> StreamResponse:
    response = StreamResponse()
    response.status_update.task_id = task_id
    response.status_update.status.state = state
    response.status_update.status.message.parts.add().text = text
    return response


def _make_artifact_event(task_id: str = "task-1") -> StreamResponse:
    response = StreamResponse()
    response.artifact_update.task_id = task_id
    response.artifact_update.artifact.artifact_id = "art-1"
    response.artifact_update.artifact.name = "faultManagement.Incident"
    response.artifact_update.last_chunk = True
    return response


def _make_message_event(text: str) -> StreamResponse:
    response = StreamResponse()
    response.message.message_id = "msg-1"
    response.message.role = Role.ROLE_AGENT
    response.message.parts.add().text = text
    return response


def _scenario_input() -> dict[str, object]:
    return {"scenario": "create incident subscription"}


class RunClientFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_consumes_stream_events_and_returns_normalized(self) -> None:
        events = [
            _make_status_event(TaskState.TASK_STATE_SUBMITTED, "submitted"),
            _make_status_event(TaskState.TASK_STATE_WORKING, "working"),
            _make_artifact_event(),
            _make_artifact_event(),
            _make_artifact_event(),
            _make_status_event(TaskState.TASK_STATE_COMPLETED, "done"),
        ]
        a2a_client = FakeStreamA2AClient(events=events)
        prompt_client = FakePromptClient(prompt_text="generated prompt")

        results = await run_client_flow(
            prompt_client=prompt_client,
            a2a_client=a2a_client,
            initial_input=_scenario_input(),
            max_artifacts=3,
        )

        self.assertEqual(len(results), 6)
        self.assertEqual(results[0]["kind"], "status")
        self.assertEqual(results[2]["kind"], "artifact")
        self.assertEqual(results[5]["kind"], "status")
        self.assertEqual(len(a2a_client.send_calls), 1)
        self.assertEqual(len(prompt_client.generate_calls), 1)

    async def test_generate_task_prompt_uses_hardcoded_nl_input(self) -> None:
        """The NL input fed to prompt generation must not depend on the developer's local .env."""
        for language, expected_input in (
            ("zh-CN", NATURAL_LANGUAGE_PROMPT_INPUT_ZH),
            ("en-US", NATURAL_LANGUAGE_PROMPT_INPUT_EN),
        ):
            with self.subTest(language=language):
                a2a_client = FakeStreamA2AClient(events=[])
                prompt_client = FakePromptClient(prompt_text="generated prompt")

                with mock.patch("client_example.scenario_data.resolve_language", return_value=language):
                    await run_client_flow(
                        prompt_client=prompt_client,
                        a2a_client=a2a_client,
                        initial_input=_scenario_input(),
                    )

                self.assertEqual(prompt_client.generate_calls, [expected_input])

    async def test_request_body_aligns_with_java_convention(self) -> None:
        """Text part = scenario name, metadata[Notification-T URI] = prompt text, header = Notification-T URI."""
        a2a_client = FakeStreamA2AClient(events=[])
        prompt_client = FakePromptClient(prompt_text="generated prompt")

        await run_client_flow(
            prompt_client=prompt_client,
            a2a_client=a2a_client,
            initial_input=_scenario_input(),
        )

        request, context = a2a_client.send_calls[0]
        message = request.message
        self.assertEqual(message.parts[0].text, "create incident subscription")
        self.assertEqual(
            dict(message.metadata)[_NOTIFICATION_T_EXTENSION_URI],
            "generated prompt",
        )
        self.assertEqual(
            context.service_parameters["A2A-Extensions"],
            _NOTIFICATION_T_EXTENSION_URI,
        )

    async def test_max_artifacts_stops_after_n_artifacts(self) -> None:
        events = [
            _make_artifact_event(),
            _make_artifact_event(),
            _make_artifact_event(),
            _make_artifact_event(),
            _make_artifact_event(),
        ]
        a2a_client = FakeStreamA2AClient(events=events)
        prompt_client = FakePromptClient()

        results = await run_client_flow(
            prompt_client=prompt_client,
            a2a_client=a2a_client,
            initial_input=_scenario_input(),
            max_artifacts=2,
        )

        artifact_count = sum(1 for r in results if r["kind"] == "artifact")
        self.assertEqual(artifact_count, 2)

    async def test_no_max_artifacts_consumes_all(self) -> None:
        events = [
            _make_artifact_event(),
            _make_artifact_event(),
            _make_message_event("done"),
        ]
        a2a_client = FakeStreamA2AClient(events=events)
        prompt_client = FakePromptClient()

        results = await run_client_flow(
            prompt_client=prompt_client,
            a2a_client=a2a_client,
            initial_input=_scenario_input(),
        )

        self.assertEqual(len(results), 3)


if __name__ == "__main__":
    unittest.main()
