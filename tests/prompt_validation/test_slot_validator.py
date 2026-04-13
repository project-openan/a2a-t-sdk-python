from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.resources.models import SlotDefinition, SlotSchema
from a2a_t.prompt.validation.models import SlotValidationError, SlotValidationResult


class SlotValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.slot_schema = SlotSchema(
            scenario_code="energy_saving",
            version="0.0.1",
            slots=[
                SlotDefinition(
                    name="site",
                    required=True,
                    description="Site name",
                    example="Site A",
                    value_constraint="Must be a concrete site name.",
                    type="string",
                    allowed_values=None,
                    range=None,
                    pattern=None,
                ),
                SlotDefinition(
                    name="additional_notes",
                    required=False,
                    description="Additional notes",
                    example="Focus on power system",
                    value_constraint="Free-form notes.",
                    type="string",
                    allowed_values=None,
                    range=None,
                    pattern=None,
                ),
            ],
        )

    def test_validate_passes_when_required_slots_are_present_and_no_slot_errors(self) -> None:
        from a2a_t.prompt.validation.slot_validator import SlotValidator

        validator = SlotValidator()
        result = validator.validate(
            slots={"site": "Site A", "additional_notes": ""},
            slot_errors=[],
            slot_schema=self.slot_schema,
        )

        self.assertEqual(result, SlotValidationResult(passed=True, slot_errors=[]))

    def test_validate_adds_missing_input_for_blank_required_slot(self) -> None:
        from a2a_t.prompt.validation.slot_validator import SlotValidator

        validator = SlotValidator()
        result = validator.validate(
            slots={"site": "   ", "additional_notes": None},
            slot_errors=[],
            slot_schema=self.slot_schema,
        )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.slot_errors,
            [
                SlotValidationError(
                    slot_name="site",
                    code="missing_input",
                    message="Required slot 'site' is missing.",
                )
            ],
        )

    def test_validate_preserves_upstream_slot_errors_without_duplicate_missing_input(self) -> None:
        from a2a_t.prompt.validation.slot_validator import SlotValidator

        validator = SlotValidator()
        result = validator.validate(
            slots={"site": None, "additional_notes": None},
            slot_errors=[
                SlotValidationError(
                    slot_name="site",
                    code="invalid_value",
                    message="Site format is invalid.",
                )
            ],
            slot_schema=self.slot_schema,
        )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.slot_errors,
            [
                SlotValidationError(
                    slot_name="site",
                    code="invalid_value",
                    message="Site format is invalid.",
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
