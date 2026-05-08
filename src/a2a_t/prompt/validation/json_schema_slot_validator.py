from __future__ import annotations

from jsonschema import Draft202012Validator

from .constants import INVALID_VALUE, MISSING_INPUT
from .models import SlotValidationError, SlotValidationResult


class JsonSchemaSlotValidator:
    """Validate extracted string slots with a JSON Schema."""

    def validate(
        self,
        *,
        slots: dict[str, str | None],
        slot_errors: list[SlotValidationError],
        slot_json_schema: dict[str, object],
    ) -> SlotValidationResult:
        """Merge upstream extraction errors with local JSON Schema validation errors."""
        required_slots = set(self._required_slots(slot_json_schema))
        normalized_slots = self._normalize_slots(slots=slots, required_slots=required_slots)
        normalized_errors = self._filter_upstream_slot_errors(
            slot_errors=slot_errors,
            slots=slots,
            required_slots=required_slots,
        )
        existing_slot_names = {error.slot_name for error in normalized_errors}
        validator = Draft202012Validator(slot_json_schema)

        for slot_name in required_slots:
            if slot_name in existing_slot_names:
                continue
            slot_value = normalized_slots.get(slot_name)
            if slot_value is None or (isinstance(slot_value, str) and not slot_value.strip()):
                normalized_errors.append(
                    SlotValidationError(
                        slot_name=slot_name,
                        code=MISSING_INPUT,
                        message=f"Required slot '{slot_name}' is missing.",
                    )
                )
                existing_slot_names.add(slot_name)

        for error in validator.iter_errors(normalized_slots):
            slot_name = self._resolve_slot_name(error)
            if slot_name is None or slot_name in existing_slot_names:
                continue
            normalized_errors.append(
                self._build_slot_error(
                    slot_name=slot_name,
                    validator_name=error.validator,
                    required_slots=required_slots,
                    instance=error.instance,
                    slot_json_schema=slot_json_schema,
                )
            )
            existing_slot_names.add(slot_name)

        return SlotValidationResult(
            passed=not normalized_errors,
            slot_errors=normalized_errors,
        )

    @classmethod
    def _normalize_slots(
        cls,
        *,
        slots: dict[str, str | None],
        required_slots: set[str],
    ) -> dict[str, str | None]:
        return {
            slot_name: slot_value
            for slot_name, slot_value in slots.items()
            if slot_name in required_slots or not cls._is_empty_slot_value(slot_value)
        }

    @classmethod
    def _filter_upstream_slot_errors(
        cls,
        *,
        slot_errors: list[SlotValidationError],
        slots: dict[str, str | None],
        required_slots: set[str],
    ) -> list[SlotValidationError]:
        return [
            error
            for error in slot_errors
            if error.slot_name in required_slots or not cls._is_empty_slot_value(slots.get(error.slot_name))
        ]

    @staticmethod
    def _required_slots(slot_json_schema: dict[str, object]) -> list[str]:
        raw_required = slot_json_schema.get("required")
        if not isinstance(raw_required, list):
            return []
        return [str(item) for item in raw_required]

    @staticmethod
    def _is_empty_slot_value(value: object) -> bool:
        return value is None or (isinstance(value, str) and not value.strip())

    @staticmethod
    def _resolve_slot_name(error) -> str | None:
        if error.path:
            return str(next(iter(error.path)))
        return None

    def _build_slot_error(
        self,
        *,
        slot_name: str,
        validator_name: str,
        required_slots: set[str],
        instance: object,
        slot_json_schema: dict[str, object],
    ) -> SlotValidationError:
        is_missing_required = slot_name in required_slots and (
            instance is None or (isinstance(instance, str) and not instance.strip())
        )
        if validator_name in {"type", "minLength"} and is_missing_required:
            return SlotValidationError(
                slot_name=slot_name,
                code=MISSING_INPUT,
                message=f"Required slot '{slot_name}' is missing.",
            )
        business_message = self._constraint_message(slot_json_schema=slot_json_schema, slot_name=slot_name)
        return SlotValidationError(
            slot_name=slot_name,
            code=INVALID_VALUE,
            message=business_message or f"Slot '{slot_name}' has invalid value.",
        )

    @staticmethod
    def _constraint_message(*, slot_json_schema: dict[str, object], slot_name: str) -> str | None:
        properties = slot_json_schema.get("properties")
        if not isinstance(properties, dict):
            return None
        property_schema = properties.get(slot_name)
        if not isinstance(property_schema, dict):
            return None
        message = property_schema.get("x-a2at-value-constraint")
        if not isinstance(message, str):
            return None
        normalized_message = message.strip()
        return normalized_message or None
