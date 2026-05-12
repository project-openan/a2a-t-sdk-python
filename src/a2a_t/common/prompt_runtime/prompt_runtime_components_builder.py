from __future__ import annotations

import logging
from pathlib import Path

from a2a_t.common.prompt_resources import (
    PromptResourceLoader,
    ScenarioLoader,
    SlotJsonSchemaLoader,
    SlotSchemaLoader,
    TemplateLoader,
)
from a2a_t.config.models import A2ATConfig
from a2a_t.prompt.validation import JsonSchemaSlotValidator, SafetyGuardrailFactory

from .prompt_runtime_components import PromptRuntimeComponents


logger = logging.getLogger(__name__)


class PromptRuntimeComponentsBuilder:
    """Build the shared prompt runtime services used by client and server flows."""

    def build(self, *, config: A2ATConfig) -> PromptRuntimeComponents:
        """Create loaders, validators, and guardrails from the resolved config."""
        prompt_config = config.prompt
        if prompt_config.source_type != "local_file":
            raise ValueError(f"Unsupported prompt resource source_type: {prompt_config.source_type}")

        scenario_loader = ScenarioLoader(root_dir=prompt_config.local_root_dir)
        template_loader = TemplateLoader(root_dir=prompt_config.local_root_dir)
        slot_schema_loader = SlotSchemaLoader(root_dir=prompt_config.local_root_dir)
        slot_json_schema_loader = SlotJsonSchemaLoader(root_dir=prompt_config.local_root_dir)
        self._warn_if_custom_prompts_dir_exists(prompt_config.local_root_dir)
        prompt_resource_loader = PromptResourceLoader()
        json_schema_slot_validator = JsonSchemaSlotValidator()
        guardrail = SafetyGuardrailFactory.create(config.prompt_compliance.guardrail)

        return PromptRuntimeComponents(
            scenario_loader=scenario_loader,
            template_loader=template_loader,
            slot_schema_loader=slot_schema_loader,
            slot_json_schema_loader=slot_json_schema_loader,
            prompt_resource_loader=prompt_resource_loader,
            json_schema_slot_validator=json_schema_slot_validator,
            guardrail=guardrail,
        )

    def _warn_if_custom_prompts_dir_exists(self, local_root_dir: str) -> None:
        """Warn once per build when a custom root contains ignored prompts resources."""
        local_root = Path(local_root_dir).resolve()
        packaged_root = PromptResourceLoader().root_dir.resolve()
        if local_root == packaged_root:
            return

        prompts_dir = local_root / "prompts"
        if prompts_dir.is_dir():
            logger.warning(
                "Custom prompt resource directory contains prompts/, but SDK packaged prompts will be used instead. ignored_dir=%s",
                prompts_dir,
            )
