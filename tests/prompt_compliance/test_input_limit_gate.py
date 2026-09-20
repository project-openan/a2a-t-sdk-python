"""Input-limit gate and slot-code resolution tests for the server compliance entry (D5)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.common.prompt_resources.models import ScenarioDefinition
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.input_limit import DEFAULT_MAX_TEXT_CHARS, InputLimitConfig
from a2a_t.llm.models import LLMClientConfig
from a2a_t.prompt.analysis.models import ScenarioResolutionResult, SlotExtractionResult
from a2a_t.prompt.common.models import PromptReference
from a2a_t.prompt.validation.models import SlotValidationResult
from a2a_t.server.prompt_compliance.constants import INPUT_GATE_STAGE
from a2a_t.server.prompt_compliance.prompt_compliance_orchestrator import (
    PromptComplianceOrchestrator,
    resolve_slot_error_code,
)
from tests.support import FakePromptResourceAccess, ManagedTempDirTestCase

_SCENARIO = ScenarioDefinition(
    scenario_code="ran-energy-saving",
    scenario_name="Energy Saving",
    description="Used for energy saving analysis.",
    example="Analyze site power usage and suggest optimization.",
)


class RecordingScenarioResolver:
    """Fake resolver recording every resolve call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def resolve(self, normalized_input: str) -> ScenarioResolutionResult:
        self.calls.append(normalized_input)
        return ScenarioResolutionResult(
            success=True,
            reference=PromptReference(scenario_code="ran-energy-saving", language="en-US"),
            scenario=_SCENARIO,
        )


class FakeExtractor:
    def extract(self, **kwargs: object) -> SlotExtractionResult:
        return SlotExtractionResult(slots={"site": "Site A"}, slot_errors=[])


class FakeValidator:
    def validate(self, **kwargs: object) -> SlotValidationResult:
        return SlotValidationResult(passed=True, slot_errors=[])


def _build_orchestrator(*, input_limit: InputLimitConfig | None = None) -> PromptComplianceOrchestrator:
    return PromptComplianceOrchestrator(
        scenario_resolver=RecordingScenarioResolver(),
        resource_access=FakePromptResourceAccess(
            template_text="Site: {site}",
            slot_json_schema={"type": "object", "properties": {"site": {"type": "string"}}, "required": ["site"]},
            system_prompt="Extract slots.",
            user_prompt="Return slots.",
        ),
        extractor=FakeExtractor(),
        validator=FakeValidator(),
        semantic_validator=None,
        input_limit=input_limit,
        language="en-US",
    )


@pytest.mark.parametrize("max_chars", [1, 2, 16, 1000])
def test_check_rejects_oversized_processed_prompt_before_any_pipeline_step(max_chars: int) -> None:
    orchestrator = _build_orchestrator(input_limit=InputLimitConfig(max_text_chars=max_chars))
    resolver = orchestrator._scenario_resolver

    result = orchestrator.check(processed_prompt_text="x" * (max_chars + 1))

    assert result.success is False
    assert result.failure is not None
    assert result.failure.code == ErrorCatalog.INPUT_TEXT_TOO_LONG.value
    assert result.failure.stage == INPUT_GATE_STAGE
    assert result.failure.message == (
        f"Input text length {max_chars + 1} exceeds the maximum of {max_chars} (A2AT_INPUT_TEXT_MAX_CHARS)"
    )
    # Fast fail: no pipeline step may run once the input gate rejects the prompt.
    assert resolver.calls == []


@pytest.mark.parametrize("max_chars", [1, 2, 16, 1000])
def test_check_accepts_processed_prompt_at_the_limit_boundary(max_chars: int) -> None:
    orchestrator = _build_orchestrator(input_limit=InputLimitConfig(max_text_chars=max_chars))
    resolver = orchestrator._scenario_resolver

    result = orchestrator.check(processed_prompt_text="x" * max_chars)

    assert result.success is True
    assert result.failure is None
    assert resolver.calls == ["x" * max_chars]


def test_check_uses_the_default_limit_when_none_is_configured() -> None:
    orchestrator = _build_orchestrator()
    resolver = orchestrator._scenario_resolver

    accepted = orchestrator.check(processed_prompt_text="x" * DEFAULT_MAX_TEXT_CHARS)
    rejected = orchestrator.check(processed_prompt_text="x" * (DEFAULT_MAX_TEXT_CHARS + 1))

    assert accepted.success is True
    assert rejected.failure is not None
    assert rejected.failure.code == ErrorCatalog.INPUT_TEXT_TOO_LONG.value
    assert resolver.calls == ["x" * DEFAULT_MAX_TEXT_CHARS]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("missing_input", ErrorCatalog.SLOT_NOT_PROVIDED),
        ("invalid_value", ErrorCatalog.SLOT_CONSTRAINT_VIOLATED),
        ("slot.not_provided", ErrorCatalog.SLOT_NOT_PROVIDED),
        ("slot.constraint_violated", ErrorCatalog.SLOT_CONSTRAINT_VIOLATED),
        ("slot.semantic_conflict", ErrorCatalog.SLOT_SEMANTIC_CONFLICT),
        ("slot.fabricated_value", ErrorCatalog.SLOT_FABRICATED_VALUE),
        ("slot.cross_scenario_pollution", ErrorCatalog.SLOT_CROSS_SCENARIO_POLLUTION),
        ("slot.insufficient_grounding", ErrorCatalog.SLOT_INSUFFICIENT_GROUNDING),
        ("slot.rule_violation", ErrorCatalog.SLOT_RULE_VIOLATION),
        # Codes outside the slot domain and unknown codes never surface raw (D4).
        ("negotiation.invalid_input", ErrorCatalog.SLOT_RULE_VIOLATION),
        ("content.param_missing", ErrorCatalog.SLOT_RULE_VIOLATION),
        ("semantic_validation_parse_error", ErrorCatalog.SLOT_RULE_VIOLATION),
        ("semantic_validation_runtime_error", ErrorCatalog.SLOT_RULE_VIOLATION),
        ("", ErrorCatalog.SLOT_RULE_VIOLATION),
    ],
)
def test_resolve_slot_error_code_maps_legacy_and_unknown_codes(code: str, expected: ErrorCatalog) -> None:
    assert resolve_slot_error_code(code) is expected


def _build_llm_config() -> LLMClientConfig:
    return LLMClientConfig(
        provider="openai",
        model="gpt-4o-mini",
        api_key="sk-test",
        base_url=None,
        history_window=10,
        max_tokens=None,
        temperature=None,
        timeout_seconds=None,
        session_max_total=300,
        session_max_per_provider=100,
    )


def test_builder_passes_the_configured_input_limit_and_language_into_the_orchestrator() -> None:
    from a2a_t.config.models import A2ATConfig, PromptRuntimeConfig
    from a2a_t.server.prompt_compliance.prompt_compliance_orchestrator_builder import (
        PromptComplianceOrchestratorBuilder,
    )

    class FakeRuntimeComponentsBuilder:
        def build(self, *, config: A2ATConfig, resource_access: object | None = None) -> object:
            return type(
                "Components",
                (),
                {
                    "resource_access": resource_access if resource_access is not None else object(),
                    "json_schema_slot_validator": object(),
                },
            )()

    class FakeOrchestrator:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    input_limit = InputLimitConfig(max_text_chars=512)
    config = A2ATConfig(
        prompt=PromptRuntimeConfig(language="zh-CN"),
        input_limits=input_limit,
    )

    orchestrator = PromptComplianceOrchestratorBuilder(
        runtime_components_builder=FakeRuntimeComponentsBuilder(),
        orchestrator_cls=FakeOrchestrator,
    ).build(config=config, llm_client=object())

    assert orchestrator.kwargs["input_limit"] is input_limit
    assert orchestrator.kwargs["language"] == "zh-CN"


class A2ATServerInputLimitGateTest(ManagedTempDirTestCase):
    def test_check_task_prompt_rejects_oversized_input_at_the_facade_entry(self) -> None:
        from a2a_t.server.a2at_server import A2ATServer

        root = self.make_temp_dir("server_input_limit_env")
        env_path = root / "server.env"
        env_path.write_text(
            "\n".join(
                [
                    "A2AT_LANGUAGE=en-US",
                    "A2AT_PROMPT_SOURCE_TYPE=local_file",
                    f"A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR={root}",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        with (
            patch("a2a_t.server.a2at_server._default_env_path", return_value=env_path),
            patch("a2a_t.server.a2at_server.LLMConfigLoader.load", return_value=_build_llm_config()),
            patch("a2a_t.server.a2at_server.LLMClientFactory.create", return_value=object()),
            patch("a2a_t.server.a2at_server.ServerNegotiationOrchestratorBuilder") as negotiation_builder_cls,
        ):
            negotiation_builder_cls.return_value.build.return_value = object()
            server = A2ATServer(env_path=env_path)

            result = server.check_task_prompt(processed_prompt_text="x" * (DEFAULT_MAX_TEXT_CHARS + 1))

        assert result.success is False
        assert result.failure is not None
        assert result.failure.code == ErrorCatalog.INPUT_TEXT_TOO_LONG.value
        assert result.failure.stage == INPUT_GATE_STAGE
