from __future__ import annotations

from typing import Any

from a2a_t.config.models import A2ATConfig
from a2a_t.common.prompt_runtime import PromptRuntimeComponents, PromptRuntimeComponentsBuilder
from a2a_t.prompt.analysis import ScenarioRecognizer, ScenarioResolutionOrchestrator, SlotExtractor

from .llm_semantic_slot_validator import LLMSemanticSlotValidator
from .prompt_compliance_orchestrator import PromptComplianceOrchestrator


class PromptComplianceOrchestratorBuilder:
    """Assemble the server-side prompt compliance runtime."""

    def __init__(
        self,
        *,
        runtime_components_builder: PromptRuntimeComponentsBuilder | None = None,
        scenario_recognizer_cls: type = ScenarioRecognizer,
        scenario_resolver_cls: type = ScenarioResolutionOrchestrator,
        slot_extractor_cls: type = SlotExtractor,
        semantic_validator_cls: type = LLMSemanticSlotValidator,
        orchestrator_cls: type = PromptComplianceOrchestrator,
    ) -> None:
        self._runtime_components_builder = runtime_components_builder or PromptRuntimeComponentsBuilder()
        self._scenario_recognizer_cls = scenario_recognizer_cls
        self._scenario_resolver_cls = scenario_resolver_cls
        self._slot_extractor_cls = slot_extractor_cls
        self._semantic_validator_cls = semantic_validator_cls
        self._orchestrator_cls = orchestrator_cls

    def build(
        self,
        *,
        config: A2ATConfig,
        llm_client: Any,
        runtime_components: PromptRuntimeComponents | None = None,
        logger: Any | None = None,
    ) -> PromptComplianceOrchestrator:
        """Build a fully wired prompt compliance orchestrator."""
        components = runtime_components or self._runtime_components_builder.build(config=config)
        scenario_recognizer = self._scenario_recognizer_cls(llm_client=llm_client)
        scenario_resolver = self._scenario_resolver_cls(
            config=config.prompt,
            scenario_loader=components.scenario_loader,
            prompt_resource_loader=components.prompt_resource_loader,
            scenario_recognizer=scenario_recognizer,
        )
        extractor = self._slot_extractor_cls(llm_client=llm_client)
        semantic_validator = self._semantic_validator_cls(
            llm_client=llm_client,
            prompt_resource_loader=components.prompt_resource_loader,
        )
        return self._orchestrator_cls(
            scenario_resolver=scenario_resolver,
            template_loader=components.template_loader,
            slot_schema_loader=components.slot_schema_loader,
            prompt_resource_loader=components.prompt_resource_loader,
            extractor=extractor,
            validator=components.json_schema_slot_validator,
            semantic_validator=semantic_validator,
            logger=logger,
        )
