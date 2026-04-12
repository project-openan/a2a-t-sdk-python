from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.llm.base import LLMResponse
from a2a_t.llm.errors import LLMRuntimeError
from a2a_t.llm.factory import LLMAdapterFactory
from a2a_t.prompt.models import CacheStatus, Prompt, PromptSource
from a2a_t.config.models import LLMConfig
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

        with self.assertRaises(SlotExtractionError) as context:
            extractor.extract(
                original_prompt=self.original_prompt,
                processed_prompt_text="processed prompt body",
            )

        self.assertEqual(context.exception.context["raw_content"], '{"notes": ["missing slots"]}')


class ProviderAdapterContractTest(unittest.TestCase):
    def test_factory_registers_provider_specific_llm_adapters(self) -> None:
        available_types = LLMAdapterFactory.available_types()

        self.assertIn("openai", available_types)
        self.assertIn("anthropic", available_types)
        self.assertIn("google", available_types)
        self.assertIn("deepseek", available_types)

    @patch("a2a_t.llm.adapters.openai_adapter.OpenAI")
    def test_openai_adapter_builds_structured_payload(self, openai_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4.1",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"slots": {"device_type": "router"}, "notes": [], "confidence": 0.8}'
                    )
                )
            ],
            usage=SimpleNamespace(prompt_tokens=6, completion_tokens=2),
        )
        openai_cls.return_value = sdk_client

        adapter = LLMAdapterFactory.create("openai", {"model": "gpt-4.1", "api_key": "sk-test"})

        response = adapter.structured(
            messages=[{"role": "user", "content": "extract"}],
            json_schema={"type": "object"},
        )

        kwargs = sdk_client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["response_format"]["type"], "json_schema")
        self.assertEqual(response.content, '{"slots": {"device_type": "router"}, "notes": [], "confidence": 0.8}')

    @patch("a2a_t.llm.adapters.anthropic_adapter.Anthropic")
    def test_anthropic_adapter_builds_tool_use_payload(self, anthropic_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.messages.create.return_value = SimpleNamespace(
            model="claude-sonnet-4-5",
            usage=SimpleNamespace(input_tokens=7, output_tokens=2),
            content=[
                SimpleNamespace(type="tool_use", input={"slots": {"device_type": "router"}, "notes": [], "confidence": 0.7})
            ],
        )
        anthropic_cls.return_value = sdk_client

        adapter = LLMAdapterFactory.create("anthropic", {"model": "claude-sonnet-4-5", "api_key": "anthropic-key"})

        response = adapter.structured(
            messages=[{"role": "user", "content": "extract"}],
            json_schema={"type": "object"},
        )

        kwargs = sdk_client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["tools"][0]["input_schema"], {"type": "object"})
        self.assertEqual(response.content, '{"slots": {"device_type": "router"}, "notes": [], "confidence": 0.7}')

    def test_anthropic_adapter_rejects_complete_and_chat_in_phase1(self) -> None:
        adapter = LLMAdapterFactory.create(
            "anthropic",
            {
                "model": "claude-sonnet-4-5",
                "api_key": "anthropic-key",
            },
        )

        with self.assertRaises(LLMRuntimeError):
            adapter.complete("hello")

        with self.assertRaises(LLMRuntimeError):
            adapter.chat("hello")

    @patch("a2a_t.llm.adapters.google_adapter.types.GenerateContentConfig")
    @patch("a2a_t.llm.adapters.google_adapter.genai.Client")
    def test_google_adapter_builds_response_schema_payload(self, client_cls: Mock, config_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.models.generate_content.return_value = SimpleNamespace(
            text='{"slots": {"device_type": "router"}, "notes": [], "confidence": 0.6}',
            model_version="gemini-2.5-pro",
            usage_metadata=SimpleNamespace(prompt_token_count=5, candidates_token_count=2),
        )
        client_cls.return_value = sdk_client
        config_cls.side_effect = lambda **kwargs: kwargs

        adapter = LLMAdapterFactory.create("google", {"model": "gemini-2.5-pro", "api_key": "google-key"})

        response = adapter.structured(
            messages=[{"role": "user", "content": "extract"}],
            json_schema={"type": "object"},
        )

        kwargs = sdk_client.models.generate_content.call_args.kwargs
        self.assertEqual(kwargs["config"]["response_json_schema"], {"type": "object"})
        self.assertEqual(response.content, '{"slots": {"device_type": "router"}, "notes": [], "confidence": 0.6}')


class LLMConfigModelTest(unittest.TestCase):
    def test_llm_config_includes_chat_defaults(self) -> None:
        config = LLMConfig()

        self.assertEqual(config.history_window, 10)
        self.assertEqual(config.session_store_type, "memory")


if __name__ == "__main__":
    unittest.main()
