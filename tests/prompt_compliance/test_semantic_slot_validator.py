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

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.llm.models import LLMResponse
from tests.support import FakePromptResourceAccess


class FakeLLMClient:
    def __init__(self, response: LLMResponse | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, object],
        **kwargs: object,
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "json_schema": json_schema})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class LLMSemanticSlotValidatorTest(unittest.TestCase):
    def test_validate_builds_prompt_and_returns_passed_result(self) -> None:
        from a2a_t.server.prompt_compliance.llm_semantic_slot_validator import LLMSemanticSlotValidator

        llm = FakeLLMClient(
            LLMResponse(
                content='{"passed": true, "errors": []}',
                model="gpt-4o-mini",
                usage={},
                metadata={},
            )
        )
        access = FakePromptResourceAccess(
            system_prompt="SYSTEM_PROMPT_FROM_FILE",
            user_prompt="USER_PROMPT_FROM_FILE",
        )
        validator = LLMSemanticSlotValidator(
            llm_client=llm,
            resource_access=access,
        )

        result = validator.validate(
            language="zh-CN",
            slot_json_schema={"type": "object"},
            extracted_slots={"site": "Site A"},
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.errors, [])
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(
            access.prompt_calls,
            [("semantic_validation", "zh-CN", "system.md"), ("semantic_validation", "zh-CN", "user.md")],
        )
        messages = llm.calls[0]["messages"]
        assert isinstance(messages, list)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "SYSTEM_PROMPT_FROM_FILE")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("USER_PROMPT_FROM_FILE", messages[1]["content"])
        self.assertIn('"slot_json_schema"', messages[1]["content"])
        self.assertIn('"extracted_slots"', messages[1]["content"])
        self.assertNotIn('"scenario_code"', messages[1]["content"])
        self.assertNotIn('"language"', messages[1]["content"])
        self.assertNotIn('"processed_prompt_text"', messages[1]["content"])

    def test_validate_uses_the_packaged_prompts_by_default(self) -> None:
        from a2a_t.common.prompt_resources import PackagedPromptResourceAccess
        from a2a_t.server.prompt_compliance.llm_semantic_slot_validator import LLMSemanticSlotValidator

        llm = FakeLLMClient(
            LLMResponse(
                content='{"passed": true, "errors": []}',
                model="gpt-4o-mini",
                usage={},
                metadata={},
            )
        )
        validator = LLMSemanticSlotValidator(llm_client=llm)

        validator.validate(
            language="zh-CN",
            slot_json_schema={"type": "object"},
            extracted_slots={"site": "Site A"},
        )

        messages = llm.calls[0]["messages"]
        assert isinstance(messages, list)
        packaged = PackagedPromptResourceAccess()
        self.assertEqual(
            messages[0]["content"],
            packaged.load_prompt("semantic_validation", "zh-CN", "system.md"),
        )
        self.assertIn(packaged.load_prompt("semantic_validation", "zh-CN", "user.md"), messages[1]["content"])

    def test_validate_returns_failed_result_when_prompts_cannot_be_loaded(self) -> None:
        from a2a_t.core.errors.exceptions import A2ATError
        from a2a_t.server.prompt_compliance.llm_semantic_slot_validator import LLMSemanticSlotValidator

        llm = FakeLLMClient(
            LLMResponse(
                content='{"passed": true, "errors": []}',
                model="gpt-4o-mini",
                usage={},
                metadata={},
            )
        )
        validator = LLMSemanticSlotValidator(
            llm_client=llm,
            resource_access=FakePromptResourceAccess(
                system_prompt=A2ATError("Failed to read resource 'prompts/semantic_validation/zh-CN/system.md'.")
            ),
        )

        result = validator.validate(
            language="zh-CN",
            slot_json_schema={"type": "object"},
            extracted_slots={"site": "Site A"},
        )

        self.assertFalse(result.passed)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].code, "semantic_validation_runtime_error")
        self.assertEqual(len(llm.calls), 0)

    def test_validate_returns_failed_result_when_llm_returns_invalid_json(self) -> None:
        from a2a_t.server.prompt_compliance.llm_semantic_slot_validator import LLMSemanticSlotValidator

        llm = FakeLLMClient(
            LLMResponse(
                content="not-json",
                model="gpt-4o-mini",
                usage={},
                metadata={},
            )
        )
        validator = LLMSemanticSlotValidator(llm_client=llm)

        result = validator.validate(
            language="zh-CN",
            slot_json_schema={"type": "object"},
            extracted_slots={"site": "S"},
        )

        self.assertFalse(result.passed)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].code, "semantic_validation_parse_error")

    def test_validate_returns_failed_result_when_llm_call_raises(self) -> None:
        from a2a_t.server.prompt_compliance.llm_semantic_slot_validator import LLMSemanticSlotValidator

        llm = FakeLLMClient(RuntimeError("llm down"))
        validator = LLMSemanticSlotValidator(llm_client=llm)

        result = validator.validate(
            language="zh-CN",
            slot_json_schema={"type": "object"},
            extracted_slots={"site": "S"},
        )

        self.assertFalse(result.passed)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].code, "semantic_validation_runtime_error")
        self.assertIn("llm down", result.errors[0].message)


if __name__ == "__main__":
    unittest.main()
