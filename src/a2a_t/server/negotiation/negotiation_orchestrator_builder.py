from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from a2a_t.config.models import A2ATConfig
from a2a_t.negotiation.handling import NegotiationHandler
from a2a_t.negotiation.rendering import NegotiationPromptRenderer
from a2a_t.negotiation.common.enums import NegotiationType
from a2a_t.negotiation.store import NegotiationStateStoreFactory
from a2a_t.negotiation.types import (
    ClarificationNegotiationType,
    FeasibilityNegotiationType,
    FulfillmentNegotiationType,
    InformationNegotiationType,
)
from a2a_t.common.prompt_runtime import PromptRuntimeComponentsBuilder
from a2a_t.server.prompt_compliance.prompt_compliance_orchestrator_builder import PromptComplianceOrchestratorBuilder

from .negotiation_orchestrator import NegotiationOrchestrator


class ServerNegotiationOrchestratorBuilder:
    def __init__(
        self,
        *,
        prompt_compliance_builder: PromptComplianceOrchestratorBuilder | None = None,
        runtime_components_builder: PromptRuntimeComponentsBuilder | None = None,
        prompt_renderer_cls: type = NegotiationPromptRenderer,
        store_factory: NegotiationStateStoreFactory | None = None,
        handler_cls: type = NegotiationHandler,
        orchestrator_cls: type = NegotiationOrchestrator,
    ) -> None:
        self._prompt_compliance_builder = prompt_compliance_builder or PromptComplianceOrchestratorBuilder()
        self._runtime_components_builder = runtime_components_builder or PromptRuntimeComponentsBuilder()
        self._prompt_renderer_cls = prompt_renderer_cls
        self._store_factory = store_factory or NegotiationStateStoreFactory()
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
        effective_config = config
        if resource_root is not None:
            effective_config = A2ATConfig(
                prompt=replace(config.prompt, local_root_dir=str(resource_root)),
                prompt_compliance=config.prompt_compliance,
            )

        components = self._runtime_components_builder.build(config=effective_config)
        prompt_checker = self._prompt_compliance_builder.build(
            config=effective_config,
            llm_client=llm_client,
            resource_root=resource_root,
            runtime_components=components,
        )
        prompt_renderer = self._prompt_renderer_cls()
        store = self._build_store(env_path=env_path, logger=logger)
        handler = self._handler_cls(
            negotiation_types={
                NegotiationType.INFORMATION: InformationNegotiationType(
                    prompt_renderer=prompt_renderer,
                    prompt_checker=prompt_checker,
                ),
                NegotiationType.CLARIFICATION: ClarificationNegotiationType(prompt_renderer=prompt_renderer),
                NegotiationType.FEASIBILITY: FeasibilityNegotiationType(prompt_renderer=prompt_renderer),
                NegotiationType.FULFILLMENT: FulfillmentNegotiationType(prompt_renderer=prompt_renderer),
            },
            store=store,
        )
        return self._orchestrator_cls(
            handler=handler,
        )

    def _build_store(self, *, env_path: str | Path | None, logger: Any | None) -> object:
        return self._store_factory.build(
            env_path=env_path,
            logger=logger,
        )
