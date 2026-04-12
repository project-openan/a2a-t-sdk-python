from __future__ import annotations

from typing import Any

from a2a_t.server.prompt_compliance.slot_schema import SlotDefinition, SlotSchema


class SlotSchemaBuilder:
    """从槽位配置构建运行时 JSON Schema / Build runtime JSON Schema from slot configuration."""

    def build(self, slot_schema: SlotSchema) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        conditional_rules: list[dict[str, Any]] = []

        for slot in slot_schema.slots:
            properties[slot.name] = self._build_slot_schema(slot)
            if slot.required:
                required.append(slot.name)

        for rule in slot_schema.rules:
            conditional_rules.append(
                {
                    "if": {
                        "properties": {
                            rule.when.slot: {
                                "const": rule.when.equals,
                            }
                        },
                        "required": [rule.when.slot],
                    },
                    "then": {
                        "required": list(rule.requires),
                    },
                }
            )

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": True,
        }
        if conditional_rules:
            schema["allOf"] = conditional_rules
        return schema

    def _build_slot_schema(self, slot: SlotDefinition) -> dict[str, Any]:
        if slot.type == "enum":
            schema: dict[str, Any] = {
                "type": "string",
                "enum": list(slot.allowed_values or []),
            }
        elif slot.type == "list":
            schema = {"type": "array"}
        else:
            schema = {"type": slot.type}

        if slot.pattern is not None:
            schema["pattern"] = slot.pattern

        if slot.range is not None:
            if slot.type in {"number", "integer"}:
                if slot.range.min is not None:
                    schema["minimum"] = slot.range.min
                if slot.range.max is not None:
                    schema["maximum"] = slot.range.max
            elif slot.type == "string":
                if slot.range.min is not None:
                    schema["minLength"] = int(slot.range.min)
                if slot.range.max is not None:
                    schema["maxLength"] = int(slot.range.max)
            elif slot.type == "list":
                if slot.range.min is not None:
                    schema["minItems"] = int(slot.range.min)
                if slot.range.max is not None:
                    schema["maxItems"] = int(slot.range.max)

        return schema
