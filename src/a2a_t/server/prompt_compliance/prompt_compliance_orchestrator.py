from __future__ import annotations

from a2a_t.prompt.analysis import SlotExtractor
from a2a_t.prompt.analysis.errors import PromptAnalysisError
from a2a_t.common.prompt_resources import (
    PromptResourceLoader,
    PromptResourceNotFoundError,
    PromptResourceParseError,
    SlotSchemaLoader,
    TemplateLoader,
)
from a2a_t.prompt.common.task_prompt_format import TaskPromptFormatError, parse_task_prompt_metadata
from a2a_t.prompt.common.errors import PromptSourceError
from a2a_t.prompt.common.models import PromptReference
from a2a_t.prompt.validation.constants import INVALID_VALUE, MISSING_INPUT
from a2a_t.prompt.validation.errors import GuardrailExecutionError
from a2a_t.prompt.validation.guardrails import SafetyGuardrail
from a2a_t.prompt.validation.models import SlotValidationError
from a2a_t.prompt.validation.models import SlotValidationResult
from a2a_t.prompt.validation.slot_validator import SlotValidator
from a2a_t.server.prompt_compliance.constants import (
    GUARDRAIL_EXECUTION_ERROR,
    GENERATION_STAGE,
    PROMPT_RESOURCE_ACCESS_ERROR,
    PROMPT_RESOURCE_PARSE_ERROR,
    GUARDRAIL_REJECTED,
    GUARDRAIL_STAGE,
    PASSED_STAGE,
    PROCESSED_PROMPT_PARSE_ERROR,
    PROMPT_PARSE_STAGE,
    PROMPT_RESOURCE_LOAD_ERROR,
    SLOT_EXTRACTION_ERROR,
    SLOT_EXTRACTION_STAGE,
    SLOT_SCHEMA_LOAD_ERROR,
    SLOT_VALIDATION_ERROR,
    SLOT_VALIDATION_STAGE,
    TEMPLATE_LOAD_ERROR,
)
from a2a_t.server.prompt_compliance.result import PromptComplianceResult


class PromptComplianceOrchestrator:
    """Coordinate prompt compliance validation flow on the server side."""

    def __init__(
        self,
        *,
        guardrail: SafetyGuardrail,
        template_loader: TemplateLoader,
        slot_schema_loader: SlotSchemaLoader,
        prompt_resource_loader: PromptResourceLoader,
        extractor: SlotExtractor,
        validator: SlotValidator,
    ) -> None:
        self._guardrail = guardrail
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
        try:
            guardrail_result = self._guardrail.check(processed_prompt_text, request_metadata)
        except GuardrailExecutionError as error:
            return self._error_result(
                stage=GUARDRAIL_STAGE,
                error_code=GUARDRAIL_EXECUTION_ERROR,
                error_message=str(error),
            )
        except Exception as error:
            return self._error_result(
                stage=GUARDRAIL_STAGE,
                error_code=GUARDRAIL_EXECUTION_ERROR,
                error_message=str(error),
            )
        if not guardrail_result.passed:
            return self._error_result(
                stage=GUARDRAIL_STAGE,
                error_code=guardrail_result.error_code or GUARDRAIL_REJECTED,
                error_message=guardrail_result.error_message or "Guardrail rejected the processed prompt.",
            )

        try:
            reference = self._parse_reference(processed_prompt_text)
        except TaskPromptFormatError as error:
            return self._error_result(PROMPT_PARSE_STAGE, PROCESSED_PROMPT_PARSE_ERROR, str(error))

        try:
            template_text = self._template_loader.load(reference=reference)
        except PromptResourceNotFoundError as error:
            return self._error_result(GENERATION_STAGE, TEMPLATE_LOAD_ERROR, str(error))
        except PromptResourceParseError as error:
            return self._error_result(GENERATION_STAGE, PROMPT_RESOURCE_PARSE_ERROR, str(error))
        except PromptSourceError as error:
            return self._error_result(GENERATION_STAGE, PROMPT_RESOURCE_ACCESS_ERROR, str(error))

        try:
            slot_schema = self._slot_schema_loader.load(reference=reference)
        except PromptResourceNotFoundError as error:
            return self._error_result(GENERATION_STAGE, SLOT_SCHEMA_LOAD_ERROR, str(error))
        except PromptResourceParseError as error:
            return self._error_result(GENERATION_STAGE, PROMPT_RESOURCE_PARSE_ERROR, str(error))
        except PromptSourceError as error:
            return self._error_result(GENERATION_STAGE, PROMPT_RESOURCE_ACCESS_ERROR, str(error))

        try:
            slot_prompts = self._prompt_resource_loader.load(
                analysis_action="slot_extraction",
                version=reference.version,
                language=reference.language,
            )
        except PromptResourceNotFoundError as error:
            return self._error_result(GENERATION_STAGE, PROMPT_RESOURCE_LOAD_ERROR, str(error))
        except PromptResourceParseError as error:
            return self._error_result(GENERATION_STAGE, PROMPT_RESOURCE_PARSE_ERROR, str(error))
        except PromptSourceError as error:
            return self._error_result(GENERATION_STAGE, PROMPT_RESOURCE_ACCESS_ERROR, str(error))

        try:
            extraction_result = self._extractor.extract(
                normalized_input=processed_prompt_text,
                reference=reference,
                template_text=template_text,
                slot_schema=slot_schema,
                system_prompt=slot_prompts.system_prompt,
                user_prompt=slot_prompts.user_prompt,
            )
        except PromptAnalysisError as error:
            return self._error_result(SLOT_EXTRACTION_STAGE, SLOT_EXTRACTION_ERROR, str(error))
        except Exception as error:
            return self._error_result(SLOT_EXTRACTION_STAGE, SLOT_EXTRACTION_ERROR, str(error))

        validation_result: SlotValidationResult = self._validator.validate(
            slots=extraction_result.slots,
            slot_errors=extraction_result.slot_errors,
            slot_schema=slot_schema,
        )
        if not validation_result.passed:
            error_message = self._aggregate_slot_errors(validation_result)
            slot_errors = list(validation_result.slot_errors)
            return PromptComplianceResult(
                passed=False,
                stage=SLOT_VALIDATION_STAGE,
                extracted_slots=extraction_result.slots,
                error_code=SLOT_VALIDATION_ERROR,
                error_message=error_message,
                slot_errors=slot_errors,
                need_negotiation=self._is_negotiable_slot_failure(slot_errors),
                negotiation_input=self._build_negotiation_input(
                    error_message=error_message,
                    slot_errors=slot_errors,
                ),
            )

        return PromptComplianceResult(
            passed=True,
            stage=PASSED_STAGE,
            extracted_slots=extraction_result.slots,
        )

    @staticmethod
    def _parse_reference(processed_prompt_text: str) -> PromptReference:
        return parse_task_prompt_metadata(processed_prompt_text).to_prompt_reference()

    def _aggregate_slot_errors(self, validation_result: SlotValidationResult) -> str:
        messages = [slot_error.message for slot_error in validation_result.slot_errors if slot_error.message]
        return "; ".join(messages) if messages else "Slot validation failed."

    def _is_negotiable_slot_failure(self, slot_errors: list[SlotValidationError]) -> bool:
        if not slot_errors:
            return False
        return all(slot_error.code in {MISSING_INPUT, INVALID_VALUE} for slot_error in slot_errors)

    def _build_negotiation_input(
        self,
        *,
        error_message: str,
        slot_errors: list[SlotValidationError],
    ) -> dict[str, object] | None:
        if not self._is_negotiable_slot_failure(slot_errors):
            return None

        missing_fields: list[str] = []
        invalid_fields: list[dict[str, str]] = []
        for slot_error in slot_errors:
            if slot_error.code == INVALID_VALUE:
                invalid_fields.append(
                    {
                        "name": slot_error.slot_name,
                        "reason": slot_error.message,
                    }
                )
                continue
            missing_fields.append(slot_error.slot_name)

        return {
            "type": "information",
            "contentText": error_message,
            "facts": {
                "missingFields": missing_fields,
                "invalidFields": invalid_fields,
            },
        }

    @staticmethod
    def _error_result(stage: str, error_code: str, error_message: str) -> PromptComplianceResult:
        return PromptComplianceResult(
            passed=False,
            stage=stage,
            error_code=error_code,
            error_message=error_message,
        )
