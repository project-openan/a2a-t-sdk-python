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

"""Tests for the sectioned template renderer and its two blank-slot policies.

Every expectation below is ported from the Java 1.1.0 test suite
(``a2a-t-prompt/src/test/.../taskrendering/``): ``TaskPromptRendererTest`` for the collapse
policy, ``DropBlankSlotSectionRendererTest`` for the drop policy and
``RealTemplateRenderingContractTest`` for the packaged template resources. The two policies
are deliberately not interchangeable (D12), so the differential tests pin the documented
shape difference for the same input: the collapse policy preserves the template
scaffolding — blank slot sections keep their title plus a single blank separator line —
while the drop policy removes blank slot sections entirely, title included, discards any
preamble before the first ``## `` title and never emits a trailing newline.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.resources import files

import pytest

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.prompt.task_rendering import collapse_sections, drop_sections
from a2a_t.prompt.task_rendering.errors import TaskPromptRenderError

# --------------------------------------------------------------------------
# Collapse policy — Java TaskPromptRendererTest, ported case by case.
# --------------------------------------------------------------------------

# (case id, template text, slot values, expected rendered output)
JAVA_COLLAPSE_CASES = [
    (
        "plain-body",
        "Site: {site}\nNotes: {additional_notes}",
        {"site": "Site A", "additional_notes": ""},
        "Site: Site A\nNotes: ",
    ),
    (
        "blank-slot-section-keeps-title-and-single-separator",
        "## Task Type\n"
        "{{task_type}} (Required)\n\n"
        "Requirement: provide the task type.\n\n"
        "## Condition\n"
        "{{condition}} (Optional)\n\n"
        "Requirement: describe the condition.\n\n"
        "## Expected Output\n"
        "{{expected_output}} (Optional)\n",
        {"task_type": "Diagnosis", "condition": "", "expected_output": "Return a structured diagnosis result."},
        "## Task Type\nDiagnosis\n\n## Condition\n\n## Expected Output\nReturn a structured diagnosis result.\n",
    ),
    (
        "double-braced-placeholders",
        "Topic: {{topic}}\nCondition: {{condition}}",
        {"topic": "Incident", "condition": "critical alert"},
        "Topic: Incident\nCondition: critical alert",
    ),
    (
        "double-braced-placeholders-longer-values",
        "Topic: {{topic}}\nCondition: {{condition}}",
        {"topic": "Incident", "condition": "Severity is critical"},
        "Topic: Incident\nCondition: Severity is critical",
    ),
    (
        "standalone-slot-with-suffix-without-space",
        "## Task Type\n"
        "Diagnosis\n\n"
        "## Task Target\n"
        "{{task_target}}(Required)\n\n"
        "Requirement: explain the target.\n"
        "Example: complete the diagnosis.\n\n"
        "## Expected Output\n"
        "{{expected_output}}(Optional)\n",
        {
            "task_target": "Complete the diagnosis and provide remediation advice.",
            "expected_output": "Return a structured diagnosis result.",
        },
        "## Task Type\n"
        "Diagnosis\n\n"
        "## Task Target\n"
        "Complete the diagnosis and provide remediation advice.\n\n"
        "## Expected Output\n"
        "Return a structured diagnosis result.\n",
    ),
    (
        "standalone-slot-with-parenthesized-suffix",
        "## Task Target\n"
        "{{task_target}} (Required)\n\n"
        "Requirement: explain the target.\n"
        "Example: complete the diagnosis.\n\n"
        "## Expected Output\n"
        "{{expected_output}} (Optional)\n",
        {
            "task_target": "Complete the fault diagnosis and provide remediation advice.",
            "expected_output": "Return a structured diagnosis result.",
        },
        "## Task Target\n"
        "Complete the fault diagnosis and provide remediation advice.\n\n"
        "## Expected Output\n"
        "Return a structured diagnosis result.\n",
    ),
    (
        "regular-inline-placeholder-content-is-preserved",
        "## Subscription\n"
        "Please subscribe to {{topic}} incidents.\n\n"
        "## Condition\n"
        "{{condition}} (Optional)\n"
        "Requirement: describe the filter.\n",
        {"topic": "network", "condition": "critical only"},
        "## Subscription\nPlease subscribe to network incidents.\n\n## Condition\ncritical only\n",
    ),
    (
        "single-brace-example-text-is-kept-verbatim",
        "Rate target: {00:00~06:00,2Mbps}\nSite: {{site}}",
        {"site": "Site A"},
        "Rate target: {00:00~06:00,2Mbps}\nSite: Site A",
    ),
    (
        "standalone-slot-with-lowercase-marker",
        "## Task Target\n"
        "{{task_target}} (required)\n\n"
        "Requirement: explain the target.\n\n"
        "## Expected Output\n"
        "{{expected_output}} (optional)\n",
        {"task_target": "Do X.", "expected_output": "Y."},
        "## Task Target\nDo X.\n\n## Expected Output\nY.\n",
    ),
    (
        "standalone-slot-with-full-width-chinese-marker",
        "## 操作类型\n{{操作类型}}（必填）\n\n要求：\n请提供操作类型。\n\n## 任务目标\n{{任务目标}}（选填）\n",
        {"操作类型": "创建", "任务目标": "节能最大化"},
        "## 操作类型\n创建\n\n## 任务目标\n节能最大化\n",
    ),
    (
        "standalone-slot-with-long-conditional-suffix",
        "## Expected Output\n"
        "{{expected_output}} (optional when creating, not required when modifying)\n\n"
        "Requirement: describe the output format.\n",
        {"expected_output": "Report."},
        "## Expected Output\nReport.\n",
    ),
    (
        "standalone-slot-with-bare-placeholder",
        "## Task Target\n{{task_target}}\n\nRequirement: explain the target.\n",
        {"task_target": "Do X."},
        "## Task Target\nDo X.\n",
    ),
    (
        "single-brace-example-first-line-does-not-collapse",
        "## Task Context\n{00:00~06:00,2Mbps}\n\n{{task_context}} (optional)\n",
        {"task_context": "Context value."},
        "## Task Context\n{00:00~06:00,2Mbps}\n\nContext value. (optional)\n",
    ),
]


@pytest.mark.parametrize(("case_id", "template_text", "values", "expected"), JAVA_COLLAPSE_CASES)
def test_collapse_sections_matches_java_task_prompt_renderer(
    case_id: str, template_text: str, values: dict[str, str | None], expected: str
) -> None:
    assert collapse_sections(template_text, values) == expected


@pytest.mark.parametrize("blank_value", [None, "", "   "], ids=["none", "empty", "whitespace"])
def test_collapse_sections_treats_null_empty_and_whitespace_slot_values_as_blank(blank_value: str | None) -> None:
    """Java ``isBlank()`` semantics: every blank spelling keeps title + one separator line."""
    template_text = "## Task Type\n{{task_type}} (Required)\n\nRequirement: provide.\n\n## Notes\nstatic\n"

    rendered = collapse_sections(template_text, {"task_type": blank_value})

    assert rendered == "## Task Type\n\n## Notes\nstatic\n"


def test_collapse_sections_renders_null_inline_slot_value_as_blank() -> None:
    """The same value semantics as the legacy task prompt renderer: ``None`` renders blank."""
    assert collapse_sections(
        "Site: {site}\nNotes: {additional_notes}", {"site": "Site A", "additional_notes": None}
    ) == ("Site: Site A\nNotes: ")


def test_collapse_sections_normalizes_crlf_line_endings() -> None:
    """Java ``normalizeLineEndings``: CRLF and CR become LF before the section split."""
    assert collapse_sections(
        "## Task Type\r\n{{task_type}} (required)\r\nRequirement: describe.\r\n", {"task_type": "V"}
    ) == ("## Task Type\nV\n")


def test_collapse_sections_accepts_a_null_value_map() -> None:
    """Java parity: a ``null`` slot map behaves as an empty map."""
    assert collapse_sections("## Static\nContent.\n", None) == "## Static\nContent.\n"


# --------------------------------------------------------------------------
# Collapse policy — failure paths (Java TaskPromptRenderException mapping).
# --------------------------------------------------------------------------


def test_collapse_sections_raises_coded_error_for_unknown_double_braced_slot() -> None:
    with pytest.raises(TaskPromptRenderError) as excinfo:
        collapse_sections("Site: {{site}}\nTime Range: {{time_range}}", {"site": "Site A"})

    assert excinfo.value.code is ErrorCatalog.TEMPLATE_RENDER_FAILED
    assert excinfo.value.code_str == "template.render_failed"
    assert excinfo.value.facts["reason"] == "Unknown slot referenced by template: time_range"


def test_collapse_sections_raises_coded_error_for_unbalanced_braces() -> None:
    with pytest.raises(TaskPromptRenderError) as excinfo:
        collapse_sections("Site: {{site}", {"site": "Site A"})

    assert excinfo.value.code is ErrorCatalog.TEMPLATE_RENDER_FAILED
    assert excinfo.value.facts["reason"] == "Template text has unbalanced braces."


def test_collapse_sections_keeps_unknown_single_braced_text_verbatim_instead_of_failing() -> None:
    """Single-brace unknown text is example prose, not an error (Java parity)."""
    assert collapse_sections("Site: {site}\nTime Range: {time_range}", {"site": "Site A"}) == (
        "Site: Site A\nTime Range: {time_range}"
    )


@pytest.mark.parametrize("render", [collapse_sections, drop_sections], ids=["collapse", "drop"])
def test_renderers_reject_a_missing_template_text(render: Callable[..., str]) -> None:
    """A missing template is a programming error outside the error tree (Java NPE guard)."""
    with pytest.raises(TypeError, match="Template text must not be null."):
        render(None, {})


@pytest.mark.parametrize("render", [collapse_sections, drop_sections], ids=["collapse", "drop"])
def test_renderers_reject_non_string_template_text(render: Callable[..., str]) -> None:
    with pytest.raises(TypeError, match="Template text must be a string"):
        render(42, {})


@pytest.mark.parametrize("render", [collapse_sections, drop_sections], ids=["collapse", "drop"])
def test_renderers_reject_non_mapping_slot_values(render: Callable[..., str]) -> None:
    with pytest.raises(TypeError, match="Slot values must be a mapping"):
        render("## A\ncontent", ["not", "a", "mapping"])


# --------------------------------------------------------------------------
# Drop policy — Java DropBlankSlotSectionRendererTest, ported case by case.
# --------------------------------------------------------------------------

# (case id, template text, slot values, expected rendered output)
JAVA_DROP_CASES = [
    (
        "slot-section-with-lowercase-english-marker",
        "## Slot\n{{slot}} (required)\nRequirements:\nSome requirements.\n",
        {"slot": "value"},
        "## Slot\nvalue",
    ),
    (
        "slot-section-with-full-width-chinese-marker",
        "## 槽位\n{{槽位}}（必填）\n要求：\n一些要求。\n",
        {"槽位": "值"},
        "## 槽位\n值",
    ),
    (
        "slot-section-with-chinese-optional-variant",
        "## 订阅条件\n{{订阅条件}}（可选）\n要求：\n提供订阅条件。\n",
        {"订阅条件": "critical"},
        "## 订阅条件\ncritical",
    ),
    (
        "slot-section-with-chinese-required-variant",
        "## 通知主题\n{{通知主题}}（必选）\n要求：\n提供通知主题。\n",
        {"通知主题": "Incident"},
        "## 通知主题\nIncident",
    ),
    (
        "slot-section-with-bare-placeholder",
        "## Slot\n{{slot}}\nRequirements:\nSome requirements.\n",
        {"slot": "value"},
        "## Slot\nvalue",
    ),
    (
        "slot-section-with-long-conditional-suffix",
        "## Expected Output\n{{expected_output}} (optional when creating, not required when modifying)\n"
        "Requirement:\nDescribe the output format.\n",
        {"expected_output": "Report."},
        "## Expected Output\nReport.",
    ),
    (
        "static-section-passes-through",
        "## Static\nSome static content.\n",
        {},
        "## Static\nSome static content.",
    ),
    (
        "sections-are-joined-with-a-single-blank-line",
        "## Section A\nContent A.\n\n## Section B\nContent B.\n",
        {},
        "## Section A\nContent A.\n\n## Section B\nContent B.",
    ),
    (
        "all-sections-dropped-renders-empty-string",
        "## Slot A\n{{slot_a}} (required)\nRequirements.\n\n## Slot B\n{{slot_b}} (required)\nRequirements.\n",
        {},
        "",
    ),
]


@pytest.mark.parametrize(("case_id", "template_text", "values", "expected"), JAVA_DROP_CASES)
def test_drop_sections_matches_java_drop_blank_slot_section_renderer(
    case_id: str, template_text: str, values: dict[str, str | None], expected: str
) -> None:
    assert drop_sections(template_text, values) == expected


@pytest.mark.parametrize(
    ("case_id", "values"),
    [
        ("missing-key", {}),
        ("none-value", {"slot": None}),
        ("empty-value", {"slot": ""}),
        ("whitespace-value", {"slot": "   "}),
    ],
)
def test_drop_sections_drops_slot_section_when_value_is_null_or_blank(
    case_id: str, values: dict[str, str | None]
) -> None:
    """A null or blank slot value removes the whole section, title included."""
    template_text = "## Slot\n{{slot}} (required)\nRequirements:\nSome requirements.\n"

    assert drop_sections(template_text, values) == ""


def test_drop_sections_drops_heading_and_leaves_no_blank_line_debris() -> None:
    """Adjacent dropped sections must not leave blank-line debris between the survivors."""
    template_text = (
        "## Kept First\nContent A.\n\n"
        "## Dropped One\n{{dropped_one}} (optional)\nRequirement: describe.\n\n"
        "## Dropped Two\n{{dropped_two}} (optional)\nRequirement: describe.\n\n"
        "## Kept Last\nContent B.\n"
    )

    rendered = drop_sections(template_text, {})

    assert rendered == "## Kept First\nContent A.\n\n## Kept Last\nContent B."


def test_drop_sections_discards_content_before_the_first_title() -> None:
    """The preamble — including a leading HTML description comment — never survives."""
    template_text = (
        "<!-- Negotiation message template: propose -->\nDescription prose.\n\n## Section\n{{slot}} (required)\n"
    )

    assert drop_sections(template_text, {"slot": "value"}) == "## Section\nvalue"
    assert drop_sections(template_text, {}) == ""


def test_drop_sections_returns_empty_string_when_no_title_line_exists() -> None:
    assert drop_sections("plain prose without any section title", {}) == ""


def test_drop_sections_does_not_identify_slot_section_when_first_line_is_single_brace_example() -> None:
    template_text = "## Section\n{00:00~06:00,2Mbps}\n\n{{slot}} (optional)\n"

    rendered = drop_sections(template_text, {"slot": "value"})

    assert "{00:00~06:00,2Mbps}" in rendered
    assert "value" in rendered
    assert "(optional)" in rendered


def test_drop_sections_keeps_blank_inline_placeholders_in_static_sections_verbatim() -> None:
    """The drop policy removes whole slot sections only; blank inline placeholders stay."""
    template_text = "## Static\nValue: {{known}} and {{unknown}}\n"

    assert drop_sections(template_text, {"known": ""}) == "## Static\nValue: {{known}} and {{unknown}}"
    assert drop_sections(template_text, {"known": "x"}) == "## Static\nValue: x and {{unknown}}"


def test_drop_sections_strips_the_slot_value_and_never_emits_a_trailing_newline() -> None:
    assert (
        drop_sections("## Slot\n{{slot}} (required)\nReq.\n", {"slot": "  padded value  "}) == "## Slot\npadded value"
    )


def test_drop_sections_renders_crlf_slot_sections_with_normalized_output() -> None:
    """Java parity: the drop policy never normalizes line endings, but titles and slot
    values are stripped, so CRLF slot sections still render with LF output."""
    assert drop_sections("## Slot\r\n{{slot}} (required)\r\nReq.\r\n", {"slot": "value"}) == "## Slot\nvalue"


# --------------------------------------------------------------------------
# Differential tests — the two policies are deliberately not interchangeable.
# --------------------------------------------------------------------------


def test_policies_differ_for_a_blank_slot_section() -> None:
    """Collapse keeps the section scaffolding; drop removes the section entirely."""
    template_text = "## Task Type\n{{task_type}} (required)\nRequirement: describe.\n\n## Notes\n{{notes}} (optional)\n"
    values = {"task_type": "Diagnosis", "notes": None}

    assert collapse_sections(template_text, values) == "## Task Type\nDiagnosis\n\n## Notes\n"
    assert drop_sections(template_text, values) == "## Task Type\nDiagnosis"


def test_policies_differ_for_content_before_the_first_title() -> None:
    """Collapse retains the preamble; the drop policy discards it."""
    template_text = "<!-- description -->\nPreamble line.\n\n## Section\n{{slot}} (required)\nReq.\n"
    values = {"slot": "value"}

    assert collapse_sections(template_text, values) == "<!-- description -->\nPreamble line.\n\n## Section\nvalue\n"
    assert drop_sections(template_text, values) == "## Section\nvalue"


def test_policies_differ_in_trailing_newline_and_separator_shape() -> None:
    """Collapse mirrors the template's own line shape; drop joins sections with one blank
    line and never emits a trailing newline."""
    template_text = "## Section A\n{{slot_a}} (required)\nReq.\n\n## Section B\nstatic B\n"
    values = {"slot_a": "A value"}

    assert collapse_sections(template_text, values) == "## Section A\nA value\n\n## Section B\nstatic B\n"
    assert drop_sections(template_text, values) == "## Section A\nA value\n\n## Section B\nstatic B"


def test_policies_differ_minimally_when_every_slot_is_filled() -> None:
    """Even a fully filled template differs: collapse preserves the template's trailing
    newline while drop never emits one."""
    template_text = "## Slot\n{{slot}} (required)\nReq.\n"
    values = {"slot": "value"}

    assert collapse_sections(template_text, values) == "## Slot\nvalue\n"
    assert drop_sections(template_text, values) == "## Slot\nvalue"


# --------------------------------------------------------------------------
# Packaged template contract tests — Java RealTemplateRenderingContractTest.
# --------------------------------------------------------------------------


def _load_template(relative_path: str) -> str:
    """Read one packaged template resource (D8: ``importlib.resources`` access)."""
    resource = files("a2a_t").joinpath("prompt_resources", "templates", *relative_path.split("/"))
    return resource.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("template_uri", "values", "scaffolding_marker"),
    [
        (
            "Task-T/network-layer/ran-energy-saving/v1/en-US/template.md",
            {
                "operation_type": "Create",
                "task_description": "Energy saving plan.",
                "task_object": "Area: Songshanhu",
                "task_target": "Maximize energy saving.",
                "task_context": "NR; full period.",
                "expected_output": "Report.",
            },
            "Requirement:",
        ),
        (
            "Task-T/network-layer/ran-energy-saving/v1/zh-CN/template.md",
            {
                "操作类型": "创建",
                "任务描述": "节能方案。",
                "任务对象": "区域：松山湖",
                "任务目标": "最大化节能。",
                "任务上下文": "NR；全时段。",
                "预期输出": "报告。",
            },
            "要求：",
        ),
        (
            "Authorization-T/authorization-policy-management/v1/en-US/template.md",
            {
                "authorization_policy_operation_type": "add authorization policy",
                "network_operation_authorization_policy_list": "Policy list content.",
            },
            "Requirement:",
        ),
        (
            "Authorization-T/authorization-policy-management/v1/zh-CN/template.md",
            {
                "授权策略的操作类型": "新增授权策略",
                "动网操作的授权策略列表": "策略列表内容。",
            },
            "要求：",
        ),
        (
            "Notification-T/network-layer/subscribe-incident/v1/zh-CN/template.md",
            {"通知主题": "Incident", "订阅条件": "严重", "上报通知数据格式": "DataPart"},
            "要求：",
        ),
        (
            "Notification-T/network-layer/service-recovery/v1/zh-CN/template.md",
            {"订阅条件": "子网：xx", "上报通知数据格式": "数据格式内容。"},
            "要求：",
        ),
    ],
)
def test_collapse_sections_removes_scaffolding_from_packaged_templates(
    template_uri: str, values: dict[str, str], scaffolding_marker: str
) -> None:
    """Real templates render without their requirement scaffolding (Java contract test)."""
    rendered = collapse_sections(_load_template(template_uri), values)

    assert scaffolding_marker not in rendered
    assert all(value in rendered for value in values.values())


@pytest.mark.parametrize(
    ("template_uri", "values", "expected_value"),
    [
        (
            "Negotiation-T/information-negotiation/propose/v1/zh-CN/template.md",
            {"所需信息项": "1. 信息项"},
            "信息项",
        ),
        (
            "Negotiation-T/information-negotiation/propose/v1/en-US/template.md",
            {"required_information_items": "1. information item"},
            "information item",
        ),
    ],
)
def test_drop_sections_drops_blank_slot_sections_from_packaged_templates(
    template_uri: str, values: dict[str, str], expected_value: str
) -> None:
    """Real negotiation templates drop their unfilled slot sections (Java contract test)."""
    rendered = drop_sections(_load_template(template_uri), values)

    assert "要求：" not in rendered
    assert "Requirement:" not in rendered
    assert expected_value in rendered


def test_drop_sections_keeps_static_sections_of_the_abort_template() -> None:
    """The abort template's static result section survives; its blank reason drops."""
    template_text = _load_template("Negotiation-T/common/abort/v1/zh-CN/template.md")

    assert drop_sections(template_text, {}) == "## 协商结果\nAbort"
    assert drop_sections(template_text, {"协商终止原因": "达到协商轮次上限。"}) == (
        "## 协商结果\nAbort\n\n## 协商终止原因\n达到协商轮次上限。"
    )
