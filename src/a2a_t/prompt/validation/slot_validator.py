from __future__ import annotations

from a2a_t.common.prompt_resources.models import SlotSchema

from .models import SlotValidationError, SlotValidationResult


class SlotValidator:
    def validate(
        self,
        *,
        slots: dict[str, str | None],
        slot_errors: list[SlotValidationError],
        slot_schema: SlotSchema,
    ) -> SlotValidationResult:
        normalized_errors = list(slot_errors)
        existing_slot_names = {error.slot_name for error in normalized_errors}

        for slot in slot_schema.slots:
            slot_value = slots.get(slot.name)
            if not slot.required:
                continue
            if slot.name in existing_slot_names:
                continue
            if self._is_missing(slot_value):
                normalized_errors.append(
                    SlotValidationError(
                        slot_name=slot.name,
                        code="missing_input",
                        message=f"Required slot '{slot.name}' is missing.",
                    )
                )

        return SlotValidationResult(
            passed=not normalized_errors,
            slot_errors=normalized_errors,
        )

    def _is_missing(self, value: str | None) -> bool:
        if value is None:
            return True
        return not value.strip()
