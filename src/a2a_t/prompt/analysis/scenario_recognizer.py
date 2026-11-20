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

import json
from typing import Any

from a2a_t.common.prompt_resources.models import ScenarioDefinition

from .errors import ScenarioRecognitionError
from .json_schema_builder import AnalysisJsonSchemaBuilder
from .message_builder import AnalysisMessageBuilder
from .models import ScenarioRecognitionResult


class ScenarioRecognizer:
    """Recognize the best matching scenario from normalized user input."""

    def __init__(
        self,
        *,
        llm_client: Any,
        message_builder: AnalysisMessageBuilder | None = None,
        json_schema_builder: AnalysisJsonSchemaBuilder | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._message_builder = message_builder or AnalysisMessageBuilder()
        self._json_schema_builder = json_schema_builder or AnalysisJsonSchemaBuilder()
        self.last_raw_response_content: str | None = None

    def recognize(
        self,
        *,
        normalized_input: str,
        scenarios: list[ScenarioDefinition],
        language: str,
        system_prompt: str,
        user_prompt: str,
    ) -> ScenarioRecognitionResult:
        """Run scenario recognition and normalize the structured LLM response."""
        messages = self._message_builder.build_scenario_recognition_messages(
            normalized_input=normalized_input,
            scenarios=scenarios,
            language=language,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        response = self._llm_client.structured(
            messages=messages,
            json_schema=self._json_schema_builder.build_scenario_recognition_schema(),
        )
        self.last_raw_response_content = response.content
        return self._parse_response(response.content)

    def _parse_response(self, content: str) -> ScenarioRecognitionResult:
        """Validate the response contract before the orchestrator trusts the result."""
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as error:
            raise ScenarioRecognitionError(
                "Scenario recognition returned invalid JSON.",
                raw_content=content,
            ) from error

        if not isinstance(payload, dict):
            raise ScenarioRecognitionError("Scenario recognition must return a JSON object.", raw_content=content)

        matched = payload.get("matched")
        scenario_code = payload.get("scenario_code")
        error_message = payload.get("error_message")

        if not isinstance(matched, bool):
            raise ScenarioRecognitionError("Scenario recognition field 'matched' must be boolean.", raw_content=content)

        if matched:
            # A matched result must be self-contained so later stages never infer missing fields.
            if not isinstance(scenario_code, str) or not scenario_code.strip():
                raise ScenarioRecognitionError(
                    "Scenario recognition requires non-empty 'scenario_code' when matched is true.",
                    raw_content=content,
                )
            if error_message is not None:
                raise ScenarioRecognitionError(
                    "Scenario recognition requires null 'error_message' when matched is true.",
                    raw_content=content,
                )
        else:
            # Unmatched responses must carry an explicit reason because the orchestrator surfaces it directly.
            if scenario_code is not None:
                raise ScenarioRecognitionError(
                    "Scenario recognition requires null 'scenario_code' when matched is false.",
                    raw_content=content,
                )
            if not isinstance(error_message, str) or not error_message.strip():
                raise ScenarioRecognitionError(
                    "Scenario recognition requires non-empty 'error_message' when matched is false.",
                    raw_content=content,
                )

        return ScenarioRecognitionResult(
            matched=matched,
            scenario_code=scenario_code,
            error_message=error_message,
        )
