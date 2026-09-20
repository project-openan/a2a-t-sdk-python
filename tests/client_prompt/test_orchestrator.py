from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.client.prompt_generation.generation_constants import (
    GENERATION_STAGE,
    RENDER_STAGE,
    SCENARIO_STAGE,
)
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATError
from a2a_t.llm.errors import LLMConfigError, LLMRuntimeError
from a2a_t.prompt.analysis.errors import PromptAnalysisError
from a2a_t.prompt.analysis.models import (
    ScenarioDefinition,
    ScenarioResolutionFailure,
    ScenarioResolutionResult,
    SlotExtractionResult,
)
from a2a_t.prompt.common.models import PromptReference
from tests.support import FakePromptResourceAccess

#: Catalog codes the generation pipeline emits at its failure boundaries (Java orchestrator parity).
GENERATION_FAILURE_CODES = (
    ErrorCatalog.INPUT_TEXT_TOO_LONG,
    ErrorCatalog.SCENARIO_NOT_MATCHED,
    ErrorCatalog.TEMPLATE_NOT_FOUND,
    ErrorCatalog.SLOT_SCHEMA_NOT_FOUND,
    ErrorCatalog.TEMPLATE_LOAD_FAILED,
    ErrorCatalog.LLM_RESPONSE_INVALID,
    ErrorCatalog.LLM_INVOCATION_FAILED,
    ErrorCatalog.LLM_NOT_CONFIGURED,
    ErrorCatalog.TEMPLATE_RENDER_FAILED,
)

_SCENARIO = ScenarioDefinition(
    scenario_code="ran-energy-saving",
    scenario_name="Energy Saving",
    description="Used for energy saving analysis.",
    example="Analyze site power usage and suggest optimization.",
)

_SLOT_JSON_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "site": {
            "type": "string",
            "description": "Site name",
            "examples": ["Site A"],
            "x-a2at-value-constraint": "Must be a concrete site name.",
        },
        "additional_notes": {
            "type": "string",
            "description": "Additional notes",
            "examples": ["Focus on power system"],
        },
    },
    "required": ["site"],
}


@pytest.mark.parametrize("entry", GENERATION_FAILURE_CODES, ids=lambda entry: entry.value)
def test_generation_failure_codes_are_closed_catalog_members(entry: ErrorCatalog) -> None:
    assert ErrorCatalog(entry.value) is entry
    assert entry.value.count(".") == 1
    assert entry.value == entry.value.lower()


class FakeScenarioResolver:
    def __init__(self, result: ScenarioResolutionResult) -> None:
        self._result = result
        self.calls: list[str] = []
        self.last_raw_response_content = '{"matched": true}'

    def resolve(self, normalized_input: str) -> ScenarioResolutionResult:
        self.calls.append(normalized_input)
        return self._result


class FakeSlotExtractor:
    def __init__(self, result: SlotExtractionResult) -> None:
        self._result = result
        self.last_reference: PromptReference | None = None
        self.last_kwargs: dict[str, Any] = {}
        self.last_raw_response_content = '{"slots": {}}'

    def extract(self, **kwargs: object) -> SlotExtractionResult:
        self.last_reference = kwargs.get("reference")  # type: ignore[assignment]
        self.last_kwargs = dict(kwargs)
        return self._result


class RaisingSlotExtractor:
    def __init__(self, error: Exception) -> None:
        self._error = error
        self.last_raw_response_content: str | None = None

    def extract(self, **kwargs: object) -> SlotExtractionResult:
        raise self._error


class FakeLogger:
    def __init__(self) -> None:
        self.info_calls: list[str] = []
        self.debug_calls: list[str] = []

    def info(self, message: str, *args: object) -> None:
        self.info_calls.append(message % args if args else message)

    def debug(self, message: str, *args: object) -> None:
        self.debug_calls.append(message % args if args else message)


class FalsyLogger(FakeLogger):
    def __bool__(self) -> bool:
        return False


class FakeRenderer:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def render(self, **kwargs: object) -> str:
        raise self._error


class FakePromptRuntimeConfig(PromptRuntimeConfig):
    __slots__ = ("prompt_generation_debug",)

    def __init__(
        self,
        *,
        language: str = "en-US",
        source_type: str = "local_file",
        local_root_dir: str = "./src/a2a_t/prompt_resources",
        prompt_generation_debug: bool = False,
    ) -> None:
        super().__init__(
            language=language,
            source_type=source_type,
            local_root_dir=local_root_dir,
        )
        self.prompt_generation_debug = prompt_generation_debug


def _success_resolution() -> ScenarioResolutionResult:
    return ScenarioResolutionResult(
        success=True,
        reference=PromptReference(scenario_code="ran-energy-saving", language="en-US"),
        scenario=_SCENARIO,
    )


def _build_orchestrator(
    *,
    scenario_result: ScenarioResolutionResult | None = None,
    extraction_result: SlotExtractionResult | None = None,
    resource_access: FakePromptResourceAccess | None = None,
    debug_enabled: bool = False,
    logger: FakeLogger | None = None,
    slot_extractor: object | None = None,
    renderer: object | None = None,
) -> Any:
    from a2a_t.client.prompt_generation.prompt_generation_orchestrator import PromptGenerationOrchestrator

    return PromptGenerationOrchestrator(
        config=FakePromptRuntimeConfig(
            language="en-US",
            prompt_generation_debug=debug_enabled,
        ),
        resource_access=resource_access
        or FakePromptResourceAccess(
            template_text="Site: {site}\nNotes: {additional_notes}",
            slot_json_schema=_SLOT_JSON_SCHEMA,
            system_prompt="Extract slots.",
            user_prompt="Return slots.",
        ),
        scenario_resolver=FakeScenarioResolver(scenario_result or _success_resolution()),
        slot_extractor=slot_extractor
        or FakeSlotExtractor(extraction_result or SlotExtractionResult(slots={}, slot_errors=[])),
        renderer=renderer,
        logger=logger,
    )


def test_orchestrator_requires_prompt_runtime_config() -> None:
    from a2a_t.client.prompt_generation.prompt_generation_orchestrator import PromptGenerationOrchestrator

    with pytest.raises(TypeError):
        PromptGenerationOrchestrator(
            config=object(),  # type: ignore[arg-type]
            resource_access=FakePromptResourceAccess(),
            scenario_resolver=FakeScenarioResolver(_success_resolution()),
            slot_extractor=FakeSlotExtractor(SlotExtractionResult(slots={}, slot_errors=[])),
        )


def test_generate_returns_success_result() -> None:
    access = FakePromptResourceAccess(
        template_text="Site: {site}\nNotes: {additional_notes}",
        slot_json_schema=_SLOT_JSON_SCHEMA,
        system_prompt="Extract slots.",
        user_prompt="Return slots.",
    )
    extractor = FakeSlotExtractor(
        SlotExtractionResult(slots={"site": "Site A", "additional_notes": None}, slot_errors=[])
    )
    orchestrator = _build_orchestrator(resource_access=access, extraction_result=None, slot_extractor=extractor)

    result = orchestrator.generate("Analyze Site A energy usage.")

    assert result.success is True
    assert access.template_calls == [("ran-energy-saving", "en-US")]
    assert access.slot_schema_calls == [("ran-energy-saving", "en-US")]
    assert access.prompt_calls == [
        ("slot_extraction", "en-US", "system.md"),
        ("slot_extraction", "en-US", "user.md"),
    ]
    assert extractor.last_reference == PromptReference(scenario_code="ran-energy-saving", language="en-US")
    assert extractor.last_kwargs["system_prompt"] == "Extract slots."
    assert extractor.last_kwargs["user_prompt"] == "Return slots."
    assert result.failure is None
    assert result.prompt_text == "Site: Site A\nNotes: "


def test_orchestrator_uses_explicit_logger_even_when_logger_is_falsy() -> None:
    logger = FalsyLogger()
    orchestrator = _build_orchestrator(logger=logger)

    orchestrator.generate("Analyze Site A energy usage.")

    assert "prompt_generation_started" in logger.info_calls


def test_generate_returns_success_result_when_extracted_slots_are_missing() -> None:
    orchestrator = _build_orchestrator(
        extraction_result=SlotExtractionResult(slots={"site": None, "additional_notes": None}, slot_errors=[])
    )

    result = orchestrator.generate("Analyze Site A energy usage.")

    assert result.success is True
    assert result.prompt_text == "Site: \nNotes: "
    assert result.failure is None


def test_generate_returns_scenario_failure_when_resolution_fails() -> None:
    orchestrator = _build_orchestrator(
        scenario_result=ScenarioResolutionResult(
            success=False,
            failure=ScenarioResolutionFailure(
                code=ErrorCatalog.SCENARIO_NOT_MATCHED.value,
                message="The input does not match any known scenario: No matching scenario.",
                stage=SCENARIO_STAGE,
            ),
        ),
    )

    result = orchestrator.generate("Analyze Site A energy usage.")

    assert result.success is False
    assert result.prompt_text is None
    assert result.failure is not None
    assert result.failure.code == ErrorCatalog.SCENARIO_NOT_MATCHED.value
    assert result.failure.stage == SCENARIO_STAGE


def test_generate_returns_scenario_failure_when_scenario_resources_are_invalid() -> None:
    orchestrator = _build_orchestrator(
        scenario_result=ScenarioResolutionResult(
            success=False,
            failure=ScenarioResolutionFailure(
                code=ErrorCatalog.TEMPLATE_LOAD_FAILED.value,
                message="Failed to read template resource 'scenario resources are invalid'",
                stage=SCENARIO_STAGE,
            ),
        ),
    )

    result = orchestrator.generate("Analyze Site A energy usage.")

    assert result.success is False
    assert result.failure is not None
    assert result.failure.code == ErrorCatalog.TEMPLATE_LOAD_FAILED.value
    assert result.failure.stage == SCENARIO_STAGE
    assert result.failure.message == "Failed to read template resource 'scenario resources are invalid'"


def test_generate_returns_generation_failure_when_generation_resource_access_fails() -> None:
    access = FakePromptResourceAccess(template_text=A2ATError("template resource read failed"))
    orchestrator = _build_orchestrator(resource_access=access)

    result = orchestrator.generate("Analyze Site A energy usage.")

    assert result.success is False
    assert result.failure is not None
    assert result.failure.code == ErrorCatalog.TEMPLATE_LOAD_FAILED.value
    assert result.failure.stage == "preparation"
    assert result.failure.message == "Failed to read template resource 'ran-energy-saving'"


@pytest.mark.parametrize(
    ("template_failure", "expected_code", "expected_message"),
    [
        (
            ErrorCatalog.TEMPLATE_NOT_FOUND,
            "template.not_found",
            "Template 'ran-energy-saving' does not exist",
        ),
        (
            ErrorCatalog.SLOT_SCHEMA_NOT_FOUND,
            "slot.schema_not_found",
            "Template 'ran-energy-saving' is missing its slot schema (language 'en-US')",
        ),
    ],
    ids=["missing-template", "missing-slot-schema"],
)
def test_generate_surfaces_the_access_layer_business_codes(
    template_failure: ErrorCatalog,
    expected_code: str,
    expected_message: str,
) -> None:
    from a2a_t.core.errors.exceptions import A2ATBusinessError

    access = FakePromptResourceAccess(
        template_text=A2ATBusinessError(
            template_failure,
            {"template_uri": "ran-energy-saving", "language": "en-US"},
        )
        if template_failure is ErrorCatalog.TEMPLATE_NOT_FOUND
        else "Site: {site}",
        slot_json_schema=A2ATBusinessError(
            template_failure,
            {"template_uri": "ran-energy-saving", "language": "en-US"},
        )
        if template_failure is ErrorCatalog.SLOT_SCHEMA_NOT_FOUND
        else _SLOT_JSON_SCHEMA,
    )
    orchestrator = _build_orchestrator(resource_access=access)

    result = orchestrator.generate("Analyze Site A energy usage.")

    assert result.success is False
    assert result.failure is not None
    assert result.failure.code == expected_code
    assert result.failure.stage == "preparation"
    assert result.failure.message == expected_message


def test_generate_returns_generation_failure_when_slot_extraction_prompts_are_missing() -> None:
    access = FakePromptResourceAccess(system_prompt=A2ATError("prompt resource read failed"))
    orchestrator = _build_orchestrator(resource_access=access)

    result = orchestrator.generate("Analyze Site A energy usage.")

    assert result.success is False
    assert result.failure is not None
    assert result.failure.code == ErrorCatalog.TEMPLATE_LOAD_FAILED.value
    assert result.failure.stage == "preparation"
    assert result.failure.message == (
        "Failed to read template resource 'prompt_resources/prompts/slot_extraction/en-US/system.md'"
    )


def test_generate_returns_generation_failure_when_slot_extraction_payload_is_invalid() -> None:
    orchestrator = _build_orchestrator(
        slot_extractor=RaisingSlotExtractor(PromptAnalysisError("slot extraction returned invalid JSON"))
    )

    result = orchestrator.generate("Analyze Site A energy usage.")

    assert result.success is False
    assert result.failure is not None
    assert result.failure.code == ErrorCatalog.LLM_RESPONSE_INVALID.value
    assert result.failure.stage == GENERATION_STAGE
    assert result.failure.message == "The LLM response is invalid (step: slot extraction); please retry"


def test_generate_returns_generation_failure_when_slot_extraction_runtime_fails() -> None:
    orchestrator = _build_orchestrator(slot_extractor=RaisingSlotExtractor(RuntimeError("llm transport down")))

    result = orchestrator.generate("Analyze Site A energy usage.")

    assert result.success is False
    assert result.failure is not None
    assert result.failure.code == ErrorCatalog.LLM_INVOCATION_FAILED.value
    assert result.failure.stage == GENERATION_STAGE
    assert result.failure.message == "LLM invocation failed (provider {provider}): llm transport down"


def test_generate_returns_render_failure_when_renderer_rejects_slots() -> None:
    from a2a_t.prompt.task_rendering.errors import TaskPromptRenderError

    orchestrator = _build_orchestrator(
        extraction_result=SlotExtractionResult(slots={"site": "Site A", "additional_notes": None}, slot_errors=[]),
        renderer=FakeRenderer(TaskPromptRenderError("Template references unknown slot: time_range")),
    )

    result = orchestrator.generate("Analyze Site A energy usage.")

    assert result.success is False
    assert result.failure is not None
    assert result.failure.code == ErrorCatalog.TEMPLATE_RENDER_FAILED.value
    assert result.failure.stage == RENDER_STAGE
    assert result.failure.message == (
        "Failed to render template 'ran-energy-saving': Template references unknown slot: time_range"
    )


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_message"),
    [
        (
            LLMConfigError("llm is not configured"),
            ErrorCatalog.LLM_NOT_CONFIGURED,
            "No LLM client is configured; check the A2AT_LLM_* settings",
        ),
        (
            LLMRuntimeError("openai returned invalid json: boom"),
            ErrorCatalog.LLM_RESPONSE_INVALID,
            "The LLM response is invalid (step: slot extraction); please retry",
        ),
        (
            LLMRuntimeError("openai returned empty content"),
            ErrorCatalog.LLM_RESPONSE_INVALID,
            "The LLM response is invalid (step: slot extraction); please retry",
        ),
        (
            LLMRuntimeError("openai invocation failed: timeout"),
            ErrorCatalog.LLM_INVOCATION_FAILED,
            "LLM invocation failed (provider {provider}): openai invocation failed: timeout",
        ),
    ],
    ids=["config-error", "invalid-json", "empty-content", "transport-failure"],
)
def test_generate_translates_llm_step_failures_to_catalog_codes(
    error: Exception,
    expected_code: ErrorCatalog,
    expected_message: str,
) -> None:
    orchestrator = _build_orchestrator(slot_extractor=RaisingSlotExtractor(error))

    result = orchestrator.generate("Analyze Site A energy usage.")

    assert result.success is False
    assert result.failure is not None
    assert result.failure.code == expected_code.value
    assert result.failure.stage == GENERATION_STAGE
    assert result.failure.message == expected_message
