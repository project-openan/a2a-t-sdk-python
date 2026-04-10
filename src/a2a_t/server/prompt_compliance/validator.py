from __future__ import annotations

from jsonschema import Draft202012Validator

from a2a_t.server.prompt_compliance.models import SlotValidationResult
from a2a_t.server.prompt_compliance.schema_builder import SlotSchemaBuilder
from a2a_t.server.prompt_compliance.slot_config import SlotConfig


class SlotValidator:
    """Validate extracted slots against a generated JSON Schema."""

    def __init__(self, schema_builder: SlotSchemaBuilder | None = None) -> None:
        self._schema_builder = schema_builder or SlotSchemaBuilder()

    def validate(self, *, extracted_slots: dict[str, object], slot_config: SlotConfig) -> SlotValidationResult:
        schema = self._schema_builder.build(slot_config)
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(extracted_slots), key=lambda error: list(error.path))
        return SlotValidationResult(
            valid=not errors,
            errors=[error.message for error in errors],
        )
