from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.llm.models import LLMResponse


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


class FakePromptResourceLoader:
    def __init__(self, *, system_prompt: str, user_prompt: str) -> None:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.calls: list[dict[str, str]] = []

    def load(self, *, analysis_action: str, language: str):
        self.calls.append({"analysis_action": analysis_action, "language": language})
        return type(
            "PromptMessages",
            (),
            {"system_prompt": self.system_prompt, "user_prompt": self.user_prompt},
        )()


class LLMSemanticSlotValidatorTest(unittest.TestCase):
    def test_validate_builds_prompt_and_returns_passed_result(self) -> None:
        from a2a_t.server.prompt_compliance.llm_semantic_slot_validator import LLMSemanticSlotValidator

        llm = FakeLLMClient(
            LLMResponse(
                content='{"passed": true, "errors": []}',
                model="deepseek-chat",
                usage={},
                metadata={},
            )
        )
        prompt_loader = FakePromptResourceLoader(
            system_prompt="SYSTEM_PROMPT_FROM_FILE",
            user_prompt="USER_PROMPT_FROM_FILE",
        )
        validator = LLMSemanticSlotValidator(
            llm_client=llm,
            prompt_resource_loader=prompt_loader,
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
            prompt_loader.calls,
            [{"analysis_action": "semantic_validation", "language": "zh-CN"}],
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

    def test_validate_returns_failed_result_when_llm_returns_invalid_json(self) -> None:
        from a2a_t.server.prompt_compliance.llm_semantic_slot_validator import LLMSemanticSlotValidator

        llm = FakeLLMClient(
            LLMResponse(
                content="not-json",
                model="deepseek-chat",
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
