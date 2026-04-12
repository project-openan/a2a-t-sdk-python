from __future__ import annotations

import json
from typing import Any

from a2a_t.llm.base import LLMAdapter
from a2a_t.prompt.models import Prompt
from a2a_t.server.prompt_compliance.errors import SlotExtractionError
from a2a_t.server.prompt_compliance.models import SlotExtractionResult


class PromptSlotExtractor:
    """从原始 Prompt 和加工后 Prompt 中提取结构化槽位 / Extract structured slots from original and processed prompts."""

    _OUTPUT_SCHEMA: dict[str, Any] = {
        "type": "object",
        "properties": {
            "slots": {"type": "object"},
            "notes": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": ["number", "null"]},
        },
        "required": ["slots", "notes"],
        "additionalProperties": False,
    }

    def __init__(self, adapter: LLMAdapter) -> None:
        self._adapter = adapter

    def extract(self, *, original_prompt: Prompt, processed_prompt_text: str) -> SlotExtractionResult:
        response = self._adapter.structured(
            messages=self._build_messages(original_prompt=original_prompt, processed_prompt_text=processed_prompt_text),
            json_schema=self._OUTPUT_SCHEMA,
        )
        payload = self._parse_response(response.content)
        return SlotExtractionResult(
            slots=payload["slots"],
            notes=payload["notes"],
            confidence=payload.get("confidence"),
            raw_response=None,
        )

    def _build_messages(self, *, original_prompt: Prompt, processed_prompt_text: str) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "Extract task slots from the processed prompt with reference to the original prompt. "
                    "Return JSON matching the provided schema."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Original prompt:\n{original_prompt.raw_content or original_prompt.body}\n\n"
                    f"Processed prompt:\n{processed_prompt_text}"
                ),
            },
        ]

    def _parse_response(self, content: str) -> dict[str, Any]:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise SlotExtractionError("Structured slot extraction returned invalid JSON", raw_content=content) from exc

        if not isinstance(payload, dict):
            raise SlotExtractionError("Structured slot extraction must return a JSON object", raw_content=content)
        if "slots" not in payload or "notes" not in payload:
            raise SlotExtractionError("Structured slot extraction response is missing required fields", raw_content=content)
        if not isinstance(payload["slots"], dict):
            raise SlotExtractionError("Structured slot extraction field 'slots' must be an object", raw_content=content)
        if not isinstance(payload["notes"], list) or not all(isinstance(item, str) for item in payload["notes"]):
            raise SlotExtractionError("Structured slot extraction field 'notes' must be a string list", raw_content=content)

        confidence = payload.get("confidence")
        if confidence is not None and not isinstance(confidence, int | float):
            raise SlotExtractionError("Structured slot extraction field 'confidence' must be numeric or null", raw_content=content)

        return payload
