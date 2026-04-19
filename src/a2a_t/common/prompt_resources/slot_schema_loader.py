from __future__ import annotations

from a2a_t.prompt.common.models import PromptReference

from ._base import BasePromptResourceLoader
from .models import SlotDefinition, SlotRange, SlotSchema


class SlotSchemaLoader(BasePromptResourceLoader):
    def load(self, *, reference: PromptReference) -> SlotSchema:
        path = f"slots/{reference.scenario_code}/{reference.version}/{reference.language}/slot.json"
        data = self._read_json(path)
        raw_slots = data.get("slots") or []

        return SlotSchema(
            scenario_code=str(data["scenario_code"]),
            version=str(data["version"]),
            slots=[self._build_slot_definition(item) for item in raw_slots],
        )

    def _build_slot_definition(self, raw_slot: dict[str, object]) -> SlotDefinition:
        raw_range = raw_slot.get("range")
        slot_range = None
        if isinstance(raw_range, dict):
            slot_range = SlotRange(
                min=raw_range.get("min"),  # type: ignore[arg-type]
                max=raw_range.get("max"),  # type: ignore[arg-type]
            )

        allowed_values = raw_slot.get("allowed_values")
        if allowed_values is not None and not isinstance(allowed_values, list):
            allowed_values = [allowed_values]

        return SlotDefinition(
            name=str(raw_slot["name"]),
            required=bool(raw_slot["required"]),
            description=str(raw_slot["description"]),
            example=str(raw_slot["example"]),
            value_constraint=str(raw_slot["value_constraint"]),
            type=str(raw_slot["type"]) if raw_slot.get("type") is not None else None,
            allowed_values=allowed_values,
            range=slot_range,
            pattern=str(raw_slot["pattern"]) if raw_slot.get("pattern") is not None else None,
        )
