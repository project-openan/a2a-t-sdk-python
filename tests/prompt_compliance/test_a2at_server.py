from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.negotiation.common.enums import NegotiationStatus, NegotiationType
from a2a_t.negotiation.common.models import ContinueNegotiationInput, NegotiationContext, StartNegotiationInput
from a2a_t.server.prompt_compliance.result import PromptComplianceResult
from tests.test_support import ManagedTempDirTestCase, TEST_ENV_PATH


class FakePromptComplianceOrchestrator:
    def __init__(self, result: PromptComplianceResult) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    def check(self, *, processed_prompt_text: str, request_metadata: dict[str, object] | None) -> PromptComplianceResult:
        self.calls.append(
            {
                "processed_prompt_text": processed_prompt_text,
                "request_metadata": request_metadata,
            }
        )
        return self._result


class FakePromptComplianceBuilder:
    def __init__(self, orchestrator: FakePromptComplianceOrchestrator) -> None:
        self._orchestrator = orchestrator
        self.calls: list[dict[str, object]] = []

    def build(self, **kwargs: object) -> FakePromptComplianceOrchestrator:
        self.calls.append(dict(kwargs))
        return self._orchestrator


class FakeNegotiationOrchestrator:
    def __init__(self) -> None:
        self.start_calls: list[object] = []
        self.receive_calls: list[dict[str, object]] = []
        self.continue_calls: list[object] = []

    def start_negotiation(self, input: StartNegotiationInput) -> dict[str, object]:
        self.start_calls.append(input)
        return {"started": True}

    def receive_negotiation(self, message: str, context: dict[str, object]) -> dict[str, object]:
        self.receive_calls.append({"message": message, "context": context})
        return {"received": True}

    def continue_negotiation(self, input: ContinueNegotiationInput) -> dict[str, object]:
        self.continue_calls.append(input)
        return {"continued": True}


class A2ATServerTest(unittest.TestCase):
    def test_a2at_server_delegates_all_public_methods_with_typed_negotiation_inputs(self) -> None:
        from a2a_t.server.a2at_server import A2ATServer

        compliance_result = PromptComplianceResult(
            success=False,
            failure={
                "code": "slot_validation_error",
                "message": "Site format is invalid.",
                "stage": "slot_validation",
            },
        )
        compliance = FakePromptComplianceOrchestrator(compliance_result)
        compliance_builder = FakePromptComplianceBuilder(compliance)
        negotiation = FakeNegotiationOrchestrator()
        start_input = StartNegotiationInput(
            type=NegotiationType.INFORMATION,
            content_text="Need more information.",
            facts={},
        )
        continue_input = ContinueNegotiationInput(
            context=NegotiationContext.from_context(
                {
                    "negotiationType": "information",
                    "negotiationId": "neg-1",
                    "role": "server",
                    "round": 1,
                    "status": "in-progress",
                    "extra": {},
                }
            ),
            status=NegotiationStatus.IN_PROGRESS,
            content_text="Need the site name.",
        )

        with (
            patch("a2a_t.server.a2at_server._default_env_path", return_value=TEST_ENV_PATH),
            patch("a2a_t.server.a2at_server.PromptComplianceOrchestratorBuilder", return_value=compliance_builder),
            patch("a2a_t.server.a2at_server.ServerNegotiationOrchestratorBuilder") as negotiation_builder_cls,
            patch("a2a_t.server.a2at_server.LLMClient", return_value=object()),
        ):
            negotiation_builder_cls.return_value.build.return_value = negotiation
            server = A2ATServer()

            self.assertEqual(
                server.check_task_prompt(processed_prompt_text="prompt"),
                {
                    "success": False,
                    "failure": {
                        "code": "slot_validation_error",
                        "message": "Site format is invalid.",
                        "stage": "slot_validation",
                    },
                },
            )
            self.assertEqual(server.start_negotiation(start_input), {"started": True})
            self.assertEqual(
                server.receive_negotiation(
                    "Need more information",
                    {
                        "negotiationType": "information",
                        "negotiationId": "neg-1",
                        "role": "server",
                        "round": 1,
                        "status": "in-progress",
                        "extra": {},
                    },
                ),
                {"received": True},
            )
            self.assertEqual(server.continue_negotiation(continue_input), {"continued": True})

        self.assertEqual(
            compliance.calls,
            [
                {
                    "processed_prompt_text": "prompt",
                    "request_metadata": None,
                }
            ],
        )
        self.assertEqual(negotiation.start_calls, [start_input])
        self.assertEqual(negotiation.receive_calls[0]["message"], "Need more information")
        self.assertEqual(negotiation.continue_calls, [continue_input])

    def test_check_task_prompt_returns_success_only_when_compliance_passes(self) -> None:
        from a2a_t.server.a2at_server import A2ATServer

        compliance = FakePromptComplianceOrchestrator(PromptComplianceResult(success=True))
        compliance_builder = FakePromptComplianceBuilder(compliance)
        negotiation = FakeNegotiationOrchestrator()

        with (
            patch("a2a_t.server.a2at_server._default_env_path", return_value=TEST_ENV_PATH),
            patch("a2a_t.server.a2at_server.PromptComplianceOrchestratorBuilder", return_value=compliance_builder),
            patch("a2a_t.server.a2at_server.ServerNegotiationOrchestratorBuilder") as negotiation_builder_cls,
            patch("a2a_t.server.a2at_server.LLMClient", return_value=object()),
        ):
            negotiation_builder_cls.return_value.build.return_value = negotiation
            server = A2ATServer()

            self.assertEqual(
                server.check_task_prompt(processed_prompt_text="prompt"),
                {
                    "success": True,
                },
            )

    def test_server_package_exports_a2at_server(self) -> None:
        import a2a_t.server as server_package

        self.assertTrue(hasattr(server_package, "A2ATServer"))


class A2ATServerPromptResourceTimingTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_temp_dir("a2at_server_prompt_timing")

    def _write_resource_file(self, relative_path: str, content: str) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _write_env(self) -> Path:
        env_path = self.root / "server.env"
        env_path.write_text(
            "\n".join(
                [
                    "A2AT_LANGUAGE=en-US",
                    "A2AT_PROMPT_SOURCE_TYPE=local_file",
                    f"A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR={self.root}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return env_path

    def test_check_task_prompt_still_fails_at_call_time_when_packaged_prompts_are_missing(self) -> None:
        from a2a_t.server.a2at_server import A2ATServer

        self._write_resource_file(
            "scenarios/en-US/scenarios.json",
            '{"scenarios":[{"scenario_code":"energy_saving","scenario_name":"Energy Saving","description":"Used for energy saving analysis.","example":"Analyze site power usage and suggest optimization."}]}',
        )
        env_path = self._write_env()
        missing_packaged_root = self.make_temp_dir("missing_packaged_prompts_server")

        with (
            patch("a2a_t.server.a2at_server.ServerNegotiationOrchestratorBuilder") as negotiation_builder_cls,
            patch("a2a_t.server.a2at_server.LLMClient", return_value=object()),
            patch("a2a_t.common.prompt_resources.local_resources.LocalPromptResourceFiles._default_root_dir", return_value=missing_packaged_root),
        ):
            negotiation_builder_cls.return_value.build.return_value = object()
            server = A2ATServer(env_path=env_path)

            result = server.check_task_prompt(processed_prompt_text="processed body")

        self.assertEqual(
            result,
            {
                "success": False,
                "failure": {
                    "code": "prompt_resource_load_error",
                    "message": "Prompt resource file does not exist.",
                    "stage": "generation",
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
