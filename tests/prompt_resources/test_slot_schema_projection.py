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

"""Tests for the JSON Schema to slot-schema view projection.

Port of the slot-schema expectations of the interim loader tests onto the D31 access layer: the
raw ``slot.json`` document is loaded through :meth:`PromptResourceAccess.slot_schema` and projected
into the flat :class:`SlotSchema` view by :func:`slot_schema_from_json_schema` (the counterpart of
the Java ``PromptSlotJsonSchema.toPromptSlotSchema``).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.common.prompt_resources.models import SlotRange, SlotSchema, slot_schema_from_json_schema
from a2a_t.core.standard_templates import SUBSCRIBE_INCIDENT_URI


def test_projection_reads_the_standard_json_schema_of_a_scenario() -> None:
    data = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "site": {
                "type": "string",
                "description": "Site name",
                "examples": ["Shenzhen site A"],
                "minLength": 1,
                "x-a2at-value-constraint": "Must be a concrete site name.",
            },
            "incident_level": {
                "type": "string",
                "description": "Incident level",
                "examples": ["critical"],
                "enum": ["critical", "major"],
                "x-a2at-value-constraint": "Must be one of the supported levels.",
            },
        },
        "required": ["site"],
    }

    slot_schema = slot_schema_from_json_schema(data, scenario_code="ran-energy-saving")

    assert slot_schema.scenario_code == "ran-energy-saving"
    assert [slot.name for slot in slot_schema.slots] == ["site", "incident_level"]
    assert slot_schema.slots[0].required is True
    assert slot_schema.slots[0].description == "Site name"
    assert slot_schema.slots[0].example == "Shenzhen site A"
    assert slot_schema.slots[0].value_constraint == "Must be a concrete site name."
    assert slot_schema.slots[0].type == "string"
    assert slot_schema.slots[0].allowed_values is None
    assert slot_schema.slots[0].pattern is None
    assert slot_schema.slots[1].required is False
    assert slot_schema.slots[1].allowed_values == ["critical", "major"]


@pytest.mark.parametrize(
    ("property_schema", "expected_type"),
    [
        ({"type": "string"}, "string"),
        ({"x-a2at-slot-type": "list", "type": "string"}, "list"),
    ],
    ids=["json-schema-type", "slot-type-extension-wins"],
)
def test_projection_prefers_the_slot_type_extension(property_schema: dict[str, object], expected_type: str) -> None:
    data = {"type": "object", "properties": {"slot": property_schema}, "required": []}

    slot_schema = slot_schema_from_json_schema(data, scenario_code="scenario")

    assert slot_schema.slots[0].type == expected_type


def test_projection_maps_the_numeric_range_and_pattern_constraints() -> None:
    data = {
        "type": "object",
        "properties": {
            "threshold": {"type": "number", "minimum": 1, "maximum": 100, "pattern": "^\\d+$"},
        },
        "required": [],
    }

    slot_schema = slot_schema_from_json_schema(data, scenario_code="scenario")

    assert slot_schema.slots[0].range == SlotRange(min=1, max=100)
    assert slot_schema.slots[0].pattern == "^\\d+$"


def test_projection_defaults_every_field_when_the_property_schema_is_empty() -> None:
    data = {"type": "object", "properties": {"slot": None}, "required": []}

    slot_schema = slot_schema_from_json_schema(data, scenario_code="scenario")

    assert slot_schema.slots[0].required is False
    assert slot_schema.slots[0].description == ""
    assert slot_schema.slots[0].example == ""
    assert slot_schema.slots[0].value_constraint == ""
    assert slot_schema.slots[0].type is None
    assert slot_schema.slots[0].allowed_values is None
    assert slot_schema.slots[0].range is None
    assert slot_schema.slots[0].pattern is None


def test_projection_yields_no_slots_without_a_properties_object() -> None:
    slot_schema = slot_schema_from_json_schema({"type": "object"}, scenario_code="scenario")

    assert slot_schema == SlotSchema(scenario_code="scenario", slots=[])


def test_access_layer_serves_the_raw_json_schema_and_the_projected_view() -> None:
    from a2a_t.common.prompt_resources import PackagedPromptResourceAccess, PromptResourceAccess

    access = PackagedPromptResourceAccess()
    assert isinstance(access, PromptResourceAccess)
    slot_json_schema = access.slot_schema(SUBSCRIBE_INCIDENT_URI, "en-US")

    assert slot_json_schema.get("type") == "object"
    assert "properties" in slot_json_schema
    slot_schema = slot_schema_from_json_schema(slot_json_schema, scenario_code="subscribe-incident")
    assert [slot.name for slot in slot_schema.slots] == list(slot_json_schema["properties"])


def test_access_layer_serves_the_local_slot_schema_from_the_snapshot(tmp_path: Path) -> None:
    from a2a_t.common.prompt_resources import create
    from a2a_t.config.models import PromptRuntimeConfig

    slot_path = tmp_path / "slots" / "Notification-T" / "network-layer" / "subscribe-incident" / "v1" / "en-US"
    slot_path.mkdir(parents=True)
    (slot_path / "slot.json").write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"custom_field": {"type": "string", "description": "Custom field"}},
                "required": ["custom_field"],
            }
        ),
        encoding="utf-8",
    )

    access = create(PromptRuntimeConfig(source_type="local_file", local_root_dir=str(tmp_path)))
    slot_json_schema = access.slot_schema("subscribe-incident", "en-US")

    assert slot_json_schema["required"] == ["custom_field"]
    slot_schema = slot_schema_from_json_schema(slot_json_schema, scenario_code="subscribe-incident")
    assert [slot.name for slot in slot_schema.slots] == ["custom_field"]
    assert slot_schema.slots[0].required is True


@pytest.mark.parametrize(
    "module_name",
    [
        "local_resources",
        "template_loader",
        "slot_schema_loader",
        "scenario_loader",
        "prompt_resource_loader",
        "errors",
    ],
)
def test_interim_loader_module_is_not_importable(module_name: str) -> None:
    assert importlib.util.find_spec(f"a2a_t.common.prompt_resources.{module_name}") is None


def test_interim_resource_roots_module_is_not_importable() -> None:
    assert importlib.util.find_spec("a2a_t.common.resource_roots") is None
