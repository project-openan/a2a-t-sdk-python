# ruff: noqa: E402, I001

from __future__ import annotations

import sys
from json import dumps
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.llm.factory import LLMAdapterFactory
from a2a_t.llm.base import ComposedLLMAdapter


class OpenAICompatibleAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_adapters = dict(LLMAdapterFactory._adapters)
        self.original_adapter_imports = dict(LLMAdapterFactory._adapter_imports)
        self.original_composed_adapters = dict(getattr(LLMAdapterFactory, "_composed_adapters", {}))

    def tearDown(self) -> None:
        LLMAdapterFactory._adapters = self.original_adapters
        LLMAdapterFactory._adapter_imports = self.original_adapter_imports
        if hasattr(LLMAdapterFactory, "_composed_adapters"):
            LLMAdapterFactory._composed_adapters = self.original_composed_adapters

    @patch("a2a_t.llm.transports.OpenAI")
    def test_factory_creates_provider_adapter_as_composed_adapter(self, openai_cls: Mock) -> None:
        openai_cls.return_value = Mock()
        LLMAdapterFactory._adapters.pop("deepseek", None)
        if hasattr(LLMAdapterFactory, "_composed_adapters"):
            LLMAdapterFactory._composed_adapters.pop("deepseek", None)

        adapter = LLMAdapterFactory.create("deepseek", {"model": "deepseek-chat", "api_key": "deepseek-key"})
        self.assertEqual(adapter.adapter_type, "deepseek")
        self.assertIsInstance(adapter, ComposedLLMAdapter)
        from a2a_t.llm.adapters import OpenAICompatibleAdapter
        from a2a_t.llm.payload_builders import OpenAICompatiblePayloadBuilder
        from a2a_t.llm.response_parsers import OpenAICompatibleResponseParser
        from a2a_t.llm.transports import OpenAICompatibleTransportAdapter

        self.assertIsInstance(adapter, OpenAICompatibleAdapter)
        self.assertIsInstance(adapter._payload_builder, OpenAICompatiblePayloadBuilder)
        self.assertIsInstance(adapter._response_parser, OpenAICompatibleResponseParser)
        self.assertIsInstance(adapter._transport, OpenAICompatibleTransportAdapter)
        openai_cls.assert_called_once_with(
            api_key="deepseek-key",
            base_url="https://api.deepseek.com",
            timeout=None,
        )

    def test_provider_wrapper_type_is_exposed_as_openai_compatible_adapter(self) -> None:
        from a2a_t.llm.adapters import OpenAICompatibleAdapter

        self.assertTrue(issubclass(OpenAICompatibleAdapter, ComposedLLMAdapter))

    @patch("a2a_t.llm.transports.OpenAI")
    def test_complete_forces_json_mode_and_json_instruction(self, openai_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="deepseek-chat",
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer":"done"}'))],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=1),
        )
        openai_cls.return_value = sdk_client

        adapter = LLMAdapterFactory.create("deepseek", {"model": "deepseek-chat", "api_key": "deepseek-key"})
        response = adapter.complete("say hi", system_prompt="be short")

        self.assertEqual(response.content, '{"answer":"done"}')
        self.assertEqual(response.model, "deepseek-chat")
        sdk_client.chat.completions.create.assert_called_once()
        payload = sdk_client.chat.completions.create.call_args.kwargs
        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertIn("JSON", payload["messages"][0]["content"])
        self.assertEqual(payload["messages"][1], {"role": "system", "content": "be short"})
        self.assertEqual(payload["messages"][2], {"role": "user", "content": "say hi"})

    @patch("a2a_t.llm.transports.OpenAI")
    def test_chat_forces_json_mode_for_every_turn(self, openai_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.side_effect = [
            SimpleNamespace(
                model="deepseek-chat",
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"reply":"first"}'))],
                usage=SimpleNamespace(prompt_tokens=6, completion_tokens=2),
            ),
            SimpleNamespace(
                model="deepseek-chat",
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"reply":"second"}'))],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
            ),
        ]
        openai_cls.return_value = sdk_client

        adapter = LLMAdapterFactory.create(
            "deepseek",
            {
                "model": "deepseek-chat",
                "api_key": "deepseek-key",
                "history_window": 2,
            },
        )
        first = adapter.chat("hello", system_prompt="be concise")
        second = adapter.chat("again", session_id=first.session_id)

        self.assertEqual(second.session_id, first.session_id)
        second_call = sdk_client.chat.completions.create.call_args_list[1].kwargs
        self.assertEqual(second_call["response_format"], {"type": "json_object"})
        self.assertEqual(second_call["messages"][0]["role"], "system")
        self.assertIn("JSON", second_call["messages"][0]["content"])
        self.assertEqual(second_call["messages"][1], {"role": "system", "content": "be concise"})
        self.assertEqual(second_call["messages"][2], {"role": "user", "content": "hello"})
        self.assertEqual(second_call["messages"][3], {"role": "assistant", "content": '{"reply":"first"}'})
        self.assertEqual(second_call["messages"][4], {"role": "user", "content": "again"})

    @patch("a2a_t.llm.transports.OpenAI")
    def test_structured_forces_json_mode_and_includes_schema_instruction(self, openai_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="deepseek-chat",
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"device_type":"router"}'))],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2),
        )
        openai_cls.return_value = sdk_client

        adapter = LLMAdapterFactory.create(
            "deepseek",
            {"model": "deepseek-chat", "api_key": "sk-5a2b4be663c64c82a41814e3468c9943"},
        )
        json_schema = {
            "type": "object",
            "properties": {"device_type": {"type": "string"}},
            "required": ["device_type"],
        }

        response = adapter.structured(
            messages=[{"role": "user", "content": "extract router"}],
            json_schema=json_schema,
        )

        self.assertEqual(response.content, '{"device_type":"router"}')
        payload = sdk_client.chat.completions.create.call_args.kwargs
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertIn("JSON", payload["messages"][0]["content"])
        self.assertIn(dumps(json_schema, ensure_ascii=False), payload["messages"][1]["content"])

    def test_payload_builder_builds_structured_payload_with_schema_instruction(self) -> None:
        from a2a_t.llm.payload_builders import OpenAICompatiblePayloadBuilder

        payload = OpenAICompatiblePayloadBuilder({"provider": "deepseek"}).build_structured(
            model="deepseek-chat",
            messages=[{"role": "user", "content": "extract router"}],
            json_schema={"type": "object"},
            temperature=0.2,
            max_tokens=9,
        )

        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertIn("JSON", payload["messages"][0]["content"])
        self.assertIn(dumps({"type": "object"}, ensure_ascii=False), payload["messages"][1]["content"])
        self.assertEqual(payload["messages"][2], {"role": "user", "content": "extract router"})

    def test_response_parser_extracts_top_level_json_object_string(self) -> None:
        from a2a_t.llm.response_parsers import OpenAICompatibleResponseParser

        response = OpenAICompatibleResponseParser({"provider": "deepseek"}).parse_structured(
            response=SimpleNamespace(
                model="deepseek-chat",
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"device_type":"router"}'))],
                usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2),
            ),
            model="deepseek-chat",
        )

        self.assertEqual(response.content, '{"device_type":"router"}')
        self.assertEqual(response.usage["prompt_tokens"], 7)
        self.assertEqual(response.usage["completion_tokens"], 2)

    @patch("a2a_t.llm.transports.OpenAI")
    def test_transport_adapter_invokes_openai_chat_completions(self, openai_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = {"ok": True}
        openai_cls.return_value = sdk_client

        from a2a_t.llm.transports import OpenAICompatibleTransportAdapter

        transport = OpenAICompatibleTransportAdapter(
            {"provider": "deepseek", "api_key": "deepseek-key", "model": "deepseek-chat"}
        )
        response = transport.invoke(payload={"model": "deepseek-chat", "messages": []})

        self.assertEqual(response, {"ok": True})
        sdk_client.chat.completions.create.assert_called_once_with(model="deepseek-chat", messages=[])


if __name__ == "__main__":
    unittest.main()
