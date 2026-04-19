from __future__ import annotations

from a2a_t.prompt.common.models import PromptReference

from .errors import PromptResourceNotFoundError
from .models import PromptMessages, ScenarioDefinition, SlotSchema
from .prompt_resource_loader import PromptResourceLoader
from .scenario_loader import ScenarioLoader
from .slot_schema_loader import SlotSchemaLoader
from .source import PromptResourceSource
from .template_loader import TemplateLoader


class PromptResourceRegistry:
    """Resolve prompt resources with shared language fallback behavior."""

    def __init__(
        self,
        *,
        source: PromptResourceSource | None = None,
        root_dir: str | None = None,
        scenario_loader: ScenarioLoader | None = None,
        prompt_resource_loader: PromptResourceLoader | None = None,
        template_loader: TemplateLoader | None = None,
        slot_schema_loader: SlotSchemaLoader | None = None,
    ) -> None:
        self._scenario_loader = scenario_loader or ScenarioLoader(source=source, root_dir=root_dir)
        self._prompt_resource_loader = prompt_resource_loader or PromptResourceLoader(source=source, root_dir=root_dir)
        self._template_loader = template_loader or TemplateLoader(source=source, root_dir=root_dir)
        self._slot_schema_loader = slot_schema_loader or SlotSchemaLoader(source=source, root_dir=root_dir)

    def load_scenario_resources(
        self,
        *,
        version: str,
        language: str,
    ) -> tuple[str, list[ScenarioDefinition], PromptMessages]:
        resolved_language, payload = self._load_with_language_fallback(
            language=language,
            loader=lambda resolved_language: (
                self._scenario_loader.load(version=version, language=resolved_language),
                self._prompt_resource_loader.load(
                    analysis_action="scenario_recognition",
                    version=version,
                    language=resolved_language,
                ),
            ),
        )
        return resolved_language, payload[0], payload[1]

    def load_generation_resources(
        self,
        *,
        reference: PromptReference,
    ) -> tuple[PromptReference, str, SlotSchema, PromptMessages]:
        resolved_language, payload = self._load_with_language_fallback(
            language=reference.language,
            loader=lambda language: (
                self._template_loader.load(
                    reference=PromptReference(
                        scenario_code=reference.scenario_code,
                        version=reference.version,
                        language=language,
                    )
                ),
                self._slot_schema_loader.load(
                    reference=PromptReference(
                        scenario_code=reference.scenario_code,
                        version=reference.version,
                        language=language,
                    )
                ),
                self._prompt_resource_loader.load(
                    analysis_action="slot_extraction",
                    version=reference.version,
                    language=language,
                ),
            ),
        )
        return (
            PromptReference(
                scenario_code=reference.scenario_code,
                version=reference.version,
                language=resolved_language,
            ),
            payload[0],
            payload[1],
            payload[2],
        )

    def _load_with_language_fallback(
        self,
        *,
        language: str,
        loader,
    ):
        try:
            return language, loader(language)
        except PromptResourceNotFoundError:
            if language == "en-US":
                raise
            return "en-US", loader("en-US")
