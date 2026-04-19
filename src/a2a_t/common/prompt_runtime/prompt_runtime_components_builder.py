from __future__ import annotations

from a2a_t.common.prompt_resources import (
    LocalPromptResourceSource,
    PromptResourceLoader,
    PromptResourceRegistry,
    ScenarioLoader,
    SlotSchemaLoader,
    TemplateLoader,
)
from a2a_t.config.models import A2ATConfig
from a2a_t.prompt.validation import SafetyGuardrailFactory, SlotValidator

from .prompt_runtime_components import PromptRuntimeComponents


class PromptRuntimeComponentsBuilder:
    def build(self, *, config: A2ATConfig) -> PromptRuntimeComponents:
        prompt_config = config.prompt
        if prompt_config.source_type != "local_file":
            raise ValueError(f"Unsupported prompt resource source_type: {prompt_config.source_type}")

        resource_source = LocalPromptResourceSource(
            root_dir=prompt_config.local_root_dir,
            cache=None,
        )
        scenario_loader = ScenarioLoader(source=resource_source)
        template_loader = TemplateLoader(source=resource_source)
        slot_schema_loader = SlotSchemaLoader(source=resource_source)
        prompt_resource_loader = PromptResourceLoader(source=resource_source)
        resource_registry = PromptResourceRegistry(
            source=resource_source,
            scenario_loader=scenario_loader,
            prompt_resource_loader=prompt_resource_loader,
            template_loader=template_loader,
            slot_schema_loader=slot_schema_loader,
        )
        slot_validator = SlotValidator()
        guardrail = SafetyGuardrailFactory.create(config.prompt_compliance.guardrail)

        return PromptRuntimeComponents(
            resource_source=resource_source,
            resource_registry=resource_registry,
            scenario_loader=scenario_loader,
            template_loader=template_loader,
            slot_schema_loader=slot_schema_loader,
            prompt_resource_loader=prompt_resource_loader,
            slot_validator=slot_validator,
            guardrail=guardrail,
        )
