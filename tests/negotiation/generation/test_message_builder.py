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

"""Tests of the LLM message-list assembly of one negotiation LLM step.

Port of the message-builder rows of the Java ``NegotiationPromptResourceLoaderTest``. The Java
builder holds its own classpath ``NegotiationPromptResourceLoader``; this port consumes the common
resource access layer (D31), so the loader rows become: every bundled negotiation category and
language resolves non-blank prompts, a missing prompt is the coded ``infra.resource_read_failed``
(the layer's replacement of the Java ``ResourceNotFoundException``), path-like arguments are
rejected as programming errors, the system prompt passes verbatim, and every literal bracket token
of the user prompt is replaced by its supplied value.
"""

from __future__ import annotations

import pytest

from a2a_t.core.errors.exceptions import A2ATError
from a2a_t.core.standard_templates import INFORMATION_NEGOTIATION_PROPOSE_URI
from a2a_t.negotiation.generation.message_builder import (
    TOKEN_INPUT,
    TOKEN_NEGOTIATION_TYPE,
    TOKEN_PHASE,
    TOKEN_SCHEMA,
    TOKEN_TEMPLATE_URI,
    build_messages,
)

NEGOTIATION_CATEGORIES = (
    "information_negotiation",
    "target_negotiation",
    "feasibility_negotiation",
    "negotiation_semantic_validation",
    "abort_negotiation",
)


@pytest.mark.parametrize("category", NEGOTIATION_CATEGORIES)
@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_builds_non_blank_system_and_user_prompts_of_every_category_and_language(category: str, language: str) -> None:
    messages = build_messages(category, language)

    assert [message["role"] for message in messages] == ["system", "user"]
    assert all(message["content"].strip() for message in messages)
    assert all(set(message) == {"role", "content"} for message in messages)


def test_missing_prompts_fail_with_the_coded_resource_read_failure() -> None:
    with pytest.raises(A2ATError) as info:
        build_messages("unknown_negotiation", "zh-CN")

    assert info.value.code.value == "infra.resource_read_failed"
    assert "unknown_negotiation" in str(info.value)

    with pytest.raises(A2ATError):
        build_messages("information_negotiation", "fr-FR")


@pytest.mark.parametrize(
    ("category", "language"),
    [("../information_negotiation", "zh-CN"), ("information_negotiation", "../zh-CN")],
    ids=["path-like-category", "path-like-language"],
)
def test_rejects_path_like_arguments(category: str, language: str) -> None:
    with pytest.raises(ValueError, match="simple path segment"):
        build_messages(category, language)


def test_uses_the_system_prompt_verbatim_and_replaces_the_user_prompt_tokens() -> None:
    messages = build_messages(
        "information_negotiation",
        "zh-CN",
        {
            TOKEN_PHASE: "propose",
            TOKEN_INPUT: "请提供故障发生时间。",
        },
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    system_prompt = build_messages("information_negotiation", "zh-CN")[0]["content"]
    assert messages[0]["content"] == system_prompt
    user_prompt = messages[1]["content"]
    assert "协商阶段：propose" in user_prompt
    assert "请提供故障发生时间。" in user_prompt
    assert "[phase]" not in user_prompt
    assert "[input]" not in user_prompt


def test_replaces_the_semantic_validation_tokens() -> None:
    messages = build_messages(
        "negotiation_semantic_validation",
        "en-US",
        {
            TOKEN_NEGOTIATION_TYPE: "information",
            TOKEN_TEMPLATE_URI: INFORMATION_NEGOTIATION_PROPOSE_URI,
            TOKEN_SCHEMA: '{"type":"object"}',
            TOKEN_INPUT: "the message text",
        },
    )

    user_prompt = messages[1]["content"]
    assert "Declared negotiation type: information" in user_prompt
    assert f"Declared template identifier: {INFORMATION_NEGOTIATION_PROPOSE_URI}" in user_prompt
    assert "Parameter schema:" in user_prompt
    assert '{"type":"object"}' in user_prompt
    assert "Negotiation message to validate:" in user_prompt
    assert "the message text" in user_prompt
    assert "[negotiation_type]" not in user_prompt
    assert "[template_uri]" not in user_prompt
    assert "[schema]" not in user_prompt
    assert "[input]" not in user_prompt


def test_a_none_token_value_is_replaced_with_an_empty_string() -> None:
    messages = build_messages("information_negotiation", "zh-CN", {TOKEN_PHASE: None})

    assert "[phase]" not in messages[1]["content"]


def test_bracket_tokens_without_a_supplied_value_are_left_unchanged() -> None:
    messages = build_messages("information_negotiation", "zh-CN", {TOKEN_INPUT: "文本"})

    assert "[phase]" in messages[1]["content"]
    assert "文本" in messages[1]["content"]
