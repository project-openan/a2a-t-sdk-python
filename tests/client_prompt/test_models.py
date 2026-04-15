from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.validation.models import SlotValidationError


class PromptGenerationModelsTest(unittest.TestCase):
    def test_validation_result_exposes_only_shared_slot_errors(self) -> None:
        from a2a_t.client.prompt_generation.models import ValidationResult

        validation = ValidationResult(
            passed=False,
            slot_errors=[
                SlotValidationError(
                    slot_name="site",
                    code="missing_input",
                    message="Required slot 'site' is missing.",
                )
            ],
        )

        self.assertFalse(hasattr(validation, "missing_required_fields"))
        self.assertEqual(
            validation.to_dict(),
            {
                "passed": False,
                "slot_errors": [
                    {
                        "slot_name": "site",
                        "code": "missing_input",
                        "message": "Required slot 'site' is missing.",
                    }
                ],
            },
        )

    def test_prompt_generation_result_uses_flat_scenario_code_field(self) -> None:
        from a2a_t.client.prompt_generation.models import PromptGenerationResult, ValidationResult

        result = PromptGenerationResult(
            success=True,
            prompt_text="body",
            scenario_code="energy_saving",
            language="en-US",
            input_kind="natural_language",
            slots={"site": "Site A"},
            validation=ValidationResult(passed=True, slot_errors=[]),
            failure=None,
        )

        self.assertEqual(
            result.to_dict(),
            {
                "success": True,
                "prompt_text": "body",
                "scenario_code": "energy_saving",
                "language": "en-US",
                "input_kind": "natural_language",
                "slots": {"site": "Site A"},
                "validation": {"passed": True, "slot_errors": []},
                "failure": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
