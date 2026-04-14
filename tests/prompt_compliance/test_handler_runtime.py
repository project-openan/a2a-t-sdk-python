from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.server.prompt_compliance.result import PromptComplianceResult
from a2a_t.server.prompt_handler import PromptHandler
from tests.test_support import ManagedTempDirTestCase


class PromptHandlerRuntimeTest(ManagedTempDirTestCase):
    def test_process_returns_compact_payload_without_notes_or_confidence(self) -> None:
        class FakeComplianceService:
            def check(self, *, processed_prompt_text: str, request_metadata: dict[str, object] | None) -> PromptComplianceResult:
                return PromptComplianceResult(
                    passed=True,
                    stage="passed",
                    extracted_slots={"site": "Site A"},
                )

        handler = PromptHandler(validator=FakeComplianceService())

        result = handler.process("task-1", {"processed_prompt_text": "processed prompt"})

        self.assertEqual(
            result,
            {
                "passed": True,
                "stage": "passed",
                "extracted_slots": {"site": "Site A"},
                "error_code": None,
                "error_message": None,
            },
        )

    def test_handler_builds_validator_via_builder(self) -> None:
        class FakeComplianceService:
            def check(self, *, processed_prompt_text: str, request_metadata: dict[str, object] | None) -> PromptComplianceResult:
                return PromptComplianceResult(
                    passed=True,
                    stage="passed",
                    extracted_slots={"site": "Site A"},
                )

        class FakeValidatorBuilder:
            def __init__(self, validator: FakeComplianceService) -> None:
                self.validator = validator
                self.calls: list[dict[str, object]] = []

            def build(self, **kwargs: object) -> FakeComplianceService:
                self.calls.append(dict(kwargs))
                return self.validator

        env_path = self.make_temp_dir("prompt_handler") / ".env"
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
        llm_client = object()
        validator = FakeComplianceService()
        builder = FakeValidatorBuilder(validator)

        handler = PromptHandler(
            env_path=env_path,
            llm_client=llm_client,
            validator_builder=builder,
        )
        result = handler.process("task-1", {"processed_prompt_text": "processed prompt"})

        self.assertEqual(result["passed"], True)
        self.assertEqual(len(builder.calls), 1)
        self.assertIs(builder.calls[0]["llm_client"], llm_client)


if __name__ == "__main__":
    unittest.main()
