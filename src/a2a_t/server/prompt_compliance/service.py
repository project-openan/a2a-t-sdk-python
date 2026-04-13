from __future__ import annotations

from typing import Any

from a2a_t.prompt.analysis.errors import PromptAnalysisError
from a2a_t.prompt.resources.errors import PromptResourceNotFoundError
from a2a_t.prompt.validation.models import SlotValidationResult
from a2a_t.server.prompt_compliance.errors import ProcessedPromptParseError
from a2a_t.server.prompt_compliance.models import PromptComplianceResult


class PromptComplianceService:
    """Coordinate prompt compliance validation flow on the server side."""

    def __init__(
        self,
        *,
        guardrail: Any,
        parser: Any,
        template_loader: Any,
        slot_schema_loader: Any,
        prompt_resource_loader: Any,
        extractor: Any,
        validator: Any,
    ) -> None:
        self._guardrail = guardrail
        self._parser = parser
        self._template_loader = template_loader
        self._slot_schema_loader = slot_schema_loader
        self._prompt_resource_loader = prompt_resource_loader
        self._extractor = extractor
        self._validator = validator

    def check(
        self,
        *,
        processed_prompt_text: str,
        request_metadata: dict[str, object] | None = None,
    ) -> PromptComplianceResult:
        guardrail_result = self._guardrail.check(processed_prompt_text, request_metadata)
        if not guardrail_result.passed:
            return self._error_result(
                stage="guardrail",
                error_code=guardrail_result.error_code or "guardrail_rejected",
                error_message=guardrail_result.error_message or "Guardrail rejected the processed prompt.",
            )

        try:
            identity = self._parser.parse(processed_prompt_text)
        except ProcessedPromptParseError as error:
            return self._error_result("prompt_parse", "processed_prompt_parse_error", str(error))

        try:
            template_text = self._template_loader.load(
                scenario_code=identity.scenario_code,
                version=identity.version,
                language=identity.language,
            )
        except PromptResourceNotFoundError as error:
            return self._error_result("generation", "template_load_error", str(error))

        try:
            slot_schema = self._slot_schema_loader.load(
                scenario_code=identity.scenario_code,
                version=identity.version,
                language=identity.language,
            )
        except PromptResourceNotFoundError as error:
            return self._error_result("generation", "slot_schema_load_error", str(error))

        try:
            slot_prompts = self._prompt_resource_loader.load(
                analysis_action="slot_extraction",
                version=identity.version,
                language=identity.language,
            )
        except PromptResourceNotFoundError as error:
            return self._error_result("generation", "prompt_resource_load_error", str(error))

        try:
            extraction_result = self._extractor.extract(
                normalized_input=processed_prompt_text,
                scenario_code=identity.scenario_code,
                version=identity.version,
                language=identity.language,
                template_text=template_text,
                slot_schema=slot_schema,
                system_prompt=slot_prompts.system_prompt,
                user_prompt=slot_prompts.user_prompt,
            )
        except PromptAnalysisError as error:
            return self._error_result("slot_extraction", "slot_extraction_error", str(error))
        except Exception as error:
            return self._error_result("slot_extraction", "slot_extraction_error", str(error))

        validation_result: SlotValidationResult = self._validator.validate(
            slots=extraction_result.slots,
            slot_errors=extraction_result.slot_errors,
            slot_schema=slot_schema,
        )
        if not validation_result.passed:
            return PromptComplianceResult(
                passed=False,
                stage="slot_validation",
                extracted_slots=extraction_result.slots,
                error_code="slot_validation_error",
                error_message=self._aggregate_slot_errors(validation_result),
            )

        return PromptComplianceResult(
            passed=True,
            stage="passed",
            extracted_slots=extraction_result.slots,
        )

    def _aggregate_slot_errors(self, validation_result: SlotValidationResult) -> str:
        messages = [slot_error.message for slot_error in validation_result.slot_errors if slot_error.message]
        return "; ".join(messages) if messages else "Slot validation failed."

    @staticmethod
    def _error_result(stage: str, error_code: str, error_message: str) -> PromptComplianceResult:
        return PromptComplianceResult(
            passed=False,
            stage=stage,
            error_code=error_code,
            error_message=error_message,
        )
