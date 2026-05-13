from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from a2a_t.common.prompt_resources.models import SlotSchema
from a2a_t.prompt.common.models import PromptReference


@dataclass(frozen=True)
class SemanticValidationError:
    slot_name: str
    code: str
    message: str


@dataclass(frozen=True)
class SemanticValidationResult:
    passed: bool
    errors: list[SemanticValidationError]


class SemanticSlotValidator(Protocol):
    def validate(
        self,
        *,
        processed_prompt_text: str,
        reference: PromptReference,
        template_text: str,
        slot_schema: SlotSchema,
        slot_json_schema: dict[str, object],
        extracted_slots: dict[str, str | None],
    ) -> SemanticValidationResult:
        ...


class NoopSemanticSlotValidator:
    def validate(
        self,
        *,
        processed_prompt_text: str,
        reference: PromptReference,
        template_text: str,
        slot_schema: SlotSchema,
        slot_json_schema: dict[str, object],
        extracted_slots: dict[str, str | None],
    ) -> SemanticValidationResult:
        return SemanticValidationResult(passed=True, errors=[])
