# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from a2a_t.core.template_uri import TemplateUri

#: Source marker of templates served from the packaged prompt resource tree (Java ``classpath``).
SOURCE_PACKAGED: Final[str] = "packaged"

#: Source marker of templates served from the configured local root.
SOURCE_LOCAL: Final[str] = "local"


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """One loadable prompt template of any A2A-T extension (port of the Java ``PromptTemplate``).

    Attributes:
        template_uri: typed template URI such as ``Task-T/network-layer/ran-energy-saving/v1`` or
            ``Negotiation-T/information-negotiation/propose/v1``.
        description: template description taken from the leading HTML comment of the template file;
            an empty string when the template has no such comment.
        content: full template file text; ``None`` when the template content is unavailable.
        source: effective origin of the loaded template, either :data:`SOURCE_PACKAGED` or
            :data:`SOURCE_LOCAL`.
    """

    template_uri: TemplateUri
    description: str
    content: str | None
    source: str


@dataclass(slots=True)
class ScenarioDefinition:
    """Describe one scenario candidate available to scenario recognition.

    Attributes:
        scenario_code: scenario identifier, such as ``ran-energy-saving``.
        scenario_name: display name of the scenario.
        description: what a request of this scenario sounds like, consumed by recognition.
        example: one example request of the scenario, consumed by recognition.
    """

    scenario_code: str
    scenario_name: str
    description: str
    example: str


@dataclass(slots=True)
class PromptMessages:
    """Bundle the system and user prompts used for one analysis action.

    Attributes:
        system_prompt: system-role instruction of the LLM call.
        user_prompt: user-role payload of the LLM call.
    """

    system_prompt: str
    user_prompt: str


@dataclass(slots=True)
class SlotRange:
    """Describe the optional numeric range constraint of a slot.

    Attributes:
        min: inclusive lower bound; ``None`` when unbounded.
        max: inclusive upper bound; ``None`` when unbounded.
    """

    min: float | int | None
    max: float | int | None


@dataclass(slots=True)
class SlotDefinition:
    """Describe one slot expected by a scenario template.

    Attributes:
        name: slot name as used in the template placeholders.
        required: whether the slot must be filled for the prompt to be valid.
        description: meaning of the slot, injected into the extraction prompt.
        example: one example value, injected into the extraction prompt.
        value_constraint: human-readable constraint of acceptable values.
        type: JSON-schema type of the slot, such as ``string`` or ``integer``; ``None`` when the
            schema declares none.
        allowed_values: closed set of acceptable values; ``None`` when the slot is open.
        range: numeric range constraint; ``None`` when the slot carries none.
        pattern: regular expression the value must match; ``None`` when unconstrained.
    """

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
    """Describe all slots defined for one scenario resource.

    Attributes:
        scenario_code: scenario the schema was resolved for.
        slots: slot definitions of the scenario, in schema order.
    """

    scenario_code: str
    slots: list[SlotDefinition]


def slot_schema_from_json_schema(data: Mapping[str, object], *, scenario_code: str) -> SlotSchema:
    """Project one standard JSON Schema document into the flat slot schema view.

    Port of the Java ``PromptSlotJsonSchema.toPromptSlotSchema`` model projection: the prompt
    pipeline loads the raw ``slot.json`` document through the resource access layer
    (:meth:`~a2a_t.common.prompt_resources.resource_access.PromptResourceAccess.slot_schema`) and
    uses this projection to obtain the slot view consumed by slot extraction. Unknown keys are
    ignored, mirroring the Java ``@JsonIgnoreProperties`` leniency.

    Args:
        data: raw JSON Schema object of a ``slot.json`` resource.
        scenario_code: scenario code the schema was resolved for.

    Returns:
        the flattened slot schema of the document.
    """
    properties = data.get("properties")
    if not isinstance(properties, Mapping):
        properties = {}
    raw_required = data.get("required")
    required_names: set[str] = {str(item) for item in raw_required} if isinstance(raw_required, list) else set()
    slots = [
        _slot_definition_from_property(
            name=str(name),
            property_schema=property_schema,
            required=bool(name in required_names),
        )
        for name, property_schema in properties.items()
    ]
    return SlotSchema(scenario_code=scenario_code, slots=slots)


def _slot_definition_from_property(
    *,
    name: str,
    property_schema: object,
    required: bool,
) -> SlotDefinition:
    """Normalize one JSON Schema property into the shared slot definition model."""
    if not isinstance(property_schema, Mapping):
        property_schema = {}

    examples = property_schema.get("examples")
    example = ""
    if isinstance(examples, list) and examples:
        example = str(examples[0])

    allowed_values = property_schema.get("x-a2at-allowed-values")
    if allowed_values is None:
        allowed_values = property_schema.get("enum")
    if allowed_values is not None and not isinstance(allowed_values, list):
        allowed_values = [allowed_values]

    slot_range = None
    minimum = property_schema.get("minimum")
    maximum = property_schema.get("maximum")
    if minimum is not None or maximum is not None:
        slot_range = SlotRange(min=minimum, max=maximum)

    slot_type = property_schema.get("x-a2at-slot-type")
    if slot_type is None:
        slot_type = property_schema.get("type")

    return SlotDefinition(
        name=name,
        required=required,
        description=str(property_schema.get("description") or ""),
        example=example,
        value_constraint=str(property_schema.get("x-a2at-value-constraint") or ""),
        type=str(slot_type) if slot_type is not None else None,
        allowed_values=allowed_values,
        range=slot_range,
        pattern=str(property_schema["pattern"]) if property_schema.get("pattern") is not None else None,
    )
