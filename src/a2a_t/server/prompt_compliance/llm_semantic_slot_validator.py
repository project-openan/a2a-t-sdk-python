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

from a2a_t.common.prompt_resources import PackagedPromptResourceAccess, PromptResourceAccess

from .models import SemanticValidationError, SemanticValidationResult
from .semantic_validator import SemanticSlotValidator

#: Analysis action whose system/user prompts drive semantic slot validation.
_SEMANTIC_VALIDATION_ACTION = "semantic_validation"


class LLMSemanticSlotValidator(SemanticSlotValidator):
    """Validate extracted slot values semantically with an LLM-backed structured call.

    The semantic-validation instruction prompts are loaded through the shared resource access
    layer; ``prompts/**`` is package-fixed by the D31 routing table, so a local copy under a
    configured local root is ignored and the packaged SDK contract is always used.
    """

    def __init__(
        self,
        *,
        llm_client: Any,
        resource_access: PromptResourceAccess | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._resource_access = resource_access if resource_access is not None else PackagedPromptResourceAccess()

    def validate(
        self,
        *,
        language: str,
        slot_json_schema: dict[str, object],
        extracted_slots: dict[str, str | None],
    ) -> SemanticValidationResult:
        try:
            system_prompt = self._resource_access.load_prompt(_SEMANTIC_VALIDATION_ACTION, language, "system.md")
            user_prompt = self._resource_access.load_prompt(_SEMANTIC_VALIDATION_ACTION, language, "user.md")
        except Exception as error:
            return SemanticValidationResult(
                passed=False,
                errors=[
                    SemanticValidationError(
                        slot_name="_global",
                        code="semantic_validation_runtime_error",
                        message=str(error),
                    )
                ],
            )
        try:
            response = self._llm_client.structured(
                messages=self._build_messages(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    slot_json_schema=slot_json_schema,
                    extracted_slots=extracted_slots,
                ),
                json_schema=self._result_json_schema(),
            )
        except Exception as error:
            return SemanticValidationResult(
                passed=False,
                errors=[
                    SemanticValidationError(
                        slot_name="_global",
                        code="semantic_validation_runtime_error",
                        message=str(error),
                    )
                ],
            )

        return self._parse_response(response.content)

    def _build_messages(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        slot_json_schema: dict[str, object],
        extracted_slots: dict[str, str | None],
    ) -> list[dict[str, str]]:
        payload = {
            "slot_json_schema": slot_json_schema,
            "extracted_slots": extracted_slots,
        }
        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"{user_prompt}\n\n" + json.dumps(payload, ensure_ascii=False, indent=2),
            },
        ]

    def _result_json_schema(self) -> dict[str, object]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["passed", "errors"],
            "properties": {
                "passed": {"type": "boolean"},
                "errors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["slot_name", "code", "message"],
                        "properties": {
                            "slot_name": {"type": "string"},
                            "code": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    },
                },
            },
        }

    def _parse_response(self, content: str) -> SemanticValidationResult:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return SemanticValidationResult(
                passed=False,
                errors=[
                    SemanticValidationError(
                        slot_name="_global",
                        code="semantic_validation_parse_error",
                        message="semantic validation returned invalid JSON",
                    )
                ],
            )
        if not isinstance(payload, dict):
            return SemanticValidationResult(
                passed=False,
                errors=[
                    SemanticValidationError(
                        slot_name="_global",
                        code="semantic_validation_parse_error",
                        message="semantic validation response must be a JSON object",
                    )
                ],
            )
        passed = payload.get("passed")
        errors = payload.get("errors")
        if not isinstance(passed, bool) or not isinstance(errors, list):
            return SemanticValidationResult(
                passed=False,
                errors=[
                    SemanticValidationError(
                        slot_name="_global",
                        code="semantic_validation_parse_error",
                        message="semantic validation response missing required fields",
                    )
                ],
            )
        normalized_errors: list[SemanticValidationError] = []
        for item in errors:
            if not isinstance(item, dict):
                continue
            slot_name = item.get("slot_name")
            code = item.get("code")
            message = item.get("message")
            if isinstance(slot_name, str) and isinstance(code, str) and isinstance(message, str) and message.strip():
                normalized_errors.append(
                    SemanticValidationError(
                        slot_name=slot_name,
                        code=code,
                        message=message,
                    )
                )
        if passed:
            return SemanticValidationResult(passed=True, errors=[])
        if normalized_errors:
            return SemanticValidationResult(passed=False, errors=normalized_errors)
        return SemanticValidationResult(
            passed=False,
            errors=[
                SemanticValidationError(
                    slot_name="_global",
                    code="semantic_validation_parse_error",
                    message="semantic validation failed without detailed errors",
                )
            ],
        )
