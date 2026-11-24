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
from typing import Any

from a2a_t.common.prompt_resources import PromptResourceAccess
from a2a_t.common.prompt_resources.json_source import scenario_catalog_key
from a2a_t.common.prompt_resources.models import PromptMessages
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATError
from a2a_t.core.errors.messages import render as render_error_message
from a2a_t.core.prompt_resource_key import PromptResourceKey
from a2a_t.prompt.common.models import PromptReference

from .errors import PromptAnalysisError
from .models import ScenarioResolutionFailure, ScenarioResolutionResult

PREPARATION_STAGE = "preparation"
PROMPT_PARSE_STAGE = "prompt_parse"

#: Default reason rendered into ``scenario.not_matched`` when the recognizer reports none.
DEFAULT_SCENARIO_REASON = "Scenario recognition failed."

#: Analysis action whose system/user prompts drive scenario recognition.
_SCENARIO_RECOGNITION_ACTION = "scenario_recognition"


class ScenarioResolutionOrchestrator:
    """Resolve a prompt reference from scenario recognition.

    Every resource — the scenario catalog and the scenario-recognition instruction prompts — is
    loaded through the shared resource access layer (D31): the catalog follows the configured
    source routing, the instruction prompts are always the packaged SDK contract.
    """

    def __init__(
        self,
        *,
        config: PromptRuntimeConfig,
        resource_access: PromptResourceAccess,
        scenario_recognizer: Any,
    ) -> None:
        if not isinstance(config, PromptRuntimeConfig):
            raise TypeError("config must be a PromptRuntimeConfig instance.")
        self._config = config
        self._resource_access = resource_access
        self._scenario_recognizer = scenario_recognizer

    def resolve(self, normalized_input: str) -> ScenarioResolutionResult:
        """Return a resolved prompt reference or a standardized failure."""
        language = self._config.language
        try:
            scenarios = self._resource_access.load_scenarios(language)
        except A2ATError:
            return self._resource_failure(scenario_catalog_key(language).relative_path())
        try:
            scenario_prompts = self._load_scenario_prompts()
        except A2ATError:
            return self._resource_failure(
                PromptResourceKey.prompt(_SCENARIO_RECOGNITION_ACTION, language, "system.md").relative_path()
            )

        try:
            recognition_result = self._scenario_recognizer.recognize(
                normalized_input=normalized_input,
                scenarios=scenarios,
                language=self._config.language,
                system_prompt=scenario_prompts.system_prompt,
                user_prompt=scenario_prompts.user_prompt,
            )
        except PromptAnalysisError as error:
            return self._scenario_failure(str(error))
        except Exception as error:
            return self._scenario_failure(str(error))

        if not recognition_result.matched or not recognition_result.scenario_code:
            return self._scenario_failure(recognition_result.error_message or DEFAULT_SCENARIO_REASON)

        for scenario in scenarios:
            if scenario.scenario_code == recognition_result.scenario_code:
                return ScenarioResolutionResult(
                    success=True,
                    reference=PromptReference(
                        scenario_code=scenario.scenario_code,
                        language=self._config.language,
                    ),
                    scenario=scenario,
                )

        return self._scenario_failure(
            f"Scenario recognition returned unsupported scenario_code: {recognition_result.scenario_code}"
        )

    def _load_scenario_prompts(self) -> PromptMessages:
        """Load the packaged scenario-recognition instruction prompts of the configured language."""
        language = self._config.language
        system_prompt = self._resource_access.load_prompt(_SCENARIO_RECOGNITION_ACTION, language, "system.md")
        user_prompt = self._resource_access.load_prompt(_SCENARIO_RECOGNITION_ACTION, language, "user.md")
        return PromptMessages(system_prompt=system_prompt, user_prompt=user_prompt)

    def _resource_failure(self, resource_path: str) -> ScenarioResolutionResult:
        """Build the ``template.load_failed`` failure for one resource-loading error.

        Args:
            resource_path: path of the resource family that failed to load, used as the failure fact.
        """
        return self._failure(
            stage=PREPARATION_STAGE,
            entry=ErrorCatalog.TEMPLATE_LOAD_FAILED,
            facts={"resource_path": resource_path},
        )

    def _scenario_failure(self, reason: str) -> ScenarioResolutionResult:
        """Build the ``scenario.not_matched`` failure carrying the recognition reason."""
        return self._failure(
            stage=PROMPT_PARSE_STAGE,
            entry=ErrorCatalog.SCENARIO_NOT_MATCHED,
            facts={"reason": reason},
        )

    def _failure(self, *, stage: str, entry: ErrorCatalog, facts: Mapping[str, object]) -> ScenarioResolutionResult:
        return ScenarioResolutionResult(
            success=False,
            failure=ScenarioResolutionFailure(
                code=entry.value,
                message=render_error_message(entry, facts, self._config.language),
                stage=stage,
            ),
        )
