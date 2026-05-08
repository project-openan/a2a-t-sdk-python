from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.validation.models import SlotValidationError, SlotValidationResult


class JsonSchemaSlotValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "site": {
                    "type": "string",
                    "minLength": 1,
                    "x-a2at-value-constraint": "Must be a concrete site name.",
                },
                "incident_level": {
                    "type": "string",
                    "enum": ["critical", "major"],
                    "x-a2at-value-constraint": "Must be one of: critical, major.",
                },
            },
            "required": ["site"],
        }

    def test_validate_passes_when_slots_match_schema_and_no_upstream_errors(self) -> None:
        from a2a_t.prompt.validation.json_schema_slot_validator import JsonSchemaSlotValidator

        validator = JsonSchemaSlotValidator()
        result = validator.validate(
            slots={"site": "Site A", "incident_level": "critical"},
            slot_errors=[],
            slot_json_schema=self.schema,
        )

        self.assertEqual(result, SlotValidationResult(passed=True, slot_errors=[]))

    def test_validate_adds_missing_input_for_required_slot_with_null_value(self) -> None:
        from a2a_t.prompt.validation.json_schema_slot_validator import JsonSchemaSlotValidator

        validator = JsonSchemaSlotValidator()
        result = validator.validate(
            slots={"site": None, "incident_level": "critical"},
            slot_errors=[],
            slot_json_schema=self.schema,
        )

        self.assertEqual(
            result,
            SlotValidationResult(
                passed=False,
                slot_errors=[
                    SlotValidationError(
                        slot_name="site",
                        code="missing_input",
                        message="Required slot 'site' is missing.",
                    )
                ],
            ),
        )

    def test_validate_adds_missing_input_for_blank_required_slot(self) -> None:
        from a2a_t.prompt.validation.json_schema_slot_validator import JsonSchemaSlotValidator

        validator = JsonSchemaSlotValidator()
        result = validator.validate(
            slots={"site": "   ", "incident_level": "critical"},
            slot_errors=[],
            slot_json_schema=self.schema,
        )

        self.assertEqual(
            result,
            SlotValidationResult(
                passed=False,
                slot_errors=[
                    SlotValidationError(
                        slot_name="site",
                        code="missing_input",
                        message="Required slot 'site' is missing.",
                    )
                ],
            ),
        )

    def test_validate_adds_invalid_value_for_enum_violation(self) -> None:
        from a2a_t.prompt.validation.json_schema_slot_validator import JsonSchemaSlotValidator

        validator = JsonSchemaSlotValidator()
        result = validator.validate(
            slots={"site": "Site A", "incident_level": "warning"},
            slot_errors=[],
            slot_json_schema=self.schema,
        )

        self.assertEqual(
            result,
            SlotValidationResult(
                passed=False,
                slot_errors=[
                    SlotValidationError(
                        slot_name="incident_level",
                        code="invalid_value",
                        message="Must be one of: critical, major.",
                    )
                ],
            ),
        )

    def test_validate_ignores_null_optional_slot_values(self) -> None:
        from a2a_t.prompt.validation.json_schema_slot_validator import JsonSchemaSlotValidator

        validator = JsonSchemaSlotValidator()
        result = validator.validate(
            slots={"site": "Site A", "incident_level": None},
            slot_errors=[],
            slot_json_schema=self.schema,
        )

        self.assertEqual(result, SlotValidationResult(passed=True, slot_errors=[]))

    def test_validate_ignores_blank_optional_slot_values(self) -> None:
        from a2a_t.prompt.validation.json_schema_slot_validator import JsonSchemaSlotValidator

        validator = JsonSchemaSlotValidator()
        result = validator.validate(
            slots={"site": "Site A", "incident_level": "   "},
            slot_errors=[],
            slot_json_schema=self.schema,
        )

        self.assertEqual(result, SlotValidationResult(passed=True, slot_errors=[]))

    def test_validate_uses_business_constraint_message_for_pattern_violation(self) -> None:
        from a2a_t.prompt.validation.json_schema_slot_validator import JsonSchemaSlotValidator

        validator = JsonSchemaSlotValidator()
        result = validator.validate(
            slots={"site": "Site A", "incident_level": "[]"},
            slot_errors=[],
            slot_json_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "site": {"type": "string", "minLength": 1},
                    "incident_level": {
                        "type": "string",
                        "pattern": "^\\s*\\[(?:\\s*\"(?:critical|major)\"\\s*(?:,\\s*\"(?:critical|major)\"\\s*)*)\\]\\s*$",
                        "x-a2at-value-constraint": "Must be a JSON array string containing one or more of: critical, major.",
                    },
                },
                "required": ["site"],
            },
        )

        self.assertEqual(
            result,
            SlotValidationResult(
                passed=False,
                slot_errors=[
                    SlotValidationError(
                        slot_name="incident_level",
                        code="invalid_value",
                        message="Must be a JSON array string containing one or more of: critical, major.",
                    )
                ],
            ),
        )

    def test_validate_ignores_upstream_missing_input_for_optional_null_slot(self) -> None:
        from a2a_t.prompt.validation.json_schema_slot_validator import JsonSchemaSlotValidator

        validator = JsonSchemaSlotValidator()
        result = validator.validate(
            slots={"site": "Site A", "incident_level": None},
            slot_errors=[
                SlotValidationError(
                    slot_name="incident_level",
                    code="missing_input",
                    message="Optional slot is missing.",
                )
            ],
            slot_json_schema=self.schema,
        )

        self.assertEqual(result, SlotValidationResult(passed=True, slot_errors=[]))

    def test_validate_preserves_upstream_slot_errors_without_duplicate_local_error(self) -> None:
        from a2a_t.prompt.validation.json_schema_slot_validator import JsonSchemaSlotValidator

        validator = JsonSchemaSlotValidator()
        result = validator.validate(
            slots={"site": None, "incident_level": "critical"},
            slot_errors=[
                SlotValidationError(
                    slot_name="site",
                    code="missing_input",
                    message="Required slot 'site' is missing.",
                )
            ],
            slot_json_schema=self.schema,
        )

        self.assertEqual(
            result,
            SlotValidationResult(
                passed=False,
                slot_errors=[
                    SlotValidationError(
                        slot_name="site",
                        code="missing_input",
                        message="Required slot 'site' is missing.",
                    )
                ],
            ),
        )


if __name__ == "__main__":
    unittest.main()
