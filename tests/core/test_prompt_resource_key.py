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

"""Tests for the prompt resource key (port of Java ``PromptResourceKey``)."""

from __future__ import annotations

import pytest

from a2a_t.core.prompt_resource_key import PromptResourceKey
from a2a_t.core.standard_templates import (
    AUTHORIZATION,
    NEGOTIATION,
    NOTIFICATION,
    TASK,
)
from a2a_t.core.template_uri import TemplateUri

ALL_STANDARD_TEMPLATES = (*TASK, *NOTIFICATION, *AUTHORIZATION, *NEGOTIATION)

LANGUAGES = ("en-US", "zh-CN")


def test_prompt_key_resolves_the_prompt_action_layout() -> None:
    key = PromptResourceKey.prompt("slot_extraction", "en-US", "system.md")

    assert key.category == "prompts"
    assert key.path_segments == ("slot_extraction",)
    assert key.language == "en-US"
    assert key.file_name == "system.md"
    assert key.relative_path() == "prompt_resources/prompts/slot_extraction/en-US/system.md"


def test_scenario_key_has_no_path_segments() -> None:
    key = PromptResourceKey.scenario("zh-CN", "scenarios.json")

    assert key.category == "scenarios"
    assert key.path_segments == ()
    assert key.relative_path() == "prompt_resources/scenarios/zh-CN/scenarios.json"


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("template", ALL_STANDARD_TEMPLATES)
def test_template_key_mirrors_the_template_uri_layout(language: str, template: TemplateUri) -> None:
    key = PromptResourceKey.template(template, language, "template.md")

    assert key.path_segments == template.segments
    assert key.relative_path() == f"prompt_resources/templates/{template.uri}/{language}/template.md"


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("template", ALL_STANDARD_TEMPLATES)
def test_slot_schema_key_mirrors_the_template_uri_layout(language: str, template: TemplateUri) -> None:
    key = PromptResourceKey.slot_schema(template, language, "slot.json")

    assert key.category == "slots"
    assert key.relative_path() == f"prompt_resources/slots/{template.uri}/{language}/slot.json"


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("template", ALL_STANDARD_TEMPLATES)
def test_template_and_slot_schema_keys_share_the_uri_segments(language: str, template: TemplateUri) -> None:
    template_key = PromptResourceKey.template(template, language, "template.md")
    slot_key = PromptResourceKey.slot_schema(template, language, "slot.json")

    assert template_key.path_segments == slot_key.path_segments
    assert template_key.category != slot_key.category


@pytest.mark.parametrize(
    ("field_name", "value", "expected_message"),
    [
        ("category", "", "category must not be blank"),
        ("category", "   ", "category must not be blank"),
        ("path segment", "..", "path segment must be a simple path segment"),
        ("path segment", "a/b", "path segment must be a simple path segment"),
        ("path segment", "a\\b", "path segment must be a simple path segment"),
        ("language", "", "language must not be blank"),
        ("file_name", "", "file_name must not be blank"),
    ],
)
def test_constructor_rejects_invalid_components(field_name: str, value: str, expected_message: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        PromptResourceKey(
            category=value if field_name == "category" else "templates",
            path_segments=(value,) if field_name == "path segment" else ("Task-T", "v1"),
            language=value if field_name == "language" else "en-US",
            file_name=value if field_name == "file_name" else "template.md",
        )

    assert expected_message in str(excinfo.value)


def test_constructor_copies_the_path_segments_defensively() -> None:
    mutable_segments = ["Task-T", "network-layer", "ran-energy-saving", "v1"]

    key = PromptResourceKey("templates", mutable_segments, "en-US", "template.md")  # type: ignore[arg-type]
    mutable_segments.append("extra")

    assert key.path_segments == ("Task-T", "network-layer", "ran-energy-saving", "v1")


def test_prompt_resource_key_is_frozen_and_comparable() -> None:
    key = PromptResourceKey.prompt("slot_extraction", "en-US", "system.md")
    same_key = PromptResourceKey.prompt("slot_extraction", "en-US", "system.md")

    assert key == same_key
    assert hash(key) == hash(same_key)
    assert key != PromptResourceKey.prompt("slot_extraction", "zh-CN", "system.md")

    with pytest.raises(AttributeError):
        key.language = "zh-CN"  # type: ignore[misc]


def test_relative_path_rejects_traversal_only_at_construction() -> None:
    # The relative path is assembled from already-validated components, so it
    # can never escape the prompt_resources root.
    key = PromptResourceKey.template(TemplateUri.of("Task-T", "network-layer", "ran-energy-saving"), "en-US", "t.md")

    assert key.relative_path().startswith("prompt_resources/")
    assert ".." not in key.relative_path()
