"""Runtime behavior of the server prompt compliance orchestrator on the D31 access layer."""

from __future__ import annotations

from typing import Any

import pytest

from a2a_t.common.prompt_resources.models import ScenarioDefinition
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATBusinessError, A2ATError
from a2a_t.prompt.analysis.models import (
    ScenarioResolutionFailure,
    ScenarioResolutionResult,
    SlotExtractionResult,
)
from a2a_t.prompt.common.models import PromptReference
from a2a_t.prompt.validation.constants import INVALID_VALUE, MISSING_INPUT
from a2a_t.prompt.validation.models import SlotValidationError, SlotValidationResult
from a2a_t.server.prompt_compliance.constants import SLOT_VALIDATION_STAGE
from a2a_t.server.prompt_compliance.models import (
    PromptComplianceFailure,
    PromptComplianceResult,
    SemanticValidationError,
    SemanticValidationResult,
)
from tests.support import FakePromptResourceAccess

PROCESSED_PROMPT = "processed body"

_SCENARIO_RESOLUTION = ScenarioResolutionResult(
    success=True,
    reference=PromptReference(
        scenario_code="ran-energy-saving",
        language="en-US",
    ),
    scenario=ScenarioDefinition(
        scenario_code="ran-energy-saving",
        scenario_name="Energy Saving",
        description="Used for energy saving analysis.",
        example="Analyze site power usage and suggest optimization.",
    ),
)

_SLOT_JSON_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"site": {"type": "string", "minLength": 1}},
    "required": ["site"],
    "additionalProperties": False,
}


class FakeScenarioResolver:
    def __init__(self, result: ScenarioResolutionResult) -> None:
        self._result = result
        self.calls: list[str] = []

    def resolve(self, normalized_input: str) -> ScenarioResolutionResult:
        self.calls.append(normalized_input)
        return self._result


class FakeExtractor:
    def __init__(self, result: SlotExtractionResult) -> None:
        self._result = result
        self.last_reference: PromptReference | None = None
        self.last_kwargs: dict[str, Any] = {}

    def extract(self, **kwargs: object) -> SlotExtractionResult:
        self.last_reference = kwargs.get("reference")  # type: ignore[assignment]
        self.last_kwargs = dict(kwargs)
        return self._result


class FakeValidator:
    def __init__(self, result: SlotValidationResult) -> None:
        self._result = result

    def validate(self, **kwargs: object) -> SlotValidationResult:
        return self._result


class FakeSemanticValidator:
    def __init__(self, passed: bool, message: str = "semantic validation failed") -> None:
        self._passed = passed
        self._message = message
        self.calls: int = 0
        self.last_kwargs: dict[str, object] | None = None

    def validate(self, **kwargs: object) -> SemanticValidationResult:
        self.calls += 1
        self.last_kwargs = dict(kwargs)
        if self._passed:
            return SemanticValidationResult(passed=True, errors=[])
        return SemanticValidationResult(
            passed=False,
            errors=[
                SemanticValidationError(
                    slot_name="site",
                    code=INVALID_VALUE,
                    message=self._message,
                )
            ],
        )


class FakeLogger:
    def __init__(self) -> None:
        self.info_messages: list[tuple[str, tuple[object, ...]]] = []

    def info(self, message: str, *args: object) -> None:
        self.info_messages.append((message, args))


class FalsyLogger(FakeLogger):
    def __bool__(self) -> bool:
        return False


def _build_service(
    *,
    resource_access: FakePromptResourceAccess | None = None,
    scenario_resolver: FakeScenarioResolver | None = None,
    extractor: FakeExtractor | None = None,
    validator: FakeValidator | None = None,
    semantic_validator: FakeSemanticValidator | None = None,
    logger: FakeLogger | None = None,
) -> Any:
    from a2a_t.server.prompt_compliance.prompt_compliance_orchestrator import PromptComplianceOrchestrator

    return PromptComplianceOrchestrator(
        scenario_resolver=scenario_resolver or FakeScenarioResolver(_SCENARIO_RESOLUTION),
        resource_access=resource_access
        or FakePromptResourceAccess(
            template_text="Site: {site}",
            slot_json_schema=_SLOT_JSON_SCHEMA,
            system_prompt="Extract slots.",
            user_prompt="Return slots.",
        ),
        extractor=extractor or FakeExtractor(SlotExtractionResult(slots={"site": "Site A"}, slot_errors=[])),
        validator=validator or FakeValidator(SlotValidationResult(passed=True, slot_errors=[])),
        semantic_validator=semantic_validator or FakeSemanticValidator(passed=True),
        logger=logger,
    )


def test_check_returns_success_result() -> None:
    access = FakePromptResourceAccess(
        template_text="Site: {site}",
        slot_json_schema=_SLOT_JSON_SCHEMA,
        system_prompt="Extract slots.",
        user_prompt="Return slots.",
    )
    extractor = FakeExtractor(SlotExtractionResult(slots={"site": "Site A"}, slot_errors=[]))
    service = _build_service(resource_access=access, extractor=extractor)

    result = service.check(processed_prompt_text=PROCESSED_PROMPT)

    assert access.template_calls == [("ran-energy-saving", "en-US")]
    assert access.slot_schema_calls == [("ran-energy-saving", "en-US")]
    assert access.prompt_calls == [
        ("slot_extraction", "en-US", "system.md"),
        ("slot_extraction", "en-US", "user.md"),
    ]
    assert extractor.last_reference == PromptReference(scenario_code="ran-energy-saving", language="en-US")
    assert extractor.last_kwargs["system_prompt"] == "Extract slots."
    assert extractor.last_kwargs["user_prompt"] == "Return slots."
    assert result == PromptComplianceResult(success=True)


def test_check_returns_slot_validation_error_with_failure_payload() -> None:
    service = _build_service(
        validator=FakeValidator(
            SlotValidationResult(
                passed=False,
                slot_errors=[
                    SlotValidationError(
                        slot_name="site",
                        code="invalid_value",
                        message="Site format is invalid.",
                    )
                ],
            )
        )
    )

    result = service.check(processed_prompt_text=PROCESSED_PROMPT)

    assert result == PromptComplianceResult(
        success=False,
        failure=PromptComplianceFailure(
            code=ErrorCatalog.SLOT_CONSTRAINT_VIOLATED.value,
            message="Site format is invalid.",
            stage=SLOT_VALIDATION_STAGE,
        ),
    )


def test_check_returns_slot_validation_error_for_negotiable_slot_failures() -> None:
    service = _build_service(
        validator=FakeValidator(
            SlotValidationResult(
                passed=False,
                slot_errors=[
                    SlotValidationError(
                        slot_name="site",
                        code=MISSING_INPUT,
                        message="Required slot 'site' is missing.",
                    ),
                    SlotValidationError(
                        slot_name="analysis_target",
                        code=INVALID_VALUE,
                        message="analysis_target is invalid.",
                    ),
                ],
            )
        )
    )

    result = service.check(processed_prompt_text=PROCESSED_PROMPT)

    assert result == PromptComplianceResult(
        success=False,
        failure=PromptComplianceFailure(
            code=ErrorCatalog.SLOT_NOT_PROVIDED.value,
            message="Required slot 'site' is missing.; analysis_target is invalid.",
            stage=SLOT_VALIDATION_STAGE,
        ),
    )


def test_check_skips_semantic_validation_when_schema_fails() -> None:
    semantic_validator = FakeSemanticValidator(passed=True)
    service = _build_service(
        validator=FakeValidator(
            SlotValidationResult(
                passed=False,
                slot_errors=[
                    SlotValidationError(
                        slot_name="site",
                        code=MISSING_INPUT,
                        message="Required slot 'site' is missing.",
                    )
                ],
            )
        ),
        semantic_validator=semantic_validator,
    )

    result = service.check(processed_prompt_text=PROCESSED_PROMPT)

    assert semantic_validator.calls == 0
    assert result.success is False
    assert result.failure is not None
    assert result.failure.code == ErrorCatalog.SLOT_NOT_PROVIDED.value
    assert result.failure.stage == SLOT_VALIDATION_STAGE


def test_check_returns_slot_validation_error_when_semantic_validation_fails() -> None:
    semantic_validator = FakeSemanticValidator(passed=False, message="semantic mismatch for site")
    service = _build_service(semantic_validator=semantic_validator)

    result = service.check(processed_prompt_text=PROCESSED_PROMPT)

    assert semantic_validator.calls == 1
    assert semantic_validator.last_kwargs == {
        "language": "en-US",
        "slot_json_schema": _SLOT_JSON_SCHEMA,
        "extracted_slots": {"site": "Site A"},
    }
    assert result == PromptComplianceResult(
        success=False,
        failure=PromptComplianceFailure(
            code=ErrorCatalog.SLOT_CONSTRAINT_VIOLATED.value,
            message="semantic mismatch for site",
            stage=SLOT_VALIDATION_STAGE,
        ),
    )


def test_check_returns_success_when_schema_and_semantic_validation_pass() -> None:
    semantic_validator = FakeSemanticValidator(passed=True)
    logger = FakeLogger()
    service = _build_service(semantic_validator=semantic_validator, logger=logger)

    result = service.check(processed_prompt_text=PROCESSED_PROMPT)

    assert semantic_validator.calls == 1
    assert result == PromptComplianceResult(success=True)
    messages = [message for message, _ in logger.info_messages]
    assert "prompt_compliance_started" in messages
    assert any(message.startswith("prompt_compliance_scenario_resolved") for message in messages)
    assert any(message.startswith("prompt_compliance_completed") for message in messages)


def test_check_uses_explicit_logger_even_when_logger_is_falsy() -> None:
    logger = FalsyLogger()
    service = _build_service(logger=logger)

    service.check(processed_prompt_text=PROCESSED_PROMPT)

    assert ("prompt_compliance_started", ()) in logger.info_messages


def test_check_logs_failure_stage_and_code() -> None:
    logger = FakeLogger()
    service = _build_service(
        validator=FakeValidator(
            SlotValidationResult(
                passed=False,
                slot_errors=[
                    SlotValidationError(
                        slot_name="site",
                        code=MISSING_INPUT,
                        message="Required slot 'site' is missing.",
                    )
                ],
            )
        ),
        logger=logger,
    )

    result = service.check(processed_prompt_text=PROCESSED_PROMPT)

    assert result.success is False
    assert (
        "prompt_compliance_completed success=%s stage=%s code=%s",
        (False, SLOT_VALIDATION_STAGE, ErrorCatalog.SLOT_NOT_PROVIDED.value),
    ) in logger.info_messages


@pytest.mark.parametrize(
    ("template_failure", "expected_code", "expected_message"),
    [
        (
            A2ATBusinessError(
                ErrorCatalog.TEMPLATE_NOT_FOUND,
                {"template_uri": "ran-energy-saving", "language": "en-US"},
            ),
            ErrorCatalog.TEMPLATE_NOT_FOUND.value,
            "Template 'ran-energy-saving' does not exist",
        ),
        (
            A2ATError("template resource read failed"),
            ErrorCatalog.INFRA_RESOURCE_READ_FAILED.value,
            "Failed to read resource 'ran-energy-saving'",
        ),
    ],
    ids=["missing-template", "template-read-failure"],
)
def test_check_returns_template_failures_from_the_access_layer(
    template_failure: Exception,
    expected_code: str,
    expected_message: str,
) -> None:
    service = _build_service(resource_access=FakePromptResourceAccess(template_text=template_failure))

    result = service.check(processed_prompt_text=PROCESSED_PROMPT)

    assert result == PromptComplianceResult(
        success=False,
        failure=PromptComplianceFailure(
            code=expected_code,
            message=expected_message,
            stage="preparation",
        ),
    )


@pytest.mark.parametrize(
    ("slot_schema_failure", "expected_code", "expected_message"),
    [
        (
            A2ATBusinessError(
                ErrorCatalog.SLOT_SCHEMA_NOT_FOUND,
                {"template_uri": "ran-energy-saving", "language": "en-US"},
            ),
            ErrorCatalog.SLOT_SCHEMA_NOT_FOUND.value,
            "Template 'ran-energy-saving' is missing its slot schema (language 'en-US')",
        ),
        (
            A2ATError("slot schema resource read failed"),
            ErrorCatalog.INFRA_RESOURCE_READ_FAILED.value,
            "Failed to read resource 'ran-energy-saving'",
        ),
    ],
    ids=["missing-slot-schema", "slot-schema-read-failure"],
)
def test_check_returns_slot_schema_failures_from_the_access_layer(
    slot_schema_failure: Exception,
    expected_code: str,
    expected_message: str,
) -> None:
    service = _build_service(resource_access=FakePromptResourceAccess(slot_json_schema=slot_schema_failure))

    result = service.check(processed_prompt_text=PROCESSED_PROMPT)

    assert result == PromptComplianceResult(
        success=False,
        failure=PromptComplianceFailure(
            code=expected_code,
            message=expected_message,
            stage="preparation",
        ),
    )


@pytest.mark.parametrize(
    "prompt_failure",
    [
        A2ATError("Failed to read resource 'prompt_resources/prompts/slot_extraction/en-US/system.md'."),
        A2ATError("prompt resource path escapes local root"),
    ],
    ids=["missing-prompts", "prompt-read-failure"],
)
def test_check_returns_preparation_error_when_slot_prompts_cannot_be_loaded(prompt_failure: Exception) -> None:
    service = _build_service(resource_access=FakePromptResourceAccess(system_prompt=prompt_failure))

    result = service.check(processed_prompt_text=PROCESSED_PROMPT)

    assert result == PromptComplianceResult(
        success=False,
        failure=PromptComplianceFailure(
            code=ErrorCatalog.INFRA_RESOURCE_READ_FAILED.value,
            message="Failed to read resource 'ran-energy-saving'",
            stage="preparation",
        ),
    )


def test_check_returns_prompt_parse_error_when_scenario_resolution_fails() -> None:
    service = _build_service(
        scenario_resolver=FakeScenarioResolver(
            ScenarioResolutionResult(
                success=False,
                failure=ScenarioResolutionFailure(
                    code=ErrorCatalog.SCENARIO_NOT_MATCHED.value,
                    message="The input does not match any known scenario: No matching scenario.",
                    stage="prompt_parse",
                ),
            )
        ),
    )

    result = service.check(processed_prompt_text="natural language prompt")

    assert result == PromptComplianceResult(
        success=False,
        failure=PromptComplianceFailure(
            code=ErrorCatalog.SCENARIO_NOT_MATCHED.value,
            message="The input does not match any known scenario: No matching scenario.",
            stage="prompt_parse",
        ),
    )


def test_check_returns_preparation_error_when_scenario_resources_cannot_be_resolved() -> None:
    service = _build_service(
        scenario_resolver=FakeScenarioResolver(
            ScenarioResolutionResult(
                success=False,
                failure=ScenarioResolutionFailure(
                    code=ErrorCatalog.TEMPLATE_LOAD_FAILED.value,
                    message="Failed to read template resource 'scenario resources are invalid'",
                    stage="preparation",
                ),
            )
        ),
    )

    result = service.check(processed_prompt_text="natural language prompt")

    assert result == PromptComplianceResult(
        success=False,
        failure=PromptComplianceFailure(
            code=ErrorCatalog.TEMPLATE_LOAD_FAILED.value,
            message="Failed to read template resource 'scenario resources are invalid'",
            stage="preparation",
        ),
    )
