from __future__ import annotations

from typing import Any

from a2a_t.prompt.analysis.models import SlotExtractionResult


class PromptSlotExtractor:
    """Thin server-side wrapper around the shared prompt slot extractor."""

    def __init__(self, *, slot_extractor: Any) -> None:
        self._slot_extractor = slot_extractor

    def extract(
        self,
        *,
        normalized_input: str,
        scenario_code: str,
        version: str,
        language: str,
        template_text: str,
        slot_schema: Any,
        system_prompt: str,
        user_prompt: str,
    ) -> SlotExtractionResult:
        return self._slot_extractor.extract(
            normalized_input=normalized_input,
            scenario_code=scenario_code,
            version=version,
            language=language,
            template_text=template_text,
            slot_schema=slot_schema,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
