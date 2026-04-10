from __future__ import annotations

from typing import Any

from a2a_t.server.prompt_compliance.errors import (
    GuardrailExecutionError,
    ProcessedPromptParseError,
    PromptOriginResolveError,
    SlotConfigLoadError,
    SlotConfigValidationError,
    SlotExtractionError,
)
from a2a_t.server.prompt_compliance.models import PromptComplianceResult


class PromptComplianceService:
    """Coordinate prompt compliance validation flow on the server side."""

    def __init__(
        self,
        *,
        guardrail: Any,
        parser: Any,
        origin_resolver: Any,
        extractor: Any,
        slot_config_resolver: Any,
        validator: Any,
        slot_not_found_policy: str = "strict",
    ) -> None:
        self._guardrail = guardrail
        self._parser = parser
        self._origin_resolver = origin_resolver
        self._extractor = extractor
        self._slot_config_resolver = slot_config_resolver
        self._validator = validator
        self._slot_not_found_policy = slot_not_found_policy

    def check(
        self,
        *,
        processed_prompt_text: str,
        request_metadata: dict[str, object] | None = None,
    ) -> PromptComplianceResult:
        try:
            guardrail_result = self._guardrail.check(processed_prompt_text, request_metadata)
        except GuardrailExecutionError as error:
            return self._error_result("guardrail", "guardrail_execution_error", str(error))

        if not guardrail_result.passed:
            return self._error_result(
                "guardrail",
                "guardrail_rejected",
                guardrail_result.reason or "Guardrail rejected the processed prompt.",
            )

        try:
            identity = self._parser.parse(processed_prompt_text)
        except ProcessedPromptParseError as error:
            return self._error_result("prompt_parse", "processed_prompt_parse_error", str(error))

        try:
            original_prompt = self._origin_resolver.resolve(identity)
        except PromptOriginResolveError as error:
            return self._error_result("origin_resolve", "prompt_origin_resolve_error", str(error))

        try:
            extraction_result = self._extractor.extract(
                original_prompt=original_prompt,
                processed_prompt_text=processed_prompt_text,
            )
        except SlotExtractionError as error:
            return self._error_result("slot_extraction", "slot_extraction_error", str(error))

        try:
            slot_config = self._slot_config_resolver.load(identity)
        except SlotConfigLoadError as error:
            if self._slot_not_found_policy == "skip":
                return PromptComplianceResult(
                    passed=True,
                    stage="skipped_slot_validation",
                    extracted_slots=extraction_result.slots,
                    notes=extraction_result.notes,
                    confidence=extraction_result.confidence,
                )
            return self._error_result("slot_config", "slot_config_load_error", str(error))
        except SlotConfigValidationError as error:
            return self._error_result("slot_config", "slot_config_validation_error", str(error))

        validation_result = self._validator.validate(
            extracted_slots=extraction_result.slots,
            slot_config=slot_config,
        )
        if not validation_result.valid:
            return self._error_result(
                "slot_validation",
                "slot_validation_error",
                "; ".join(validation_result.errors) if validation_result.errors else "Slot validation failed.",
            )

        return PromptComplianceResult(
            passed=True,
            stage="passed",
            extracted_slots=extraction_result.slots,
            notes=extraction_result.notes,
            confidence=extraction_result.confidence,
        )

    @staticmethod
    def _error_result(stage: str, error_code: str, error_message: str) -> PromptComplianceResult:
        return PromptComplianceResult(
            passed=False,
            stage=stage,
            error_code=error_code,
            error_message=error_message,
        )
