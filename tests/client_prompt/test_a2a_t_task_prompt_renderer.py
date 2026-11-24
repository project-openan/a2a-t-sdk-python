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

"""Tests for the orchestrator-facing ``TaskPromptRenderer`` adapter (collapse policy).

The adapter delegates to :func:`a2a_t.prompt.task_rendering.collapse_sections`; the heavy
Java-parity case matrix lives in ``tests/prompt_rendering/test_sectioned_renderer.py``.
This file pins the orchestrator wiring: the extra keyword arguments the pipeline passes
are accepted and ignored, and the render failure surfaces as the coded
``template.render_failed`` error.
"""

from __future__ import annotations

import pytest

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.prompt.task_rendering import TaskPromptRenderer
from a2a_t.prompt.task_rendering.errors import TaskPromptRenderError

_RENDER_KWARGS = {
    "scenario_code": "ran-energy-saving",
    "language": "en-US",
    "description": "Used for energy saving analysis.",
}


def test_shared_task_prompt_renderer_builds_plain_prompt_body() -> None:
    renderer = TaskPromptRenderer()

    prompt_text = renderer.render(
        template_text="Site: {site}\nNotes: {additional_notes}",
        slots={"site": "Site A", "additional_notes": None},
        **_RENDER_KWARGS,
    )

    assert prompt_text == "Site: Site A\nNotes: "
    assert "scenario_code:" not in prompt_text
    assert "language:" not in prompt_text
    assert "---" not in prompt_text


def test_render_supports_double_braced_placeholders() -> None:
    renderer = TaskPromptRenderer()

    prompt_text = renderer.render(
        template_text="Topic: {{topic}}\nCondition: {{condition}}",
        slots={"topic": "Incident", "condition": "critical alert"},
        scenario_code="subscribe-incident",
        language="zh-CN",
        description="Used for incident subscription prompts.",
    )

    assert prompt_text == "Topic: Incident\nCondition: critical alert"


def test_render_keeps_only_slot_value_for_section_with_slot_header_body() -> None:
    renderer = TaskPromptRenderer()

    prompt_text = renderer.render(
        template_text=(
            "## Task Type\n"
            "Diagnosis\n\n"
            "## Task Target\n"
            "{{task_target}}（Required）\n\n"
            "Requirement: explain the target.\n"
            "Example: complete the diagnosis.\n\n"
            "## Expected Output\n"
            "{{expected_output}}（Optional）\n"
        ),
        slots={
            "task_target": "Complete the diagnosis and provide remediation advice.",
            "expected_output": "Return a structured diagnosis result.",
        },
        scenario_code="private-line-complaint",
        language="en-US",
        description="Used for private line complaint prompts.",
    )

    assert prompt_text == (
        "## Task Type\n"
        "Diagnosis\n\n"
        "## Task Target\n"
        "Complete the diagnosis and provide remediation advice.\n\n"
        "## Expected Output\n"
        "Return a structured diagnosis result.\n"
    )


def test_render_preserves_regular_inline_placeholder_content() -> None:
    renderer = TaskPromptRenderer()

    prompt_text = renderer.render(
        template_text=(
            "## Subscription\n"
            "Please subscribe to {{topic}} incidents.\n\n"
            "## Condition\n"
            "{{condition}}（Optional）\n"
            "Requirement: describe the filter.\n"
        ),
        slots={"topic": "network", "condition": "critical only"},
        scenario_code="subscribe-incident",
        language="en-US",
        description="Used for incident subscription prompts.",
    )

    assert prompt_text == ("## Subscription\nPlease subscribe to network incidents.\n\n## Condition\ncritical only\n")


def test_render_keeps_unknown_single_braced_text_verbatim() -> None:
    """Single-brace unknown text is example prose, not an unknown-slot error (Java parity)."""
    renderer = TaskPromptRenderer()

    prompt_text = renderer.render(
        template_text="Site: {site}\nTime Range: {time_range}",
        slots={"site": "Site A"},
        **_RENDER_KWARGS,
    )

    assert prompt_text == "Site: Site A\nTime Range: {time_range}"


def test_render_raises_when_template_references_unknown_slot() -> None:
    renderer = TaskPromptRenderer()

    with pytest.raises(TaskPromptRenderError) as excinfo:
        renderer.render(
            template_text="Site: {{site}}\nTime Range: {{time_range}}",
            slots={"site": "Site A"},
            **_RENDER_KWARGS,
        )

    assert excinfo.value.code is ErrorCatalog.TEMPLATE_RENDER_FAILED
    assert excinfo.value.facts["reason"] == "Unknown slot referenced by template: time_range"


def test_render_raises_when_template_is_invalid() -> None:
    renderer = TaskPromptRenderer()

    with pytest.raises(TaskPromptRenderError) as excinfo:
        renderer.render(
            template_text="Site: {site",
            slots={"site": "Site A"},
            **_RENDER_KWARGS,
        )

    assert excinfo.value.code is ErrorCatalog.TEMPLATE_RENDER_FAILED
    assert excinfo.value.facts["reason"] == "Template text has unbalanced braces."


def test_render_delegates_to_the_collapse_policy_function() -> None:
    """The adapter is a thin wrapper: same input, same output as ``collapse_sections``."""
    from a2a_t.prompt.task_rendering import collapse_sections

    template_text = "## Task Target\n{{task_target}}（必填）\n要求：\n请提供任务目标。\n"
    slots = {"task_target": "节能最大化"}

    assert TaskPromptRenderer().render(
        template_text=template_text,
        slots=slots,
        **_RENDER_KWARGS,
    ) == collapse_sections(template_text, slots)
