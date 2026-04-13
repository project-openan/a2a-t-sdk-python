from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.server.prompt_compliance.models import PromptComplianceResult
from a2a_t.server.prompt_handler import PromptHandler


class PromptHandlerRuntimeTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
