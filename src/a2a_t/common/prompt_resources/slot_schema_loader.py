from __future__ import annotations

from a2a_t.prompt.common.models import PromptReference

from .local_resources import BasePromptResourceLoader
from .errors import PromptResourceParseError
from .models import SlotDefinition, SlotRange, SlotSchema


class SlotSchemaLoader(BasePromptResourceLoader):
    """Load slot schemas used by extraction and validation flows."""

    def load(self, *, reference: PromptReference) -> SlotSchema:
        """Return the slot schema for the referenced scenario resource."""
        path = f"slots/{reference.scenario_code}/{reference.language}/slot.json"
        data = self._read_json_with_fallback(path)
        if self._looks_like_json_schema(data):
            return self._build_from_json_schema(data, reference=reference)
        raise PromptResourceParseError(
            "slot.json must be a JSON Schema object.",
            path=path,
            scenario_code=reference.scenario_code,
            language=reference.language,
        )

    @staticmethod
    def _looks_like_json_schema(data: dict[str, object]) -> bool:
        """Return whether the loaded payload already looks like a JSON Schema object."""
        return "slots" not in data and data.get("type") == "object" and "properties" in data

    def _build_from_json_schema(self, data: dict[str, object], *, reference: PromptReference) -> SlotSchema:
        """Project a standard JSON Schema back into the SlotSchema view used by generation flows."""
        properties = data.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        required_names = set(data.get("required") or [])
        slots = [
            self._build_slot_definition_from_property(
                name=str(name),
                property_schema=property_schema,
                required=bool(name in required_names),
            )
            for name, property_schema in properties.items()
        ]
        return SlotSchema(
            scenario_code=reference.scenario_code,
            slots=slots,
        )

    def _build_slot_definition_from_property(
        self,
        *,
        name: str,
        property_schema: object,
        required: bool,
    ) -> SlotDefinition:
        """Normalize one JSON Schema property into the shared slot definition model."""
        if not isinstance(property_schema, dict):
            property_schema = {}

        examples = property_schema.get("examples")
        example = ""
        if isinstance(examples, list) and examples:
            example = str(examples[0])

        allowed_values = property_schema.get("x-a2at-allowed-values")
        if allowed_values is None:
            allowed_values = property_schema.get("enum")
        if allowed_values is not None and not isinstance(allowed_values, list):
            allowed_values = [allowed_values]

        slot_range = None
        minimum = property_schema.get("minimum")
        maximum = property_schema.get("maximum")
        if minimum is not None or maximum is not None:
            slot_range = SlotRange(min=minimum, max=maximum)  # type: ignore[arg-type]

        slot_type = property_schema.get("x-a2at-slot-type")
        if slot_type is None:
            slot_type = property_schema.get("type")

        return SlotDefinition(
            name=name,
            required=required,
            description=str(property_schema.get("description") or ""),
            example=example,
            value_constraint=str(property_schema.get("x-a2at-value-constraint") or ""),
            type=str(slot_type) if slot_type is not None else None,
            allowed_values=allowed_values,
            range=slot_range,
            pattern=str(property_schema["pattern"]) if property_schema.get("pattern") is not None else None,
        )
