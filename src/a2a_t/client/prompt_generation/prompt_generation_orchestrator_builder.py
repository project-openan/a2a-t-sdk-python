from __future__ import annotations

from typing import Any

from a2a_t.common.prompt_resources import PromptResourceAccess
from a2a_t.common.prompt_runtime import PromptRuntimeComponentsBuilder
from a2a_t.config.models import A2ATConfig
from a2a_t.prompt.analysis import ScenarioRecognizer, ScenarioResolutionOrchestrator, SlotExtractor
from a2a_t.prompt.task_rendering import TaskPromptRenderer

from .prompt_generation_orchestrator import PromptGenerationOrchestrator


class PromptGenerationOrchestratorBuilder:
    """Assemble the client-side prompt generation runtime."""

    def __init__(
        self,
        *,
        runtime_components_builder: PromptRuntimeComponentsBuilder | None = None,
        scenario_recognizer_cls: type = ScenarioRecognizer,
        scenario_resolver_cls: type = ScenarioResolutionOrchestrator,
        slot_extractor_cls: type = SlotExtractor,
        renderer_cls: type = TaskPromptRenderer,
        orchestrator_cls: type = PromptGenerationOrchestrator,
    ) -> None:
        self._runtime_components_builder = runtime_components_builder or PromptRuntimeComponentsBuilder()
        self._scenario_recognizer_cls = scenario_recognizer_cls
        self._scenario_resolver_cls = scenario_resolver_cls
        self._slot_extractor_cls = slot_extractor_cls
        self._renderer_cls = renderer_cls
        self._orchestrator_cls = orchestrator_cls

    def build(
        self,
        *,
        config: A2ATConfig,
        llm_client: Any,
        resource_access: PromptResourceAccess | None = None,
        logger: Any | None = None,
    ) -> PromptGenerationOrchestrator:
        """Build a fully wired prompt generation orchestrator.

        Args:
            config: resolved SDK configuration carrying the prompt runtime settings.
            llm_client: LLM client used by scenario recognition and slot extraction.
            resource_access: optional access object overriding the one built from the config (the
                injection seam mirroring ``llm_client``).
            logger: optional logger receiving the pipeline logs.

        Returns:
            the assembled prompt generation orchestrator.
        """
        components = self._runtime_components_builder.build(config=config, resource_access=resource_access)
        scenario_recognizer = self._scenario_recognizer_cls(llm_client=llm_client)
        scenario_resolver = self._scenario_resolver_cls(
            config=config.prompt,
            resource_access=components.resource_access,
            scenario_recognizer=scenario_recognizer,
        )
        slot_extractor = self._slot_extractor_cls(llm_client=llm_client)

        return self._orchestrator_cls(  # type: ignore[no-any-return]
            config=config.prompt,
            resource_access=components.resource_access,
            scenario_resolver=scenario_resolver,
            slot_extractor=slot_extractor,
            renderer=self._renderer_cls(),
            input_limit=config.input_limits,
            logger=logger,
            provider=str(getattr(llm_client, "provider", "") or ""),
        )
