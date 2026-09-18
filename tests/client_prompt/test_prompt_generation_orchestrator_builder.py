"""Builder wiring tests for the client prompt generation orchestrator (D31 access layer)."""

from __future__ import annotations

from a2a_t.common.prompt_resources import PromptResourceAccess
from a2a_t.config.models import A2ATConfig, PromptRuntimeConfig


class FakeRuntimeComponentsBuilder:
    def __init__(self, components: object) -> None:
        self.components = components
        self.calls: list[tuple[A2ATConfig, PromptResourceAccess | None]] = []

    def build(
        self,
        *,
        config: A2ATConfig,
        resource_access: PromptResourceAccess | None = None,
    ) -> object:
        self.calls.append((config, resource_access))
        return self.components


class FakeScenarioRecognizer:
    def __init__(self, *, llm_client: object) -> None:
        self.llm_client = llm_client


class FakeScenarioResolver:
    def __init__(
        self,
        *,
        config: PromptRuntimeConfig,
        resource_access: object,
        scenario_recognizer: object,
    ) -> None:
        self.config = config
        self.resource_access = resource_access
        self.scenario_recognizer = scenario_recognizer


class FakeSlotExtractor:
    def __init__(self, *, llm_client: object) -> None:
        self.llm_client = llm_client


class FakeOrchestrator:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def _config() -> A2ATConfig:
    return A2ATConfig(
        prompt=PromptRuntimeConfig(local_root_dir="./default-root"),
    )


def test_builder_uses_runtime_components_builder_and_injects_llm_client() -> None:
    from a2a_t.client.prompt_generation.prompt_generation_orchestrator_builder import (
        PromptGenerationOrchestratorBuilder,
    )

    access = object()
    components = type("Components", (), {"resource_access": access})()
    runtime_builder = FakeRuntimeComponentsBuilder(components)
    llm_client = object()

    builder = PromptGenerationOrchestratorBuilder(
        runtime_components_builder=runtime_builder,
        scenario_recognizer_cls=FakeScenarioRecognizer,
        scenario_resolver_cls=FakeScenarioResolver,
        slot_extractor_cls=FakeSlotExtractor,
        orchestrator_cls=FakeOrchestrator,
    )

    orchestrator = builder.build(
        config=_config(),
        llm_client=llm_client,
    )

    assert len(runtime_builder.calls) == 1
    assert runtime_builder.calls[0][0] is not None
    assert isinstance(orchestrator, FakeOrchestrator)
    assert orchestrator.kwargs["config"].local_root_dir == "./default-root"
    assert isinstance(orchestrator.kwargs["scenario_resolver"], FakeScenarioResolver)
    assert orchestrator.kwargs["scenario_resolver"].config is not None
    assert orchestrator.kwargs["scenario_resolver"].resource_access is access
    assert isinstance(orchestrator.kwargs["scenario_resolver"].scenario_recognizer, FakeScenarioRecognizer)
    assert orchestrator.kwargs["scenario_resolver"].scenario_recognizer.llm_client is llm_client
    assert isinstance(orchestrator.kwargs["slot_extractor"], FakeSlotExtractor)
    assert orchestrator.kwargs["slot_extractor"].llm_client is llm_client
    assert orchestrator.kwargs["resource_access"] is access
    assert "scenario_loader" not in orchestrator.kwargs
    assert "slot_validator" not in orchestrator.kwargs


def test_builder_passes_an_injected_resource_access_through() -> None:
    from a2a_t.client.prompt_generation.prompt_generation_orchestrator_builder import (
        PromptGenerationOrchestratorBuilder,
    )

    access = object()
    components = type("Components", (), {"resource_access": access})()
    runtime_builder = FakeRuntimeComponentsBuilder(components)
    llm_client = object()

    builder = PromptGenerationOrchestratorBuilder(
        runtime_components_builder=runtime_builder,
        scenario_recognizer_cls=FakeScenarioRecognizer,
        scenario_resolver_cls=FakeScenarioResolver,
        slot_extractor_cls=FakeSlotExtractor,
        orchestrator_cls=FakeOrchestrator,
    )

    builder.build(
        config=_config(),
        llm_client=llm_client,
        resource_access=access,  # type: ignore[arg-type]
    )

    assert len(runtime_builder.calls) == 1
    assert runtime_builder.calls[0][1] is access
