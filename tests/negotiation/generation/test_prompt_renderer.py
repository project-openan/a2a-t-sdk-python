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

"""Tests of the negotiation render step (drop blank-slot section policy).

Port of the Java ``NegotiationPromptRendererTest``: slot sections keep only the title plus the
stripped slot value, blank values drop the whole section, the preamble before the first ``## ``
title is discarded, static sections pass through with lenient inline substitution, sections join
with a single blank line, and a ``None`` template text raises the internal
:class:`NegotiationRenderError` the orchestration layer later wraps into ``template.render_failed``.
"""

from __future__ import annotations

import pytest

from a2a_t.negotiation.generation.prompt_renderer import NegotiationRenderError, render

ZH_TEMPLATE = (
    "<!-- information negotiation propose template -->\n"
    "\n"
    "## 协商上下文\n"
    "{{协商上下文}}（必填）\n"
    "要求：\n"
    "必须包含 id、round 与 maxRounds。\n"
    "\n"
    "## 信息协商\n"
    "请根据<所需信息项>补充相关内容。\n"
    "\n"
    "## 所需信息项\n"
    "{{所需信息项}}（必填）\n"
    "要求：\n"
    "提供每个缺失项的名称。\n"
)

EN_TEMPLATE = (
    "<!-- information negotiation propose template -->\n"
    "\n"
    "## Negotiation Context\n"
    "{{Negotiation Context}} (required)\n"
    "Requirements:\n"
    "Every negotiation must include id, round and maxRounds.\n"
    "\n"
    "## Required Information Items\n"
    "{{Required Information Items}} (required)\n"
    "Requirements:\n"
    "Provide the name of each missing item.\n"
)


def test_renders_slot_sections_dropping_requirements_and_static_sections() -> None:
    rendered = render(
        ZH_TEMPLATE,
        {
            "协商上下文": "- id: 3dbc13b5-bd57-4c2b-b503-24e381b6c8d3\n- round: 1\n- maxRounds: 5",
            "所需信息项": "1. 节能区域信息：松山湖",
        },
    )

    assert rendered == (
        "## 协商上下文\n"
        "- id: 3dbc13b5-bd57-4c2b-b503-24e381b6c8d3\n"
        "- round: 1\n"
        "- maxRounds: 5\n"
        "\n"
        "## 信息协商\n"
        "请根据<所需信息项>补充相关内容。\n"
        "\n"
        "## 所需信息项\n"
        "1. 节能区域信息：松山湖"
    )


@pytest.mark.parametrize("missing_value", [None, "   "], ids=["null", "blank"])
def test_drops_whole_slot_section_when_value_is_missing_null_or_blank(missing_value: str | None) -> None:
    rendered = render(
        ZH_TEMPLATE, {"协商上下文": "- id: id-1\n- round: 1\n- maxRounds: 5", "所需信息项": missing_value}
    )

    assert (
        rendered
        == "## 协商上下文\n- id: id-1\n- round: 1\n- maxRounds: 5\n\n## 信息协商\n请根据<所需信息项>补充相关内容。"
    )


def test_drops_leading_html_comment_and_preamble_before_first_section() -> None:
    rendered = render(ZH_TEMPLATE, {"协商上下文": "- id: id-1", "所需信息项": "1. 名称"})

    assert "<!--" not in rendered
    assert "information negotiation propose template" not in rendered
    assert rendered.startswith("## 协商上下文")


def test_recognizes_english_required_and_optional_markers() -> None:
    rendered = render(
        EN_TEMPLATE,
        {
            "Negotiation Context": "- id: id-1",
            "Required Information Items": "1. Energy-saving area information: Songshan Lake",
        },
    )

    assert rendered == (
        "## Negotiation Context\n"
        "- id: id-1\n"
        "\n"
        "## Required Information Items\n"
        "1. Energy-saving area information: Songshan Lake"
    )


def test_recognizes_chinese_optional_marker() -> None:
    template = "## 待澄清内容\n{{待澄清内容}}（选填）\n要求：\n必须能定位到具体字段。\n"

    assert render(template, {"待澄清内容": "1. 节能时间范围：需要澄清"}) == "## 待澄清内容\n1. 节能时间范围：需要澄清"


def test_joins_sections_with_single_blank_line_and_no_trailing_newline() -> None:
    rendered = render(ZH_TEMPLATE, {"协商上下文": "v1", "所需信息项": "v2"})

    assert rendered.endswith("v2")
    assert not rendered.endswith("\n")
    # Three rendered sections are joined by exactly two single blank lines.
    assert rendered.count("\n\n") == 2
    assert "\n\n\n" not in rendered


def test_returns_empty_string_when_every_section_is_dropped() -> None:
    template = "## 协商上下文\n{{协商上下文}}（必填）\n要求：\n上下文要求。\n\n## 所需信息项\n{{所需信息项}}（必填）\n要求：\n信息项要求。\n"

    assert render(template, {}) == ""


def test_keeps_static_sections_when_all_slots_are_empty() -> None:
    rendered = render(ZH_TEMPLATE, {"协商上下文": "", "所需信息项": ""})

    assert rendered == "## 信息协商\n请根据<所需信息项>补充相关内容。"


def test_passes_malformed_braces_through_in_static_sections() -> None:
    template = "## 静态板块\n单花括号 { 不是槽位 }、未闭合 {{ 不是槽位、空名 {{}} 都保持原样。\n"

    rendered = render(template, {"其他槽位": "值"})

    assert rendered == "## 静态板块\n单花括号 { 不是槽位 }、未闭合 {{ 不是槽位、空名 {{}} 都保持原样。"


def test_substitutes_known_slots_inside_static_sections_and_leaves_unknown_ones() -> None:
    template = "## 静态板块\n已知槽位 {{已知}} 与未知槽位 {{未知}} 并存。\n"

    assert render(template, {"已知": "替换值"}) == "## 静态板块\n已知槽位 替换值 与未知槽位 {{未知}} 并存。"


def test_rejects_none_template_text() -> None:
    with pytest.raises(NegotiationRenderError) as info:
        render(None, {})  # type: ignore[arg-type]

    assert str(info.value) == "Negotiation template text must not be null."


def test_ignores_none_slot_map() -> None:
    assert render("## 板块\n静态内容。", None) == "## 板块\n静态内容。"


def test_the_render_failure_stays_outside_the_sdk_error_tree() -> None:
    # The Java NegotiationRenderException is internal (the orchestrator wraps it into the coded
    # template.render_failed generation failure); the port keeps it outside the A2ATError tree.
    from a2a_t.core.errors.exceptions import A2ATError

    with pytest.raises(NegotiationRenderError) as info:
        render(None, {})  # type: ignore[arg-type]

    assert not isinstance(info.value, A2ATError)
