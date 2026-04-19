from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ScenarioDefinition:
    scenario_code: str
    scenario_name: str
    description: str
    example: str


@dataclass(slots=True)
class PromptMessages:
    system_prompt: str
    user_prompt: str


@dataclass(slots=True)
class SlotRange:
    min: float | int | None
    max: float | int | None


@dataclass(slots=True)
class SlotDefinition:
    name: str
    required: bool
    description: str
    example: str
    value_constraint: str
    type: str | None
    allowed_values: list[object] | None
    range: SlotRange | None
    pattern: str | None


@dataclass(slots=True)
class SlotSchema:
    scenario_code: str
    version: str
    slots: list[SlotDefinition]
