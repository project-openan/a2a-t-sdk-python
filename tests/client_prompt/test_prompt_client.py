from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.client.prompt.models import PromptGenerationResult, ValidationResult
from tests.test_support import ManagedTempDirTestCase


class FakeOrchestrator:
    def __init__(self, result: PromptGenerationResult) -> None:
        self._result = result
        self.calls: list[object] = []

    def generate(self, user_input: object) -> PromptGenerationResult:
        self.calls.append(user_input)
        return self._result


class FakeOrchestratorBuilder:
    def __init__(self, orchestrator: FakeOrchestrator) -> None:
        self._orchestrator = orchestrator
        self.calls: list[dict[str, object]] = []

    def build(self, **kwargs: object) -> FakeOrchestrator:
        self.calls.append(dict(kwargs))
        return self._orchestrator


class PromptClientTest(ManagedTempDirTestCase):
    def test_generate_a2a_t_prompt_delegates_to_orchestrator(self) -> None:
        from a2a_t.client.prompt_client import PromptClient

        result = PromptGenerationResult(
            success=True,
            prompt_text="test prompt",
            scenario_code=None,
            language="en-US",
            input_kind="natural_language",
            slots={},
            validation=ValidationResult(passed=True, slot_errors=[]),
            failure=None,
        )
        orchestrator = FakeOrchestrator(result)
        client = PromptClient(orchestrator=orchestrator)

        actual = client.generate_a2a_t_prompt("Analyze Site A.")

        self.assertIs(actual, result)
        self.assertEqual(orchestrator.calls, ["Analyze Site A."])

    def test_prompt_client_builds_orchestrator_via_builder(self) -> None:
        from a2a_t.client.prompt_client import PromptClient

        temp_root = self.make_temp_dir("client_prompt")
        env_path = temp_root / ".env"
        env_path.write_text(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
                    "A2AT_LLM_API_KEY=test-key",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        result = PromptGenerationResult(
            success=True,
            prompt_text="generated prompt",
            scenario_code="energy_saving",
            language="en-US",
            input_kind="natural_language",
            slots={},
            validation=ValidationResult(passed=True, slot_errors=[]),
            failure=None,
        )
        llm_client = object()
        orchestrator = FakeOrchestrator(result)
        orchestrator_builder = FakeOrchestratorBuilder(orchestrator)

        client = PromptClient(
            env_path=env_path,
            llm_client=llm_client,
            orchestrator_builder=orchestrator_builder,
        )
        actual = client.generate_a2a_t_prompt("Analyze Site A.")

        self.assertIs(actual, result)
        self.assertEqual(orchestrator.calls, ["Analyze Site A."])
        self.assertEqual(len(orchestrator_builder.calls), 1)
        self.assertIs(orchestrator_builder.calls[0]["llm_client"], llm_client)
        self.assertIsNone(orchestrator_builder.calls[0]["resource_root"])
        self.assertEqual(orchestrator_builder.calls[0]["config"].prompt.language, "en-US")


if __name__ == "__main__":
    unittest.main()
