from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from a2a_t.config.models import A2ATConfig
from a2a_t.common.prompt_runtime import PromptRuntimeComponents, PromptRuntimeComponentsBuilder
from a2a_t.prompt.analysis import SlotExtractor

from .prompt_compliance_orchestrator import PromptComplianceOrchestrator


class PromptComplianceOrchestratorBuilder:
    def __init__(
        self,
        *,
        runtime_components_builder: PromptRuntimeComponentsBuilder | None = None,
        slot_extractor_cls: type = SlotExtractor,
        orchestrator_cls: type = PromptComplianceOrchestrator,
    ) -> None:
        self._runtime_components_builder = runtime_components_builder or PromptRuntimeComponentsBuilder()
        self._slot_extractor_cls = slot_extractor_cls
        self._orchestrator_cls = orchestrator_cls

    def build(
        self,
        *,
        config: A2ATConfig,
        llm_client: Any,
        resource_root: str | Path | None = None,
        runtime_components: PromptRuntimeComponents | None = None,
    ) -> PromptComplianceOrchestrator:
        effective_config = config
        if resource_root is not None:
            effective_config = A2ATConfig(
                prompt=replace(config.prompt, local_root_dir=str(resource_root)),
                prompt_compliance=config.prompt_compliance,
            )

        components = runtime_components or self._runtime_components_builder.build(config=effective_config)
        extractor = self._slot_extractor_cls(llm_client=llm_client)
        return self._orchestrator_cls(
            guardrail=components.guardrail,
            template_loader=components.template_loader,
            slot_schema_loader=components.slot_schema_loader,
            prompt_resource_loader=components.prompt_resource_loader,
            extractor=extractor,
            validator=components.slot_validator,
        )
