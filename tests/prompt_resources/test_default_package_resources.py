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

"""Default packaged bundle tests through the resource access layer.

Replaces the interim loader tests: the packaged prompt resources are read through a
``PackagedPromptResourceAccess`` (the default source of the D31 access layer), covering the
scenario catalogs, task templates, slot schemas and the instruction prompts of both languages.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.common.prompt_resources import PackagedPromptResourceAccess
from a2a_t.common.prompt_resources.models import slot_schema_from_json_schema
from a2a_t.core.standard_templates import SUBSCRIBE_INCIDENT_URI

ACCESS = PackagedPromptResourceAccess()


@pytest.mark.parametrize("language", ["zh-CN", "en-US"], ids=lambda language: language)
def test_default_package_resources_include_the_scenario_catalog(language: str) -> None:
    scenarios = ACCESS.load_scenarios(language)

    assert any(item.scenario_code == "subscribe-incident" for item in scenarios)
    assert all(item.scenario_code and item.scenario_name for item in scenarios)


@pytest.mark.parametrize("language", ["zh-CN", "en-US"], ids=lambda language: language)
def test_default_package_resources_include_the_subscribe_incident_template_and_slots(language: str) -> None:
    template_text = ACCESS.template_text(SUBSCRIBE_INCIDENT_URI, language)
    slot_json_schema = ACCESS.slot_schema(SUBSCRIBE_INCIDENT_URI, language)

    assert "{{" in template_text
    assert slot_json_schema.get("type") == "object"
    assert "properties" in slot_json_schema
    slot_schema = slot_schema_from_json_schema(slot_json_schema, scenario_code="subscribe-incident")
    assert slot_schema.slots


@pytest.mark.parametrize("language", ["zh-CN", "en-US"], ids=lambda language: language)
@pytest.mark.parametrize(
    "analysis_action",
    ["scenario_recognition", "slot_extraction"],
    ids=lambda action: action,
)
def test_default_package_resources_include_the_analysis_prompts(analysis_action: str, language: str) -> None:
    system_prompt = ACCESS.load_prompt(analysis_action, language, "system.md")
    user_prompt = ACCESS.load_prompt(analysis_action, language, "user.md")

    assert system_prompt.strip()
    assert user_prompt.strip()


@pytest.mark.parametrize("language", ["zh-CN", "en-US"], ids=lambda language: language)
@pytest.mark.parametrize(
    "analysis_action",
    [
        "information_negotiation",
        "target_negotiation",
        "feasibility_negotiation",
    ],
    ids=lambda action: action,
)
def test_default_package_resources_include_the_negotiation_prompts(analysis_action: str, language: str) -> None:
    system_prompt = ACCESS.load_prompt(analysis_action, language, "system.md")
    user_prompt = ACCESS.load_prompt(analysis_action, language, "user.md")

    assert system_prompt.strip(), analysis_action
    assert user_prompt.strip(), analysis_action
