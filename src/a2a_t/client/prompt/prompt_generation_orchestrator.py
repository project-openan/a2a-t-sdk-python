from __future__ import annotations

from typing import Any

from a2a_t.prompt.validation.models import SlotValidationError

from .constants import (
    GENERATION_STAGE,
    INVALID_FIELD_VALUE,
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
from .input_normalizer import InputNormalizer
from .models import PromptGenerationFailure, PromptGenerationResult, ScenarioResolution, SlotError, ValidationResult
from .renderer import PromptRenderError, PromptRenderer


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
        input_normalizer: InputNormalizer | None = None,
        renderer: PromptRenderer | None = None,
    ) -> None:
        self._config = config
        self._scenario_loader = scenario_loader
        self._prompt_resource_loader = prompt_resource_loader
        self._template_loader = template_loader
        self._slot_schema_loader = slot_schema_loader
        self._scenario_recognizer = scenario_recognizer
        self._slot_extractor = slot_extractor
        self._slot_validator = slot_validator
        self._input_normalizer = input_normalizer or InputNormalizer()
        self._renderer = renderer or PromptRenderer()

    def generate(self, user_input: str | dict[str, object]) -> PromptGenerationResult:
        normalized_input = self._input_normalizer.normalize(user_input)
        language = getattr(self._config, "language", None) or "en-US"
        version = getattr(self._config, "prompt_resource_version", None) or "0.0.1"

        scenarios = self._scenario_loader.load(version=version, language=language)
        scenario_prompts = self._prompt_resource_loader.load(
            analysis_action="scenario_recognition",
            version=version,
            language=language,
        )
        scenario_result = self._scenario_recognizer.recognize(
            normalized_input=normalized_input.normalized_input,
            scenarios=scenarios,
            language=language,
            system_prompt=scenario_prompts.system_prompt,
            user_prompt=scenario_prompts.user_prompt,
        )
        if not scenario_result.matched or not scenario_result.scenario_code:
            return self._failure_result(
                code=SCENARIO_PARSE_FAILED,
                message=scenario_result.error_message or "Scenario recognition failed.",
                stage=SCENARIO_STAGE,
                language=language,
                input_kind=normalized_input.input_kind,
            )

        scenario_code = scenario_result.scenario_code
        template_text = self._template_loader.load(scenario_code=scenario_code, version=version, language=language)
        slot_schema = self._slot_schema_loader.load(scenario_code=scenario_code, version=version, language=language)
        slot_prompts = self._prompt_resource_loader.load(
            analysis_action="slot_extraction",
            version=version,
            language=language,
        )
        extraction_result = self._slot_extractor.extract(
            normalized_input=normalized_input.normalized_input,
            scenario_code=scenario_code,
            version=version,
            language=language,
            template_text=template_text,
            slot_schema=slot_schema,
            system_prompt=slot_prompts.system_prompt,
            user_prompt=slot_prompts.user_prompt,
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
            return PromptGenerationResult(
                success=False,
                prompt_text=None,
                scenario=ScenarioResolution(code=scenario_code),
                language=language,
                input_kind=normalized_input.input_kind,
                slots=extraction_result.slots,
                validation=validation,
                failure=PromptGenerationFailure(
                    code=failure_code,
                    message=failure_message,
                    stage=VALIDATION_STAGE,
                ),
            )

        try:
            prompt_text = self._renderer.render(
                template_text=template_text,
                slots=extraction_result.slots,
                scenario_code=scenario_code,
                language=language,
                version=version,
                description=self._resolve_scenario_description(scenarios, scenario_code),
            )
        except PromptRenderError as error:
            return PromptGenerationResult(
                success=False,
                prompt_text=None,
                scenario=ScenarioResolution(code=scenario_code),
                language=language,
                input_kind=normalized_input.input_kind,
                slots=extraction_result.slots,
                validation=validation,
                failure=PromptGenerationFailure(
                    code=RENDER_FAILED,
                    message=str(error),
                    stage=RENDER_STAGE,
                ),
            )

        return PromptGenerationResult(
            success=True,
            prompt_text=prompt_text,
            scenario=ScenarioResolution(code=scenario_code),
            language=language,
            input_kind=normalized_input.input_kind,
            slots=extraction_result.slots,
            validation=validation,
            failure=None,
        )

    def _build_validation_result(self, slot_errors: list[SlotValidationError]) -> ValidationResult:
        client_slot_errors = [
            SlotError(slot_name=slot_error.slot_name, code=slot_error.code, message=slot_error.message)
            for slot_error in slot_errors
        ]
        missing_required_fields = [slot_error.slot_name for slot_error in slot_errors if slot_error.code == "missing_input"]
        return ValidationResult(
            passed=not client_slot_errors,
            missing_required_fields=missing_required_fields,
            slot_errors=client_slot_errors,
        )

    def _contains_invalid_value(self, slot_errors: list[SlotValidationError]) -> bool:
        return any(slot_error.code == "invalid_value" for slot_error in slot_errors)

    def _resolve_scenario_description(self, scenarios: list[Any], scenario_code: str) -> str:
        for scenario in scenarios:
            if scenario.scenario_code == scenario_code:
                return scenario.description
        return ""

    def _failure_result(
        self,
        *,
        code: str,
        message: str,
        stage: str,
        language: str,
        input_kind: str,
    ) -> PromptGenerationResult:
        return PromptGenerationResult(
            success=False,
            prompt_text=None,
            scenario=None,
            language=language,
            input_kind=input_kind,
            slots={},
            validation=ValidationResult(passed=False, missing_required_fields=[], slot_errors=[]),
            failure=PromptGenerationFailure(code=code, message=message, stage=stage),
        )
