from __future__ import annotations

import json
from typing import Any

from a2a_t.prompt.common.models import PromptReference
from a2a_t.common.prompt_resources.models import SlotSchema
from a2a_t.prompt.validation.models import SlotValidationError

from .errors import SlotExtractionError
from .json_schema_builder import AnalysisJsonSchemaBuilder
from .message_builder import AnalysisMessageBuilder
from .models import SlotExtractionResult


class SlotExtractor:
    def __init__(
        self,
        *,
        llm_client: Any,
        message_builder: AnalysisMessageBuilder | None = None,
        json_schema_builder: AnalysisJsonSchemaBuilder | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._message_builder = message_builder or AnalysisMessageBuilder()
        self._json_schema_builder = json_schema_builder or AnalysisJsonSchemaBuilder()
        self.last_raw_response_content: str | None = None

    def extract(
        self,
        *,
        normalized_input: str,
        reference: PromptReference,
        template_text: str,
        slot_schema: SlotSchema,
        system_prompt: str,
        user_prompt: str,
    ) -> SlotExtractionResult:
        messages = self._message_builder.build_slot_extraction_messages(
            normalized_input=normalized_input,
            reference=reference,
            template_text=template_text,
            slot_schema=slot_schema,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        response = self._llm_client.structured(
            messages=messages,
            json_schema=self._json_schema_builder.build_slot_extraction_schema(slot_schema=slot_schema),
        )
        self.last_raw_response_content = response.content
        return self._parse_response(response.content, slot_schema=slot_schema)

    def _parse_response(self, content: str, *, slot_schema: SlotSchema) -> SlotExtractionResult:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as error:
            raise SlotExtractionError("Slot extraction returned invalid JSON.", raw_content=content) from error

        if not isinstance(payload, dict):
            raise SlotExtractionError("Slot extraction must return a JSON object.", raw_content=content)

        slots = payload.get("slots")
        raw_slot_errors = payload.get("slot_errors")
        if not isinstance(slots, dict):
            raise SlotExtractionError("Slot extraction field 'slots' must be an object.", raw_content=content)
        if not isinstance(raw_slot_errors, list):
            raise SlotExtractionError("Slot extraction field 'slot_errors' must be an array.", raw_content=content)

        expected_slot_names = {slot.name for slot in slot_schema.slots}
        normalized_slots: dict[str, str | None] = {}
        for slot_name in expected_slot_names:
            if slot_name not in slots:
                raise SlotExtractionError(
                    "Slot extraction response is missing required slot keys.",
                    raw_content=content,
                    slot_name=slot_name,
                )
            slot_value = slots[slot_name]
            if slot_value is not None and not isinstance(slot_value, str):
                raise SlotExtractionError(
                    "Slot extraction slot values must be string or null.",
                    raw_content=content,
                    slot_name=slot_name,
                )
            normalized_slots[slot_name] = slot_value

        slot_errors = [self._build_slot_error(item, expected_slot_names=expected_slot_names, raw_content=content) for item in raw_slot_errors]
        return SlotExtractionResult(slots=normalized_slots, slot_errors=slot_errors)

    def _build_slot_error(
        self,
        raw_error: object,
        *,
        expected_slot_names: set[str],
        raw_content: str,
    ) -> SlotValidationError:
        if not isinstance(raw_error, dict):
            raise SlotExtractionError("Slot extraction slot_errors items must be objects.", raw_content=raw_content)

        slot_name = raw_error.get("slot_name")
        code = raw_error.get("code")
        message = raw_error.get("message")

        if not isinstance(slot_name, str) or slot_name not in expected_slot_names:
            raise SlotExtractionError("Slot extraction returned unknown slot_error slot_name.", raw_content=raw_content)
        if code not in {"missing_input", "invalid_value"}:
            raise SlotExtractionError("Slot extraction returned unsupported slot_error code.", raw_content=raw_content)
        if not isinstance(message, str) or not message.strip():
            raise SlotExtractionError("Slot extraction returned empty slot_error message.", raw_content=raw_content)

        return SlotValidationError(slot_name=slot_name, code=str(code), message=message)
