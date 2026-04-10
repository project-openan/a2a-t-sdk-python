from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.llm.base import LLMResponse
from a2a_t.llm.factory import LLMAdapterFactory
from a2a_t.prompt.models import CacheStatus, Prompt, PromptSource
from a2a_t.server.prompt_compliance.errors import SlotExtractionError
from a2a_t.server.prompt_compliance.extractor import PromptSlotExtractor
from a2a_t.server.prompt_compliance.models import SlotExtractionResult


class FakeStructuredAdapter:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[dict[str, Any]] = []

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, Any], **kwargs: Any) -> LLMResponse:
        self.calls.append({"messages": messages, "json_schema": json_schema, "kwargs": kwargs})
        return LLMResponse(
            content=self.response_text,
            model="fake",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            metadata={},
        )


class PromptSlotExtractorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_prompt = Prompt(
            name="diagnosis",
            language="zh-CN",
            version="1.0.0",
            title="Diagnosis Prompt",
            description="Extract slots for diagnosis tasks.",
            format="markdown",
            body="original prompt body",
            raw_content="raw original prompt",
            source=PromptSource(source_type="local_file", locator="./prompts/diagnosis.md"),
            cache_status=CacheStatus.MISS,
        )

    def test_extract_returns_structured_slot_result(self) -> None:
        adapter = FakeStructuredAdapter(
            '{"slots": {"device_type": "router", "operation": "restart"}, "notes": ["from original prompt"], "confidence": 0.9}'
        )
        extractor = PromptSlotExtractor(adapter=adapter)

        result = extractor.extract(
            original_prompt=self.original_prompt,
            processed_prompt_text="processed prompt body",
        )

        self.assertEqual(
            result,
            SlotExtractionResult(
                slots={"device_type": "router", "operation": "restart"},
                notes=["from original prompt"],
                confidence=0.9,
                raw_response=None,
            ),
        )
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(adapter.calls[0]["json_schema"]["required"], ["slots", "notes"])

    def test_extract_rejects_invalid_structured_output(self) -> None:
        adapter = FakeStructuredAdapter('{"notes": ["missing slots"]}')
        extractor = PromptSlotExtractor(adapter=adapter)

        with self.assertRaises(SlotExtractionError):
            extractor.extract(
                original_prompt=self.original_prompt,
                processed_prompt_text="processed prompt body",
            )


class ProviderAdapterContractTest(unittest.TestCase):
    def test_factory_registers_provider_specific_llm_adapters(self) -> None:
        available_types = LLMAdapterFactory.available_types()

        self.assertIn("openai", available_types)
        self.assertIn("anthropic", available_types)
        self.assertIn("google", available_types)

    def test_openai_adapter_builds_structured_payload(self) -> None:
        recorded: dict[str, Any] = {}

        def transport(payload: dict[str, Any]) -> dict[str, Any]:
            recorded["payload"] = payload
            return {
                "output_text": '{"slots": {"device_type": "router"}, "notes": [], "confidence": 0.8}',
                "model": "gpt-4.1",
            }

        adapter = LLMAdapterFactory.create(
            "openai",
            {
                "model": "gpt-4.1",
                "transport": transport,
            },
        )

        response = adapter.structured(
            messages=[{"role": "user", "content": "extract"}],
            json_schema={"type": "object"},
        )

        self.assertEqual(recorded["payload"]["model"], "gpt-4.1")
        self.assertEqual(recorded["payload"]["response_format"]["type"], "json_schema")
        self.assertEqual(response.content, '{"slots": {"device_type": "router"}, "notes": [], "confidence": 0.8}')

    def test_anthropic_adapter_builds_tool_use_payload(self) -> None:
        recorded: dict[str, Any] = {}

        def transport(payload: dict[str, Any]) -> dict[str, Any]:
            recorded["payload"] = payload
            return {
                "tool_input": {"slots": {"device_type": "router"}, "notes": [], "confidence": 0.7},
                "model": "claude-sonnet-4-5",
            }

        adapter = LLMAdapterFactory.create(
            "anthropic",
            {
                "model": "claude-sonnet-4-5",
                "transport": transport,
            },
        )

        response = adapter.structured(
            messages=[{"role": "user", "content": "extract"}],
            json_schema={"type": "object"},
        )

        self.assertEqual(recorded["payload"]["model"], "claude-sonnet-4-5")
        self.assertEqual(recorded["payload"]["tools"][0]["input_schema"], {"type": "object"})
        self.assertEqual(response.content, '{"slots": {"device_type": "router"}, "notes": [], "confidence": 0.7}')

    def test_google_adapter_builds_response_schema_payload(self) -> None:
        recorded: dict[str, Any] = {}

        def transport(payload: dict[str, Any]) -> dict[str, Any]:
            recorded["payload"] = payload
            return {
                "text": '{"slots": {"device_type": "router"}, "notes": [], "confidence": 0.6}',
                "model": "gemini-2.5-pro",
            }

        adapter = LLMAdapterFactory.create(
            "google",
            {
                "model": "gemini-2.5-pro",
                "transport": transport,
            },
        )

        response = adapter.structured(
            messages=[{"role": "user", "content": "extract"}],
            json_schema={"type": "object"},
        )

        self.assertEqual(recorded["payload"]["model"], "gemini-2.5-pro")
        self.assertEqual(recorded["payload"]["generation_config"]["response_json_schema"], {"type": "object"})
        self.assertEqual(response.content, '{"slots": {"device_type": "router"}, "notes": [], "confidence": 0.6}')


if __name__ == "__main__":
    unittest.main()
