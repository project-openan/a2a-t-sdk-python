from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.client.prompt.models import PromptGenerationResult, ValidationResult


class FakeOrchestrator:
    def __init__(self, result: PromptGenerationResult) -> None:
        self._result = result
        self.calls: list[object] = []

    def generate(self, user_input: object) -> PromptGenerationResult:
        self.calls.append(user_input)
        return self._result


class PromptClientTest(unittest.TestCase):
    def test_generate_a2a_t_prompt_delegates_to_orchestrator(self) -> None:
        from a2a_t.client.prompt_client import PromptClient

        result = PromptGenerationResult(
            success=True,
            prompt_text="test prompt",
            scenario=None,
            language="en-US",
            input_kind="natural_language",
            slots={},
            validation=ValidationResult(passed=True, missing_required_fields=[], slot_errors=[]),
            failure=None,
        )
        orchestrator = FakeOrchestrator(result)
        client = PromptClient(orchestrator=orchestrator)

        actual = client.generate_a2a_t_prompt("Analyze Site A.")

        self.assertIs(actual, result)
        self.assertEqual(orchestrator.calls, ["Analyze Site A."])


if __name__ == "__main__":
    unittest.main()
