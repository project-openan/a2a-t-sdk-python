from __future__ import annotations

from dataclasses import dataclass

from a2a_t.common.prompt_resources import (
    PromptResourceLoader,
    ScenarioLoader,
    SlotJsonSchemaLoader,
    SlotSchemaLoader,
    TemplateLoader,
)
from a2a_t.prompt.validation.json_schema_slot_validator import JsonSchemaSlotValidator


@dataclass(slots=True)
class PromptRuntimeComponents:
    """Group the shared prompt runtime services built from configuration."""

    scenario_loader: ScenarioLoader
    template_loader: TemplateLoader
    slot_schema_loader: SlotSchemaLoader
    slot_json_schema_loader: SlotJsonSchemaLoader
    prompt_resource_loader: PromptResourceLoader
    json_schema_slot_validator: JsonSchemaSlotValidator
