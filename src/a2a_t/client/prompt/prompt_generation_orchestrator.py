from __future__ import annotations

import logging
from typing import Any

from a2a_t.prompt.analysis.errors import PromptAnalysisError
from a2a_t.prompt.validation.constants import INVALID_VALUE
from a2a_t.prompt.resources.errors import PromptResourceNotFoundError
from a2a_t.prompt.resources.registry import PromptResourceRegistry
from a2a_t.prompt.validation.models import SlotValidationError

from .generation_constants import (
    GENERATION_STAGE,
    INVALID_FIELD_VALUE,
    INVALID_LLM_OUTPUT,
    LLM_EXECUTION_FAILED,
    MISSING_REQUIRED_FIELDS,
    PROMPT_NOT_FOUND,
    RENDER_FAILED,
    RENDER_STAGE,
    SCENARIO_PARSE_FAILED,
    SCENARIO_STAGE,
    SLOT_SCHEMA_NOT_FOUND,
    TEMPLATE_NOT_FOUND,
    VALIDATION_STAGE,
)
from a2a_t.prompt.common.models import PromptReference
from .input_normalizer import InputNormalizer
from .models import PromptGenerationFailure, PromptGenerationResult, ValidationResult
from .a2a_t_task_prompt_renderer import A2ATTaskPromptRenderError, A2ATTaskPromptRenderer


logger = logging.getLogger(__name__)


class PromptGenerationOrchestrator:
    def __init__(
        self,
        *,
        config: Any,
        scenario_loader: Any,
        prompt_resource_loader: Any,
        template_loader: Any,
        slot_schema_loader: Any,
        scenario_recognizer: Any,
        slot_extractor: Any,
        slot_validator: Any,
        resource_registry: PromptResourceRegistry | None = None,
        input_normalizer: InputNormalizer | None = None,
        renderer: A2ATTaskPromptRenderer | None = None,
        logger: Any | None = None,
    ) -> None:
        self._config = config
        self._scenario_loader = scenario_loader
        self._prompt_resource_loader = prompt_resource_loader
        self._template_loader = template_loader
        self._slot_schema_loader = slot_schema_loader
        self._scenario_recognizer = scenario_recognizer
        self._slot_extractor = slot_extractor
        self._slot_validator = slot_validator
        self._resource_registry = resource_registry or PromptResourceRegistry(
            scenario_loader=scenario_loader,
            prompt_resource_loader=prompt_resource_loader,
            template_loader=template_loader,
            slot_schema_loader=slot_schema_loader,
        )
        self._input_normalizer = input_normalizer or InputNormalizer()
        self._renderer = renderer or A2ATTaskPromptRenderer()
        self._logger = logger or globals()["logger"]

    def generate(self, user_input: str | dict[str, object]) -> PromptGenerationResult:
        self._log_info("prompt_generation_started")
        if self._is_debug_enabled():
            self._log_debug("prompt_generation_raw_user_input raw_user_input=%s", user_input)
        normalized_input = self._input_normalizer.normalize(user_input)
        language = getattr(self._config, "language", None) or "en-US"
        version = getattr(self._config, "prompt_resource_version", None) or "0.0.1"
        self._log_info(
            "prompt_generation_input_normalized input_kind=%s requested_language=%s version=%s",
            normalized_input.input_kind,
            language,
            version,
        )

        try:
            resolved_language, scenarios, scenario_prompts = self._load_scenario_resources(version=version, language=language)
        except _PromptGenerationResourceFailure as error:
            return self._failure_result(
                code=error.code,
                message=error.message,
                stage=error.stage,
                language=language,
                input_kind=normalized_input.input_kind,
            )

        try:
            scenario_result = self._scenario_recognizer.recognize(
                normalized_input=normalized_input.normalized_input,
                scenarios=scenarios,
                language=resolved_language,
                system_prompt=scenario_prompts.system_prompt,
                user_prompt=scenario_prompts.user_prompt,
            )
        except PromptAnalysisError as error:
            return self._failure_result(
                code=INVALID_LLM_OUTPUT,
                message=str(error),
                stage=SCENARIO_STAGE,
                language=resolved_language,
                input_kind=normalized_input.input_kind,
            )
        except Exception as error:
            return self._failure_result(
                code=LLM_EXECUTION_FAILED,
                message=str(error),
                stage=SCENARIO_STAGE,
                language=resolved_language,
                input_kind=normalized_input.input_kind,
            )
        self._log_debug_if_available(
            "prompt_generation_scenario_raw_output scenario_raw_output=%s",
            self._scenario_recognizer,
        )
        if not scenario_result.matched or not scenario_result.scenario_code:
            return self._failure_result(
                code=SCENARIO_PARSE_FAILED,
                message=scenario_result.error_message or "Scenario recognition failed.",
                stage=SCENARIO_STAGE,
                language=resolved_language,
                input_kind=normalized_input.input_kind,
            )

        scenario_code = scenario_result.scenario_code
        if not self._is_supported_scenario_code(scenarios=scenarios, scenario_code=scenario_code):
            return self._failure_result(
                code=INVALID_LLM_OUTPUT,
                message=f"Scenario recognition returned unsupported scenario_code: {scenario_code}",
                stage=SCENARIO_STAGE,
                language=resolved_language,
                input_kind=normalized_input.input_kind,
            )
        self._log_info(
            "prompt_generation_scenario_recognized scenario_code=%s language=%s",
            scenario_code,
            resolved_language,
        )
        reference = PromptReference(scenario_code=scenario_code, version=version, language=resolved_language)
        try:
            resolved_language, template_text, slot_schema, slot_prompts = self._load_generation_resources(
                reference=reference,
            )
            reference = PromptReference(scenario_code=scenario_code, version=version, language=resolved_language)
        except _PromptGenerationResourceFailure as error:
            return self._finalize_result(
                PromptGenerationResult(
                success=False,
                prompt_text=None,
                scenario_code=scenario_code,
                language=resolved_language,
                input_kind=normalized_input.input_kind,
                slots={},
                validation=ValidationResult(passed=False, slot_errors=[]),
                failure=PromptGenerationFailure(code=error.code, message=error.message, stage=error.stage),
                )
            )

        try:
            extraction_result = self._slot_extractor.extract(
                normalized_input=normalized_input.normalized_input,
                reference=reference,
                template_text=template_text,
                slot_schema=slot_schema,
                system_prompt=slot_prompts.system_prompt,
                user_prompt=slot_prompts.user_prompt,
            )
        except PromptAnalysisError as error:
            return self._finalize_result(
                PromptGenerationResult(
                success=False,
                prompt_text=None,
                scenario_code=scenario_code,
                language=resolved_language,
                input_kind=normalized_input.input_kind,
                slots={},
                validation=ValidationResult(passed=False, slot_errors=[]),
                failure=PromptGenerationFailure(code=INVALID_LLM_OUTPUT, message=str(error), stage=GENERATION_STAGE),
                )
            )
        except Exception as error:
            return self._finalize_result(
                PromptGenerationResult(
                success=False,
                prompt_text=None,
                scenario_code=scenario_code,
                language=resolved_language,
                input_kind=normalized_input.input_kind,
                slots={},
                validation=ValidationResult(passed=False, slot_errors=[]),
                failure=PromptGenerationFailure(code=LLM_EXECUTION_FAILED, message=str(error), stage=GENERATION_STAGE),
                )
            )
        self._log_debug_if_available(
            "prompt_generation_slot_raw_output slot_raw_output=%s",
            self._slot_extractor,
        )
        shared_validation = self._slot_validator.validate(
            slots=extraction_result.slots,
            slot_errors=extraction_result.slot_errors,
            slot_schema=slot_schema,
        )
        validation = self._build_validation_result(shared_validation.slot_errors)
        if not shared_validation.passed:
            failure_code = INVALID_FIELD_VALUE if self._contains_invalid_value(shared_validation.slot_errors) else MISSING_REQUIRED_FIELDS
            failure_message = "Slot validation failed."
            return self._finalize_result(
                PromptGenerationResult(
                success=False,
                prompt_text=None,
                scenario_code=scenario_code,
                language=resolved_language,
                input_kind=normalized_input.input_kind,
                slots=extraction_result.slots,
                validation=validation,
                failure=PromptGenerationFailure(
                    code=failure_code,
                    message=failure_message,
                    stage=VALIDATION_STAGE,
                ),
                )
            )

        try:
            prompt_text = self._renderer.render(
                template_text=template_text,
                slots=extraction_result.slots,
                scenario_code=scenario_code,
                language=resolved_language,
                version=version,
                description=self._resolve_scenario_description(scenarios, scenario_code),
            )
        except A2ATTaskPromptRenderError as error:
            return self._finalize_result(
                PromptGenerationResult(
                success=False,
                prompt_text=None,
                scenario_code=scenario_code,
                language=resolved_language,
                input_kind=normalized_input.input_kind,
                slots=extraction_result.slots,
                validation=validation,
                failure=PromptGenerationFailure(
                    code=RENDER_FAILED,
                    message=str(error),
                    stage=RENDER_STAGE,
                ),
                )
            )

        return self._finalize_result(
            PromptGenerationResult(
            success=True,
            prompt_text=prompt_text,
            scenario_code=scenario_code,
            language=resolved_language,
            input_kind=normalized_input.input_kind,
            slots=extraction_result.slots,
            validation=validation,
            failure=None,
            )
        )

    def _load_scenario_resources(self, *, version: str, language: str) -> tuple[str, Any, Any]:
        try:
            return self._resource_registry.load_scenario_resources(
                version=version,
                language=language,
            )
        except PromptResourceNotFoundError:
            raise _PromptGenerationResourceFailure(
                code=PROMPT_NOT_FOUND,
                message="Scenario recognition prompt resources are missing.",
                stage=SCENARIO_STAGE,
            ) from None

    def _load_generation_resources(
        self,
        *,
        reference: PromptReference,
    ) -> tuple[str, Any, Any, Any]:
        try:
            resolved_reference, template_text, slot_schema, slot_prompts = self._resource_registry.load_generation_resources(
                reference=reference
            )
            return resolved_reference.language, template_text, slot_schema, slot_prompts
        except _PromptGenerationResourceFailure:
            raise
        except PromptResourceNotFoundError as error:
            resource_path = str(error.context.get("path", ""))
            if resource_path.endswith("template.md"):
                raise _PromptGenerationResourceFailure(
                    code=TEMPLATE_NOT_FOUND,
                    message=str(error),
                    stage=GENERATION_STAGE,
                ) from error
            if resource_path.endswith("slot.json"):
                raise _PromptGenerationResourceFailure(
                    code=SLOT_SCHEMA_NOT_FOUND,
                    message=str(error),
                    stage=GENERATION_STAGE,
                ) from error
            raise _PromptGenerationResourceFailure(
                code=PROMPT_NOT_FOUND,
                message=str(error),
                stage=GENERATION_STAGE,
            ) from error

    def _build_validation_result(self, slot_errors: list[SlotValidationError]) -> ValidationResult:
        return ValidationResult(
            passed=not slot_errors,
            slot_errors=list(slot_errors),
        )

    def _contains_invalid_value(self, slot_errors: list[SlotValidationError]) -> bool:
        return any(slot_error.code == INVALID_VALUE for slot_error in slot_errors)

    def _resolve_scenario_description(self, scenarios: list[Any], scenario_code: str) -> str:
        for scenario in scenarios:
            if scenario.scenario_code == scenario_code:
                return scenario.description
        return ""

    def _is_supported_scenario_code(self, *, scenarios: list[Any], scenario_code: str) -> bool:
        return any(scenario.scenario_code == scenario_code for scenario in scenarios)

    def _failure_result(
        self,
        *,
        code: str,
        message: str,
        stage: str,
        language: str,
        input_kind: str,
    ) -> PromptGenerationResult:
        return self._finalize_result(
            PromptGenerationResult(
            success=False,
            prompt_text=None,
            scenario_code=None,
            language=language,
            input_kind=input_kind,
            slots={},
            validation=ValidationResult(passed=False, slot_errors=[]),
            failure=PromptGenerationFailure(code=code, message=message, stage=stage),
            )
        )

    def _finalize_result(self, result: PromptGenerationResult) -> PromptGenerationResult:
        failure_stage = result.failure.stage if result.failure is not None else None
        failure_code = result.failure.code if result.failure is not None else None
        self._log_info(
            "prompt_generation_completed success=%s stage=%s code=%s language=%s scenario_code=%s",
            result.success,
            failure_stage,
            failure_code,
            result.language,
            result.scenario_code,
        )
        return result

    def _log_info(self, message: str, *args: object) -> None:
        self._logger.info(message, *args)

    def _log_debug(self, message: str, *args: object) -> None:
        if self._is_debug_enabled():
            self._logger.debug(message, *args)

    def _log_debug_if_available(self, message: str, source: Any) -> None:
        raw_content = getattr(source, "last_raw_response_content", None)
        if raw_content is not None:
            self._log_debug(message, raw_content)

    def _is_debug_enabled(self) -> bool:
        return bool(getattr(self._config, "prompt_generation_debug", False))


class _PromptGenerationResourceFailure(Exception):
    def __init__(self, *, code: str, message: str, stage: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
