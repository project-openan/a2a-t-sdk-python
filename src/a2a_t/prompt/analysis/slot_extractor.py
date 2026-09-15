from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from a2a_t.common.prompt_resources.models import SlotSchema
from a2a_t.prompt.common.models import PromptReference
from a2a_t.prompt.validation.models import SlotValidationError

from .errors import SlotExtractionError
from .json_schema_builder import AnalysisJsonSchemaBuilder
from .message_builder import AnalysisMessageBuilder
from .models import SlotExtractionResult

_LOGGER = logging.getLogger(__name__)

#: Slot-extraction error codes accepted from the model response. The prompt contract teaches the
#: catalog pair (``slot.not_provided`` / ``slot.constraint_violated`` with ``facts`` and no
#: message, Java parity); the legacy pair is kept for backward compatibility and mapped at the
#: orchestrator boundary.
SUPPORTED_SLOT_ERROR_CODES: frozenset[str] = frozenset(
    {
        "slot.not_provided",
        "slot.constraint_violated",
        "missing_input",
        "invalid_value",
    }
)


class SlotExtractor:
    """Extract slot values from normalized input with an LLM-backed structured call."""

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
        data_schema: Mapping[str, object] | None = None,
    ) -> SlotExtractionResult:
        """Run slot extraction and normalize the structured LLM response.

        The optional ``data_schema`` describes the meaning of each structured input field and is
        appended to the extraction user message (Java ``extractSlots(input, code, language,
        dataSchema)`` — the schema-guided variant of the metadata-content generation APIs).
        """
        messages = self._message_builder.build_slot_extraction_messages(
            normalized_input=normalized_input,
            reference=reference,
            slot_schema=slot_schema,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            data_schema=data_schema,
        )
        response = self._llm_client.structured(
            messages=messages,
            json_schema=self._json_schema_builder.build_slot_extraction_schema(slot_schema=slot_schema),
        )
        self.last_raw_response_content = response.content
        return self._parse_response(response.content, slot_schema=slot_schema)

    def _parse_response(self, content: str, *, slot_schema: SlotSchema) -> SlotExtractionResult:
        """Validate the LLM response shape before downstream validation consumes it."""
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
                # Tolerate a model omitting an unprovided slot key: treat it as null so the
                # downstream required-slot validation reports ``slot.not_provided`` instead of
                # failing the whole extraction here.
                _LOGGER.warning(
                    "Slot extraction response omitted slot key '%s'; normalized to null.",
                    slot_name,
                )
                normalized_slots[slot_name] = None
                continue
            slot_value = slots[slot_name]
            if slot_value is not None and not isinstance(slot_value, str):
                raise SlotExtractionError(
                    "Slot extraction slot values must be string or null.",
                    raw_content=content,
                    slot_name=slot_name,
                )
            normalized_slots[slot_name] = slot_value

        slot_errors = [
            self._build_slot_error(
                item,
                expected_slot_names=expected_slot_names,
                raw_content=content,
            )
            for item in raw_slot_errors
        ]
        return SlotExtractionResult(slots=normalized_slots, slot_errors=slot_errors)

    def _build_slot_error(
        self,
        raw_error: object,
        *,
        expected_slot_names: set[str],
        raw_content: str,
    ) -> SlotValidationError:
        """Normalize and validate one slot_error entry from the model response.

        Accepts both vocabularies: the catalog pair taught by the extraction prompt
        (``slot.not_provided`` / ``slot.constraint_violated`` with ``facts`` and no message, Java
        parity) and the legacy pair (``missing_input`` / ``invalid_value`` with ``message``).
        """
        if not isinstance(raw_error, dict):
            raise SlotExtractionError("Slot extraction slot_errors items must be objects.", raw_content=raw_content)

        slot_name = raw_error.get("slot_name")
        code = raw_error.get("code")
        facts = raw_error.get("facts")
        message = raw_error.get("message")

        if not isinstance(slot_name, str) or slot_name not in expected_slot_names:
            raise SlotExtractionError("Slot extraction returned unknown slot_error slot_name.", raw_content=raw_content)
        if code not in SUPPORTED_SLOT_ERROR_CODES:
            raise SlotExtractionError("Slot extraction returned unsupported slot_error code.", raw_content=raw_content)
        if facts is not None and not isinstance(facts, dict):
            raise SlotExtractionError("Slot extraction slot_errors facts must be an object.", raw_content=raw_content)
        if facts is not None and not all(
            isinstance(key, str) and isinstance(value, str) for key, value in facts.items()
        ):
            raise SlotExtractionError(
                "Slot extraction slot_errors facts must map strings to strings.",
                raw_content=raw_content,
            )
        if message is not None and (not isinstance(message, str) or not message.strip()):
            raise SlotExtractionError("Slot extraction returned empty slot_error message.", raw_content=raw_content)

        return SlotValidationError(
            slot_name=slot_name,
            code=str(code),
            message=message or "",
            facts={key: value for key, value in facts.items()} if isinstance(facts, dict) else None,
        )
