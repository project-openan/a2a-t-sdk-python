from __future__ import annotations

from typing import Any

from a2a_t.config.models import A2ATConfig
from a2a_t.common.prompt_runtime import PromptRuntimeComponentsBuilder
from a2a_t.prompt.analysis import ScenarioRecognizer, SlotExtractor
from a2a_t.prompt.task_rendering import TaskPromptRenderer

from .prompt_generation_orchestrator import PromptGenerationOrchestrator


class PromptGenerationOrchestratorBuilder:
    def __init__(
        self,
        *,
        runtime_components_builder: PromptRuntimeComponentsBuilder | None = None,
        scenario_recognizer_cls: type = ScenarioRecognizer,
        slot_extractor_cls: type = SlotExtractor,
        renderer_cls: type = TaskPromptRenderer,
        orchestrator_cls: type = PromptGenerationOrchestrator,
    ) -> None:
        self._runtime_components_builder = runtime_components_builder or PromptRuntimeComponentsBuilder()
        self._scenario_recognizer_cls = scenario_recognizer_cls
        self._slot_extractor_cls = slot_extractor_cls
        self._renderer_cls = renderer_cls
        self._orchestrator_cls = orchestrator_cls

    def build(
        self,
        *,
        config: A2ATConfig,
        llm_client: Any,
        logger: Any | None = None,
    ) -> PromptGenerationOrchestrator:
        components = self._runtime_components_builder.build(config=config)
        scenario_recognizer = self._scenario_recognizer_cls(llm_client=llm_client)
        slot_extractor = self._slot_extractor_cls(llm_client=llm_client)

        return self._orchestrator_cls(
            config=config.prompt,
            scenario_loader=components.scenario_loader,
            prompt_resource_loader=components.prompt_resource_loader,
            template_loader=components.template_loader,
            slot_schema_loader=components.slot_schema_loader,
            scenario_recognizer=scenario_recognizer,
            slot_extractor=slot_extractor,
            slot_validator=components.slot_validator,
            resource_registry=components.resource_registry,
            renderer=self._renderer_cls(),
            logger=logger,
        )
