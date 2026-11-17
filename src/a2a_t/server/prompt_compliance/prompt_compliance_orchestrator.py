# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from __future__ import annotations

import logging
from typing import Any

from a2a_t.common.prompt_resources import PromptResourceAccess
from a2a_t.common.prompt_resources.models import PromptMessages, slot_schema_from_json_schema
from a2a_t.core.errors.catalog import ErrorCatalog, by_code
from a2a_t.core.errors.exceptions import A2ATBusinessError, A2ATError
from a2a_t.core.errors.input_limit import InputLimitConfig
from a2a_t.core.errors.messages import render as render_error_message
from a2a_t.llm.errors import is_response_contract_violation
from a2a_t.prompt.analysis import ScenarioResolutionOrchestrator, SlotExtractor
from a2a_t.prompt.analysis.errors import PromptAnalysisError
from a2a_t.prompt.common.models import PromptReference
from a2a_t.prompt.validation.json_schema_slot_validator import JsonSchemaSlotValidator
from a2a_t.prompt.validation.models import SlotValidationResult
from a2a_t.server.prompt_compliance.constants import (
    INPUT_GATE_STAGE,
    PREPARATION_STAGE,
    PROMPT_PARSE_STAGE,
    SLOT_EXTRACTION_STAGE,
    SLOT_VALIDATION_STAGE,
)
from a2a_t.server.prompt_compliance.models import (
    PromptComplianceFailure,
    PromptComplianceResult,
    SemanticValidationResult,
)
from a2a_t.server.prompt_compliance.semantic_validator import SemanticSlotValidator

_LOGGER = logging.getLogger(__name__)

#: Slot-code domain prefix accepted when resolving a reported slot error code.
_SLOT_CODE_DOMAIN = "slot."

#: Legacy slot-extraction error code for a required slot that could not be extracted (Java keeps
#: the same legacy spelling and maps it at the boundary).
_LEGACY_CODE_MISSING_INPUT = "missing_input"

#: Legacy slot-extraction error code for a value violating a closed constraint.
_LEGACY_CODE_INVALID_VALUE = "invalid_value"

#: Step label reported in ``llm.response_invalid`` when the slot-extraction step fails.
_STEP_SLOT_EXTRACTION = "slot extraction"

#: Analysis action whose system/user prompts drive slot extraction.
_SLOT_EXTRACTION_ACTION = "slot_extraction"


def resolve_slot_error_code(code: str) -> ErrorCatalog:
    """Map one reported slot error code to its catalog entry.

    The legacy extraction codes ``missing_input``/``invalid_value`` map to their catalog
    counterparts, an in-catalog slot-domain code is kept as-is, and anything else falls back to
    the closed ``slot.rule_violation`` code with a warning (unknown codes are never surfaced raw,
    D4/plan 7.3).

    Args:
        code: slot error code reported by the extraction or validation step

    Returns:
        the catalog entry carrying the mapped code
    """
    if code in {_LEGACY_CODE_MISSING_INPUT, ErrorCatalog.SLOT_NOT_PROVIDED.value}:
        return ErrorCatalog.SLOT_NOT_PROVIDED
    if code in {_LEGACY_CODE_INVALID_VALUE, ErrorCatalog.SLOT_CONSTRAINT_VIOLATED.value}:
        return ErrorCatalog.SLOT_CONSTRAINT_VIOLATED
    try:
        entry = by_code(code)
    except KeyError:
        entry = None
    if entry is not None and entry.value.startswith(_SLOT_CODE_DOMAIN):
        return entry
    _LOGGER.warning(
        "Unknown slot validation error code '%s' falls back to '%s'.",
        code,
        ErrorCatalog.SLOT_RULE_VIOLATION.value,
    )
    return ErrorCatalog.SLOT_RULE_VIOLATION


class PromptComplianceOrchestrator:
    """Coordinate prompt compliance validation flow on the server side.

    Every resource — template text, slot schema and the slot-extraction instruction prompts — is
    loaded through the shared resource access layer (D31): templates and slot schemas follow the
    configured source routing, the instruction prompts are always the packaged SDK contract.
    """

    def __init__(
        self,
        *,
        scenario_resolver: ScenarioResolutionOrchestrator,
        resource_access: PromptResourceAccess,
        extractor: SlotExtractor,
        validator: JsonSchemaSlotValidator,
        semantic_validator: SemanticSlotValidator | None = None,
        input_limit: InputLimitConfig | None = None,
        language: str | None = None,
        logger: Any | None = None,
    ) -> None:
        self._scenario_resolver = scenario_resolver
        self._resource_access = resource_access
        self._extractor = extractor
        self._validator = validator
        self._semantic_validator = semantic_validator
        self._input_limit = input_limit if input_limit is not None else InputLimitConfig()
        self._language = language
        self._logger = logger if logger is not None else _LOGGER

    def check(
        self,
        *,
        processed_prompt_text: str,
    ) -> PromptComplianceResult:
        """Validate a processed task prompt and derive follow-up negotiation hints when needed."""
        self._log_info("prompt_compliance_started")
        if self._input_limit.is_too_long(processed_prompt_text):
            return self._catalog_failure(
                entry=ErrorCatalog.INPUT_TEXT_TOO_LONG,
                facts=self._input_limit.too_long_facts(processed_prompt_text),
                stage=INPUT_GATE_STAGE,
            )
        scenario_resolution = self._scenario_resolver.resolve(processed_prompt_text)
        if not scenario_resolution.success or scenario_resolution.reference is None:
            failure = scenario_resolution.failure
            if failure is None:
                return self._catalog_failure(
                    entry=ErrorCatalog.SCENARIO_NOT_MATCHED,
                    facts={"reason": "Scenario resolution failed."},
                    stage=PROMPT_PARSE_STAGE,
                )
            # The resolver already rendered the catalog message for its own failure mode.
            return self._finalize_result(
                self._error_result(
                    stage=failure.stage,
                    error_code=failure.code,
                    error_message=failure.message,
                )
            )
        reference = scenario_resolution.reference
        self._log_info(
            "prompt_compliance_scenario_resolved scenario_code=%s language=%s",
            reference.scenario_code,
            reference.language,
        )

        try:
            template_text = self._resource_access.template_text(reference.scenario_code, reference.language)
        except A2ATBusinessError as error:
            return self._catalog_failure(
                entry=error.code,
                facts=error.facts,
                stage=PREPARATION_STAGE,
            )
        except A2ATError:
            return self._resource_read_failure(reference)

        try:
            slot_json_schema = self._resource_access.slot_schema(reference.scenario_code, reference.language)
            slot_schema = slot_schema_from_json_schema(
                slot_json_schema,
                scenario_code=reference.scenario_code,
            )
        except A2ATBusinessError as error:
            return self._catalog_failure(
                entry=error.code,
                facts=error.facts,
                stage=PREPARATION_STAGE,
            )
        except A2ATError:
            return self._resource_read_failure(reference)

        try:
            slot_prompts = self._load_slot_extraction_prompts(reference)
        except A2ATError:
            return self._resource_read_failure(reference)

        try:
            extraction_result = self._extractor.extract(
                normalized_input=processed_prompt_text,
                reference=reference,
                template_text=template_text,
                slot_schema=slot_schema,
                system_prompt=slot_prompts.system_prompt,
                user_prompt=slot_prompts.user_prompt,
            )
        except PromptAnalysisError:
            return self._catalog_failure(
                entry=ErrorCatalog.LLM_RESPONSE_INVALID,
                facts={"step": _STEP_SLOT_EXTRACTION},
                stage=SLOT_EXTRACTION_STAGE,
            )
        except Exception as error:
            if is_response_contract_violation(error):
                return self._catalog_failure(
                    entry=ErrorCatalog.LLM_RESPONSE_INVALID,
                    facts={"step": _STEP_SLOT_EXTRACTION},
                    stage=SLOT_EXTRACTION_STAGE,
                )
            return self._catalog_failure(
                entry=ErrorCatalog.LLM_INVOCATION_FAILED,
                facts={"reason": str(error)},
                stage=SLOT_EXTRACTION_STAGE,
            )

        validation_result: SlotValidationResult = self._validator.validate(
            slots=extraction_result.slots,
            slot_errors=extraction_result.slot_errors,
            slot_json_schema=slot_json_schema,
        )
        if not validation_result.passed:
            error_message = self._aggregate_slot_errors(validation_result)
            first_code = validation_result.slot_errors[0].code if validation_result.slot_errors else ""
            return self._finalize_result(
                self._error_result(
                    stage=SLOT_VALIDATION_STAGE,
                    error_code=resolve_slot_error_code(first_code).value,
                    error_message=error_message,
                )
            )

        if self._semantic_validator is not None:
            semantic_result: SemanticValidationResult = self._semantic_validator.validate(
                language=reference.language,
                slot_json_schema=slot_json_schema,
                extracted_slots=extraction_result.slots,
            )
            if not semantic_result.passed:
                error_message = self._aggregate_semantic_errors(semantic_result)
                first_code = semantic_result.errors[0].code if semantic_result.errors else ""
                return self._finalize_result(
                    self._error_result(
                        stage=SLOT_VALIDATION_STAGE,
                        error_code=resolve_slot_error_code(first_code).value,
                        error_message=error_message,
                    )
                )

        return self._finalize_result(PromptComplianceResult(success=True))

    def _load_slot_extraction_prompts(self, reference: PromptReference) -> PromptMessages:
        """Load the packaged slot-extraction instruction prompts for one reference."""
        return PromptMessages(
            system_prompt=self._resource_access.load_prompt(_SLOT_EXTRACTION_ACTION, reference.language, "system.md"),
            user_prompt=self._resource_access.load_prompt(_SLOT_EXTRACTION_ACTION, reference.language, "user.md"),
        )

    def _resource_read_failure(
        self,
        reference: PromptReference,
    ) -> PromptComplianceResult:
        """Build the ``infra.resource_read_failed`` failure for one resource-loading error.

        The resource path fact identifies the template addressed by the resolved reference (Java
        parity: the orchestrator catch points report the scenario resource, not the raw reader
        error).
        """
        return self._catalog_failure(
            entry=ErrorCatalog.INFRA_RESOURCE_READ_FAILED,
            facts={"resource_path": reference.scenario_code},
            stage=PREPARATION_STAGE,
        )

    def _aggregate_slot_errors(self, validation_result: SlotValidationResult) -> str:
        """Collapse slot validation messages into the single message exposed to callers."""
        messages = [slot_error.message for slot_error in validation_result.slot_errors if slot_error.message]
        return "; ".join(messages) if messages else "Slot validation failed."

    def _aggregate_semantic_errors(self, validation_result: SemanticValidationResult) -> str:
        messages = [error.message for error in validation_result.errors if error.message]
        return "; ".join(messages) if messages else "Slot semantic validation failed."

    def _catalog_failure(
        self,
        *,
        entry: ErrorCatalog,
        facts: dict[str, str],
        stage: str,
    ) -> PromptComplianceResult:
        """Build a compliance failure whose message is rendered from the code's template."""
        return self._error_result(
            stage=stage,
            error_code=entry.value,
            error_message=render_error_message(entry, facts, self._language),
        )

    def _error_result(self, stage: str, error_code: str, error_message: str) -> PromptComplianceResult:
        """Build a standardized compliance failure result."""
        return PromptComplianceResult(
            success=False,
            failure=PromptComplianceFailure(
                code=error_code,
                message=error_message,
                stage=stage,
            ),
        )

    def _finalize_result(self, result: PromptComplianceResult) -> PromptComplianceResult:
        """Emit completion logs and return the final compliance result unchanged."""
        failure_stage = result.failure.stage if result.failure is not None else None
        failure_code = result.failure.code if result.failure is not None else None
        self._log_info(
            "prompt_compliance_completed success=%s stage=%s code=%s",
            result.success,
            failure_stage,
            failure_code,
        )
        return result

    def _log_info(self, message: str, *args: object) -> None:
        """Write an info log through the configured logger."""
        self._logger.info(message, *args)
