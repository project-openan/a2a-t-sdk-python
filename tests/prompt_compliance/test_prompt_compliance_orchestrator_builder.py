"""Builder wiring tests for the server prompt compliance orchestrator (D31 access layer)."""

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


class FakeSemanticValidator:
    def __init__(self, *, llm_client: object, resource_access: object) -> None:
        self.llm_client = llm_client
        self.resource_access = resource_access


class FakeLogger:
    def __init__(self) -> None:
        self.info_messages: list[tuple[str, tuple[object, ...]]] = []
        self.debug_messages: list[tuple[str, tuple[object, ...]]] = []

    def info(self, message: str, *args: object) -> None:
        self.info_messages.append((message, args))

    def debug(self, message: str, *args: object) -> None:
        self.debug_messages.append((message, args))


def _components() -> object:
    return type("Components", (), {"resource_access": object(), "json_schema_slot_validator": object()})()


def _config() -> A2ATConfig:
    return A2ATConfig(
        prompt=PromptRuntimeConfig(local_root_dir="./default-root"),
    )


def _builder(components: object, runtime_builder: FakeRuntimeComponentsBuilder) -> object:
    from a2a_t.server.prompt_compliance.prompt_compliance_orchestrator_builder import (
        PromptComplianceOrchestratorBuilder,
    )

    return PromptComplianceOrchestratorBuilder(
        runtime_components_builder=runtime_builder,
        scenario_recognizer_cls=FakeScenarioRecognizer,
        scenario_resolver_cls=FakeScenarioResolver,
        slot_extractor_cls=FakeSlotExtractor,
        semantic_validator_cls=FakeSemanticValidator,
        orchestrator_cls=FakeOrchestrator,
    )


def test_builder_uses_runtime_components_builder_and_injects_llm_client() -> None:
    components = _components()
    runtime_builder = FakeRuntimeComponentsBuilder(components)
    llm_client = object()
    builder = _builder(components, runtime_builder)

    orchestrator = builder.build(
        config=_config(),
        llm_client=llm_client,
    )

    assert len(runtime_builder.calls) == 1
    assert isinstance(orchestrator, FakeOrchestrator)
    assert isinstance(orchestrator.kwargs["scenario_resolver"], FakeScenarioResolver)
    assert orchestrator.kwargs["scenario_resolver"].config is not None
    assert orchestrator.kwargs["scenario_resolver"].resource_access is components.resource_access
    assert isinstance(orchestrator.kwargs["scenario_resolver"].scenario_recognizer, FakeScenarioRecognizer)
    assert orchestrator.kwargs["scenario_resolver"].scenario_recognizer.llm_client is llm_client
    assert isinstance(orchestrator.kwargs["extractor"], FakeSlotExtractor)
    assert orchestrator.kwargs["extractor"].llm_client is llm_client
    assert isinstance(orchestrator.kwargs["semantic_validator"], FakeSemanticValidator)
    assert orchestrator.kwargs["semantic_validator"].llm_client is llm_client
    assert orchestrator.kwargs["semantic_validator"].resource_access is components.resource_access
    assert orchestrator.kwargs["resource_access"] is components.resource_access
    assert orchestrator.kwargs["validator"] is components.json_schema_slot_validator
    assert "guardrail" not in orchestrator.kwargs
    assert orchestrator.kwargs["logger"] is None


def test_builder_reuses_provided_runtime_components_without_rebuilding() -> None:
    components = _components()
    runtime_builder = FakeRuntimeComponentsBuilder(components)
    llm_client = object()
    builder = _builder(components, runtime_builder)

    orchestrator = builder.build(
        config=_config(),
        llm_client=llm_client,
        runtime_components=components,  # type: ignore[arg-type]
    )

    assert runtime_builder.calls == []
    assert isinstance(orchestrator, FakeOrchestrator)
    assert orchestrator.kwargs["scenario_resolver"].resource_access is components.resource_access
    assert orchestrator.kwargs["resource_access"] is components.resource_access
    assert orchestrator.kwargs["validator"] is components.json_schema_slot_validator
    assert orchestrator.kwargs["semantic_validator"].resource_access is components.resource_access


def test_builder_passes_an_injected_resource_access_through() -> None:
    components = _components()
    runtime_builder = FakeRuntimeComponentsBuilder(components)
    llm_client = object()
    builder = _builder(components, runtime_builder)
    access = object()

    builder.build(
        config=_config(),
        llm_client=llm_client,
        resource_access=access,  # type: ignore[arg-type]
    )

    assert len(runtime_builder.calls) == 1
    assert runtime_builder.calls[0][1] is access


def test_builder_injects_logger_into_orchestrator() -> None:
    components = _components()
    runtime_builder = FakeRuntimeComponentsBuilder(components)
    llm_client = object()
    logger = FakeLogger()
    builder = _builder(components, runtime_builder)

    orchestrator = builder.build(
        config=_config(),
        llm_client=llm_client,
        logger=logger,
    )

    assert orchestrator.kwargs["logger"] is logger
