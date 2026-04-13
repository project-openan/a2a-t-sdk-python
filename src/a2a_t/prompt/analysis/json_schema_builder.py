from __future__ import annotations

from a2a_t.prompt.resources.models import SlotSchema


class AnalysisJsonSchemaBuilder:
    def build_scenario_recognition_schema(self) -> dict[str, object]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["matched", "scenario_code", "error_message"],
            "properties": {
                "matched": {"type": "boolean"},
                "scenario_code": {"type": ["string", "null"]},
                "error_message": {"type": ["string", "null"]},
            },
        }

    def build_slot_extraction_schema(self, *, slot_schema: SlotSchema) -> dict[str, object]:
        slot_properties = {slot.name: {"type": ["string", "null"]} for slot in slot_schema.slots}
        slot_required = [slot.name for slot in slot_schema.slots]
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["slots", "slot_errors"],
            "properties": {
                "slots": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": slot_required,
                    "properties": slot_properties,
                },
                "slot_errors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["slot_name", "code", "message"],
                        "properties": {
                            "slot_name": {"type": "string"},
                            "code": {"type": "string", "enum": ["missing_input", "invalid_value"]},
                            "message": {"type": "string"},
                        },
                    },
                },
            },
        }
