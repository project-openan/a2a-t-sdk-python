"""Input-limit gate tests for the client prompt-generation entry (D5)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.client.prompt_generation.generation_constants import INPUT_STAGE
from a2a_t.client.prompt_generation.prompt_generation_orchestrator import PromptGenerationOrchestrator
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.input_limit import DEFAULT_MAX_TEXT_CHARS, InputLimitConfig
from a2a_t.llm.models import LLMClientConfig
from a2a_t.prompt.analysis.models import ScenarioDefinition, ScenarioResolutionResult, SlotExtractionResult
from a2a_t.prompt.common.models import PromptReference
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


class FakeSlotExtractor:
    def __init__(self) -> None:
        self.last_raw_response_content: str | None = None

    def extract(self, **kwargs: object) -> SlotExtractionResult:
        return SlotExtractionResult(slots={"site": "Site A"}, slot_errors=[])


def _build_orchestrator(*, input_limit: InputLimitConfig | None = None) -> PromptGenerationOrchestrator:
    return PromptGenerationOrchestrator(
        config=PromptRuntimeConfig(language="en-US"),
        resource_access=FakePromptResourceAccess(
            template_text="Site: {site}",
            slot_json_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"site": {"type": "string"}},
                "required": ["site"],
            },
            system_prompt="Extract slots.",
            user_prompt="Return slots.",
        ),
        scenario_resolver=RecordingScenarioResolver(),
        slot_extractor=FakeSlotExtractor(),
        input_limit=input_limit,
    )


@pytest.mark.parametrize("max_chars", [1, 2, 16, 1000])
def test_generate_rejects_oversized_free_text_before_any_pipeline_step(max_chars: int) -> None:
    orchestrator = _build_orchestrator(input_limit=InputLimitConfig(max_text_chars=max_chars))
    resolver = orchestrator._scenario_resolver

    result = orchestrator.generate("x" * (max_chars + 1))

    assert result.success is False
    assert result.prompt_text is None
    assert result.failure is not None
    assert result.failure.code == ErrorCatalog.INPUT_TEXT_TOO_LONG.value
    assert result.failure.stage == INPUT_STAGE
    assert result.failure.message == (
        f"Input text length {max_chars + 1} exceeds the maximum of {max_chars} (A2AT_INPUT_TEXT_MAX_CHARS)"
    )
    # Fast fail: no pipeline step may run once the gate rejects the input.
    assert resolver.calls == []


@pytest.mark.parametrize("max_chars", [1, 2, 16, 1000])
def test_generate_accepts_free_text_at_the_limit_boundary(max_chars: int) -> None:
    orchestrator = _build_orchestrator(input_limit=InputLimitConfig(max_text_chars=max_chars))
    resolver = orchestrator._scenario_resolver

    result = orchestrator.generate("x" * max_chars)

    assert result.success is True
    assert result.failure is None
    assert resolver.calls == ["x" * max_chars]


def test_generate_does_not_length_gate_structured_dict_input() -> None:
    orchestrator = _build_orchestrator(input_limit=InputLimitConfig(max_text_chars=4))
    resolver = orchestrator._scenario_resolver

    result = orchestrator.generate({"site": "a value far longer than the configured limit"})

    assert result.success is True
    assert resolver.calls == ['{"site": "a value far longer than the configured limit"}']


def test_generate_uses_the_default_limit_when_none_is_configured() -> None:
    orchestrator = _build_orchestrator()
    resolver = orchestrator._scenario_resolver

    accepted = orchestrator.generate("x" * DEFAULT_MAX_TEXT_CHARS)
    rejected = orchestrator.generate("x" * (DEFAULT_MAX_TEXT_CHARS + 1))

    assert accepted.success is True
    assert rejected.failure is not None
    assert rejected.failure.code == ErrorCatalog.INPUT_TEXT_TOO_LONG.value
    assert rejected.failure.message == (
        f"Input text length {DEFAULT_MAX_TEXT_CHARS + 1} exceeds the maximum of "
        f"{DEFAULT_MAX_TEXT_CHARS} (A2AT_INPUT_TEXT_MAX_CHARS)"
    )
    assert resolver.calls == ["x" * DEFAULT_MAX_TEXT_CHARS]


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


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("32", 32),
        ("", DEFAULT_MAX_TEXT_CHARS),
        ("not-a-number", DEFAULT_MAX_TEXT_CHARS),
        ("-4", DEFAULT_MAX_TEXT_CHARS),
    ],
    ids=["explicit", "blank", "non-numeric", "non-positive"],
)
def test_a2at_config_loads_the_input_limit_from_the_env_file(
    raw_value: str,
    expected: int,
    tmp_path: Path,
) -> None:
    from a2a_t.config.models import A2ATConfig

    env_path = tmp_path / ".env"
    env_path.write_text(f"A2AT_INPUT_TEXT_MAX_CHARS={raw_value}\n", encoding="utf-8")

    config = A2ATConfig.load(env_path)

    assert config.input_limits.max_text_chars == expected


def test_builder_passes_the_configured_input_limit_into_the_orchestrator() -> None:
    from a2a_t.client.prompt_generation.prompt_generation_orchestrator_builder import (
        PromptGenerationOrchestratorBuilder,
    )
    from a2a_t.config.models import A2ATConfig, PromptRuntimeConfig

    class FakeRuntimeComponentsBuilder:
        def build(self, *, config: A2ATConfig, resource_access: object | None = None) -> object:
            return type(
                "Components",
                (),
                {"resource_access": resource_access if resource_access is not None else object()},
            )()

    class FakeOrchestrator:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    input_limit = InputLimitConfig(max_text_chars=512)
    config = A2ATConfig(
        prompt=PromptRuntimeConfig(),
        input_limits=input_limit,
    )

    orchestrator = PromptGenerationOrchestratorBuilder(
        runtime_components_builder=FakeRuntimeComponentsBuilder(),
        orchestrator_cls=FakeOrchestrator,
    ).build(config=config, llm_client=object())

    assert orchestrator.kwargs["input_limit"] is input_limit


class A2ATClientInputLimitGateTest(ManagedTempDirTestCase):
    def test_generate_task_prompt_rejects_oversized_input_at_the_facade_entry(self) -> None:
        from a2a_t.client.a2at_client import A2ATClient

        root = self.make_temp_dir("client_input_limit_env")
        env_path = root / "client.env"
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
            patch("a2a_t.client.a2at_client._default_env_path", return_value=env_path),
            patch("a2a_t.client.a2at_client.LLMConfigLoader.load", return_value=_build_llm_config()),
            patch("a2a_t.client.a2at_client.LLMClientFactory.create", return_value=object()),
            patch("a2a_t.client.a2at_client.ClientNegotiationOrchestratorBuilder") as negotiation_builder_cls,
        ):
            negotiation_builder_cls.return_value.build.return_value = object()
            client = A2ATClient(env_path=env_path)

            result = client.generate_task_prompt("x" * (DEFAULT_MAX_TEXT_CHARS + 1))

        assert result.success is False
        assert result.failure is not None
        assert result.failure.code == ErrorCatalog.INPUT_TEXT_TOO_LONG.value
        assert result.failure.stage == INPUT_STAGE
