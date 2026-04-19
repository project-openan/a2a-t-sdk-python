from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from a2a_t.config.models import A2ATConfig
from a2a_t.negotiation.handling import NegotiationHandler, NegotiationParser
from a2a_t.negotiation.rendering import NegotiationPromptRenderer
from a2a_t.negotiation.runtime import NegotiationContextFactory, NegotiationMapBuilder, NegotiationTypeRegistry
from a2a_t.negotiation.store import NegotiationStateStoreFactory
from a2a_t.negotiation.types import (
    ClarificationNegotiationType,
    FeasibilityNegotiationType,
    FulfillmentNegotiationType,
    InformationNegotiationType,
)
from a2a_t.common.prompt_runtime import PromptRuntimeComponentsBuilder

from .negotiation_orchestrator import NegotiationOrchestrator


class NegotiationOrchestratorBuilder:
    def __init__(
        self,
        *,
        runtime_components_builder: PromptRuntimeComponentsBuilder | None = None,
        prompt_renderer_cls: type = NegotiationPromptRenderer,
        context_factory_cls: type = NegotiationContextFactory,
        map_builder_cls: type = NegotiationMapBuilder,
        parser_cls: type = NegotiationParser,
        store_factory: NegotiationStateStoreFactory | None = None,
        registry_cls: type = NegotiationTypeRegistry,
        handler_cls: type = NegotiationHandler,
        orchestrator_cls: type = NegotiationOrchestrator,
    ) -> None:
        self._runtime_components_builder = runtime_components_builder or PromptRuntimeComponentsBuilder()
        self._prompt_renderer_cls = prompt_renderer_cls
        self._context_factory_cls = context_factory_cls
        self._map_builder_cls = map_builder_cls
        self._parser_cls = parser_cls
        self._store_factory = store_factory or NegotiationStateStoreFactory()
        self._registry_cls = registry_cls
        self._handler_cls = handler_cls
        self._orchestrator_cls = orchestrator_cls

    def build(
        self,
        *,
        config: A2ATConfig,
        llm_client: Any,
        resource_root: str | Path | None = None,
        env_path: str | Path | None = None,
        logger: Any | None = None,
    ) -> NegotiationOrchestrator:
        del llm_client

        effective_config = config
        if resource_root is not None:
            effective_config = A2ATConfig(
                prompt=replace(config.prompt, local_root_dir=str(resource_root)),
                prompt_compliance=config.prompt_compliance,
            )

        components = self._runtime_components_builder.build(config=effective_config)
        prompt_renderer = self._prompt_renderer_cls(
            prompt_resource_loader=components.prompt_resource_loader,
            version=effective_config.prompt.prompt_resource_version,
            language=effective_config.prompt.language,
        )
        context_factory = self._context_factory_cls()
        map_builder = self._map_builder_cls()
        parser = self._parser_cls()
        store = self._build_store(env_path=env_path, logger=logger)
        type_registry = self._registry_cls(
            {
                "information": InformationNegotiationType(prompt_renderer=prompt_renderer),
                "clarification": ClarificationNegotiationType(prompt_renderer=prompt_renderer),
                "feasibility": FeasibilityNegotiationType(prompt_renderer=prompt_renderer),
                "fulfillment": FulfillmentNegotiationType(prompt_renderer=prompt_renderer),
            }
        )
        handler = self._handler_cls(
            parser=parser,
            context_factory=context_factory,
            type_registry=type_registry,
            map_builder=map_builder,
            store=store,
            now_factory=self._now_factory,
        )
        return self._orchestrator_cls(
            handler=handler,
            parser=parser,
        )

    def _build_store(self, *, env_path: str | Path | None, logger: Any | None) -> object:
        return self._store_factory.build(
            env_path=env_path,
            logger=logger,
        )

    @staticmethod
    def _now_factory() -> object:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc)
