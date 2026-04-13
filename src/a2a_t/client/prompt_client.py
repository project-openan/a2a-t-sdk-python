"""Prompt-enabled client for template-based requests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from a2a_t.config.models import A2ATConfig
from a2a_t.llm.client import LLMClient
from a2a_t.prompt.analysis import ScenarioRecognizer, SlotExtractor
from a2a_t.prompt.resources import PromptResourceLoader, ScenarioLoader, SlotSchemaLoader, TemplateLoader
from a2a_t.prompt.validation import SlotValidator

from .prompt.prompt_generation_orchestrator import PromptGenerationOrchestrator


def _default_env_path() -> Path:
    return Path(__file__).resolve().parents[3] / "package_data" / ".env"


class PromptClient:
    """Client that constructs requests using prompt templates."""

    def __init__(
        self,
        *,
        orchestrator: PromptGenerationOrchestrator | None = None,
        env_path: Path | None = None,
        llm_client: LLMClient | None = None,
        resource_root: str | Path | None = None,
    ) -> None:
        if orchestrator is not None:
            self._orchestrator = orchestrator
            return

        resolved_env_path = env_path or _default_env_path()
        config = A2ATConfig.load(resolved_env_path)
        resolved_llm_client = llm_client or LLMClient(env_path=resolved_env_path)
        self._orchestrator = PromptGenerationOrchestrator(
            config=config,
            scenario_loader=ScenarioLoader(root_dir=resource_root),
            prompt_resource_loader=PromptResourceLoader(root_dir=resource_root),
            template_loader=TemplateLoader(root_dir=resource_root),
            slot_schema_loader=SlotSchemaLoader(root_dir=resource_root),
            scenario_recognizer=ScenarioRecognizer(llm_client=resolved_llm_client),
            slot_extractor=SlotExtractor(llm_client=resolved_llm_client),
            slot_validator=SlotValidator(),
        )

    def generate_a2a_t_prompt(self, user_input: str | dict[str, object]) -> Any:
        return self._orchestrator.generate(user_input)

    def send_with_template(self, template_name: str, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("send_with_template is superseded by generate_a2a_t_prompt().")
