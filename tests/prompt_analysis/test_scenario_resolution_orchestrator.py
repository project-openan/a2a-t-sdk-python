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

"""Tests for the scenario resolution orchestrator on the D31 resource access layer."""

from __future__ import annotations

from typing import Any

import pytest

from a2a_t.common.prompt_resources.models import PromptMessages, ScenarioDefinition
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATError
from a2a_t.prompt.analysis.models import ScenarioRecognitionResult

SCENARIOS = [
    ScenarioDefinition(
        scenario_code="ran-energy-saving",
        scenario_name="Energy Saving",
        description="Energy saving analysis tasks.",
        example="Analyze site power usage and suggest optimization.",
    )
]

SCENARIO_PROMPTS = PromptMessages(system_prompt="Identify scenario.", user_prompt="Choose scenario.")


class FakeResourceAccess:
    """Fake access object serving canned scenarios and scenario-recognition prompts."""

    def __init__(
        self,
        *,
        scenarios: list[ScenarioDefinition] | Exception = SCENARIOS,
        prompts: PromptMessages | Exception = SCENARIO_PROMPTS,
    ) -> None:
        self._scenarios = scenarios
        self._prompts = prompts
        self.scenario_calls: list[str] = []
        self.prompt_calls: list[tuple[str, str, str]] = []

    def load_scenarios(self, language: str) -> list[ScenarioDefinition]:
        self.scenario_calls.append(language)
        if isinstance(self._scenarios, Exception):
            raise self._scenarios
        return self._scenarios

    def load_prompt(self, category: str, language: str, file_name: str) -> str:
        self.prompt_calls.append((category, language, file_name))
        if isinstance(self._prompts, Exception):
            raise self._prompts
        if file_name == "system.md":
            return self._prompts.system_prompt
        return self._prompts.user_prompt


class FakeScenarioRecognizer:
    def __init__(self, result: ScenarioRecognitionResult | Exception) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    def recognize(self, **kwargs: object) -> ScenarioRecognitionResult:
        self.calls.append(dict(kwargs))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _build_orchestrator(
    *,
    access: FakeResourceAccess,
    recognition_result: ScenarioRecognitionResult | Exception,
    language: str = "zh-CN",
) -> Any:
    from a2a_t.prompt.analysis.scenario_resolution_orchestrator import ScenarioResolutionOrchestrator

    return ScenarioResolutionOrchestrator(
        config=PromptRuntimeConfig(language=language),
        resource_access=access,
        scenario_recognizer=FakeScenarioRecognizer(recognition_result),
    )


def test_resolve_returns_reference_and_scenario_when_recognition_succeeds() -> None:
    access = FakeResourceAccess()
    orchestrator = _build_orchestrator(
        access=access,
        recognition_result=ScenarioRecognitionResult(
            matched=True,
            scenario_code="ran-energy-saving",
            error_message=None,
        ),
    )

    result = orchestrator.resolve("Please analyze site A energy usage.")

    assert result.success is True
    assert result.failure is None
    assert result.reference is not None
    assert result.reference.scenario_code == "ran-energy-saving"
    assert result.reference.language == "zh-CN"
    assert result.scenario is not None
    assert result.scenario.scenario_code == "ran-energy-saving"
    assert access.scenario_calls == ["zh-CN"]
    assert access.prompt_calls == [
        ("scenario_recognition", "zh-CN", "system.md"),
        ("scenario_recognition", "zh-CN", "user.md"),
    ]


def test_resolve_requires_a_prompt_runtime_config() -> None:
    from a2a_t.prompt.analysis.scenario_resolution_orchestrator import ScenarioResolutionOrchestrator

    with pytest.raises(TypeError):
        ScenarioResolutionOrchestrator(
            config=object(),  # type: ignore[arg-type]
            resource_access=FakeResourceAccess(),
            scenario_recognizer=FakeScenarioRecognizer(
                ScenarioRecognitionResult(matched=True, scenario_code="ran-energy-saving", error_message=None)
            ),
        )


@pytest.mark.parametrize(
    "recognition_result",
    [
        ScenarioRecognitionResult(matched=False, scenario_code=None, error_message="No matching scenario."),
        ScenarioRecognitionResult(matched=True, scenario_code=None, error_message=None),
        ScenarioRecognitionResult(matched=True, scenario_code="", error_message=None),
    ],
    ids=["unmatched", "no-code", "blank-code"],
)
def test_resolve_returns_prompt_parse_failure_when_recognition_reports_unmatched(
    recognition_result: ScenarioRecognitionResult,
) -> None:
    orchestrator = _build_orchestrator(
        access=FakeResourceAccess(),
        recognition_result=recognition_result,
        language="en-US",
    )

    result = orchestrator.resolve("Please analyze site A energy usage.")

    assert result.success is False
    assert result.reference is None
    assert result.scenario is None
    assert result.failure is not None
    assert result.failure.stage == "prompt_parse"
    assert result.failure.code == ErrorCatalog.SCENARIO_NOT_MATCHED.value
    assert result.failure.message == (
        f"The input does not match any known scenario: {recognition_result.error_message or 'Scenario recognition failed.'}"
    )


def test_resolve_returns_prompt_parse_failure_when_scenario_code_is_not_supported() -> None:
    orchestrator = _build_orchestrator(
        access=FakeResourceAccess(),
        recognition_result=ScenarioRecognitionResult(
            matched=True,
            scenario_code="unknown_scenario",
            error_message=None,
        ),
        language="en-US",
    )

    result = orchestrator.resolve("Please analyze site A energy usage.")

    assert result.success is False
    assert result.failure is not None
    assert result.failure.stage == "prompt_parse"
    assert result.failure.code == ErrorCatalog.SCENARIO_NOT_MATCHED.value
    assert result.failure.message == (
        "The input does not match any known scenario: "
        "Scenario recognition returned unsupported scenario_code: unknown_scenario"
    )


@pytest.mark.parametrize(
    ("access", "expected_resource_path"),
    [
        (
            FakeResourceAccess(
                scenarios=A2ATError("Failed to read resource 'prompt_resources/scenarios/zh-CN/scenarios.json'.")
            ),
            "prompt_resources/scenarios/zh-CN/scenarios.json",
        ),
        (
            FakeResourceAccess(
                prompts=A2ATError(
                    "Failed to read resource 'prompt_resources/prompts/scenario_recognition/zh-CN/system.md'."
                )
            ),
            "prompt_resources/prompts/scenario_recognition/zh-CN/system.md",
        ),
    ],
    ids=["scenario-catalog-missing", "scenario-prompts-missing"],
)
def test_resolve_returns_preparation_failure_when_scenario_resources_cannot_be_loaded(
    access: FakeResourceAccess,
    expected_resource_path: str,
) -> None:
    orchestrator = _build_orchestrator(
        access=access,
        recognition_result=ScenarioRecognitionResult(
            matched=True,
            scenario_code="ran-energy-saving",
            error_message=None,
        ),
    )

    result = orchestrator.resolve("Please analyze site A energy usage.")

    assert result.success is False
    assert result.failure is not None
    assert result.failure.stage == "preparation"
    assert result.failure.code == ErrorCatalog.TEMPLATE_LOAD_FAILED.value
    assert result.failure.message == f"模板资源「{expected_resource_path}」读取失败"


def test_resolve_returns_prompt_parse_failure_when_recognizer_raises_runtime_error() -> None:
    orchestrator = _build_orchestrator(
        access=FakeResourceAccess(),
        recognition_result=RuntimeError("llm transport down"),
        language="en-US",
    )

    result = orchestrator.resolve("Please analyze site A energy usage.")

    assert result.success is False
    assert result.failure is not None
    assert result.failure.stage == "prompt_parse"
    assert result.failure.code == ErrorCatalog.SCENARIO_NOT_MATCHED.value
    assert result.failure.message == "The input does not match any known scenario: llm transport down"
