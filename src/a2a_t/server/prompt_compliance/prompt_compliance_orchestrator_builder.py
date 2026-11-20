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

from typing import Any

from a2a_t.common.prompt_resources import PromptResourceAccess
from a2a_t.common.prompt_runtime import PromptRuntimeComponents, PromptRuntimeComponentsBuilder
from a2a_t.config.models import A2ATConfig
from a2a_t.prompt.analysis import ScenarioRecognizer, ScenarioResolutionOrchestrator, SlotExtractor

from .llm_semantic_slot_validator import LLMSemanticSlotValidator
from .prompt_compliance_orchestrator import PromptComplianceOrchestrator


class PromptComplianceOrchestratorBuilder:
    """Assemble the server-side prompt compliance runtime."""

    def __init__(
        self,
        *,
        runtime_components_builder: PromptRuntimeComponentsBuilder | None = None,
        scenario_recognizer_cls: type = ScenarioRecognizer,
        scenario_resolver_cls: type = ScenarioResolutionOrchestrator,
        slot_extractor_cls: type = SlotExtractor,
        semantic_validator_cls: type = LLMSemanticSlotValidator,
        orchestrator_cls: type = PromptComplianceOrchestrator,
    ) -> None:
        self._runtime_components_builder = runtime_components_builder or PromptRuntimeComponentsBuilder()
        self._scenario_recognizer_cls = scenario_recognizer_cls
        self._scenario_resolver_cls = scenario_resolver_cls
        self._slot_extractor_cls = slot_extractor_cls
        self._semantic_validator_cls = semantic_validator_cls
        self._orchestrator_cls = orchestrator_cls

    def build(
        self,
        *,
        config: A2ATConfig,
        llm_client: Any,
        resource_access: PromptResourceAccess | None = None,
        runtime_components: PromptRuntimeComponents | None = None,
        logger: Any | None = None,
    ) -> PromptComplianceOrchestrator:
        """Build a fully wired prompt compliance orchestrator.

        Args:
            config: resolved SDK configuration carrying the prompt runtime settings.
            llm_client: LLM client used by scenario recognition, slot extraction and semantic
                validation.
            resource_access: optional access object overriding the one built from the config (the
                injection seam mirroring ``llm_client``); ignored when ``runtime_components`` is
                given.
            runtime_components: optional prebuilt runtime components reused as-is.
            logger: optional logger receiving the pipeline logs.

        Returns:
            the assembled prompt compliance orchestrator.
        """
        components = runtime_components or self._runtime_components_builder.build(
            config=config,
            resource_access=resource_access,
        )
        scenario_recognizer = self._scenario_recognizer_cls(llm_client=llm_client)
        scenario_resolver = self._scenario_resolver_cls(
            config=config.prompt,
            resource_access=components.resource_access,
            scenario_recognizer=scenario_recognizer,
        )
        extractor = self._slot_extractor_cls(llm_client=llm_client)
        semantic_validator = self._semantic_validator_cls(
            llm_client=llm_client,
            resource_access=components.resource_access,
        )
        return self._orchestrator_cls(  # type: ignore[no-any-return]
            scenario_resolver=scenario_resolver,
            resource_access=components.resource_access,
            extractor=extractor,
            validator=components.json_schema_slot_validator,
            semantic_validator=semantic_validator,
            input_limit=config.input_limits,
            language=config.prompt.language,
            logger=logger,
        )
