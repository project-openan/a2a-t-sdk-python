from __future__ import annotations

from dataclasses import dataclass

from a2a_t.common.prompt_resources import (
    PromptResourceLoader,
    PromptResourceRegistry,
    PromptResourceSource,
    ScenarioLoader,
    SlotSchemaLoader,
    TemplateLoader,
)
from a2a_t.prompt.validation.guardrails import SafetyGuardrail
from a2a_t.prompt.validation.slot_validator import SlotValidator


@dataclass(slots=True)
class PromptRuntimeComponents:
    resource_source: PromptResourceSource
    resource_registry: PromptResourceRegistry
    scenario_loader: ScenarioLoader
    template_loader: TemplateLoader
    slot_schema_loader: SlotSchemaLoader
    prompt_resource_loader: PromptResourceLoader
    slot_validator: SlotValidator
    guardrail: SafetyGuardrail
