from __future__ import annotations

from typing import Protocol

from .models import SemanticValidationResult


class SemanticSlotValidator(Protocol):
    def validate(
        self,
        *,
        language: str,
        slot_json_schema: dict[str, object],
        extracted_slots: dict[str, str | None],
    ) -> SemanticValidationResult:
        ...
