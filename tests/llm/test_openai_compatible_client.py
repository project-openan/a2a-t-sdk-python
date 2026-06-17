# ruff: noqa: E402

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

from a2a_t.llm.errors import LLMRuntimeError
from a2a_t.llm.factory import LLMClientFactory
from a2a_t.llm.models import LLMClientConfig
from a2a_t.llm.provider import LLMClient


def build_config(provider: str = "deepseek", base_url: str | None = None) -> LLMClientConfig:
    return LLMClientConfig(
        provider=provider,
        model="deepseek-chat",
        api_key="deepseek-key",
        base_url=base_url,
        history_window=10,
        max_tokens=None,
        temperature=None,
        timeout_seconds=None,
        session_max_total=300,
        session_max_per_provider=100,
    )


class OpenAICompatibleClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_clients = dict(LLMClientFactory._clients)
        self.original_client_defaults = dict(LLMClientFactory._client_defaults)

    def tearDown(self) -> None:
        LLMClientFactory._clients = self.original_clients
        LLMClientFactory._client_defaults = self.original_client_defaults

    @patch("a2a_t.llm.providers.openai.OpenAI")
    def test_factory_creates_deepseek_as_openai_compatible_client(self, openai_cls: Mock) -> None:
        openai_cls.return_value = Mock()

        client = LLMClientFactory.create("deepseek", build_config())

        from a2a_t.llm.providers.openai import OpenAICompatibleClient

        self.assertIsInstance(client, OpenAICompatibleClient)
        self.assertIsInstance(client, LLMClient)
        openai_cls.assert_called_once_with(
            api_key="deepseek-key",
            base_url="https://api.deepseek.com",
            timeout=None,
        )

    @patch("a2a_t.llm.providers.openai.OpenAI")
    def test_structured_forces_json_mode_and_includes_schema_instruction(self, openai_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="deepseek-chat",
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"device_type":"router"}'))],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2),
        )
        openai_cls.return_value = sdk_client

        from a2a_t.llm.providers.openai import OpenAICompatibleClient

        client = OpenAICompatibleClient(build_config(base_url="https://custom.example/v1"))
        json_schema = {
            "type": "object",
            "properties": {"device_type": {"type": "string"}},
            "required": ["device_type"],
        }

        response = client.structured(
            messages=[{"role": "user", "content": "extract router"}],
            json_schema=json_schema,
            temperature=0.2,
            max_tokens=9,
        )

        self.assertEqual(response.content, '{"device_type":"router"}')
        self.assertEqual(response.model, "deepseek-chat")
        self.assertEqual(response.usage["prompt_tokens"], 7)
        self.assertEqual(response.usage["completion_tokens"], 2)
        openai_cls.assert_called_once_with(
            api_key="deepseek-key",
            base_url="https://custom.example/v1",
            timeout=None,
        )
        payload = sdk_client.chat.completions.create.call_args.kwargs
        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(payload["max_tokens"], 9)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertIn("JSON", payload["messages"][0]["content"])
        self.assertIn(dumps(json_schema, ensure_ascii=False), payload["messages"][1]["content"])
        self.assertEqual(payload["messages"][2], {"role": "user", "content": "extract router"})

    @patch("a2a_t.llm.providers.openai.OpenAI")
    def test_structured_rejects_non_json_object_response(self, openai_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="deepseek-chat",
            choices=[SimpleNamespace(message=SimpleNamespace(content='["not-object"]'))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
        openai_cls.return_value = sdk_client

        from a2a_t.llm.providers.openai import OpenAICompatibleClient

        client = OpenAICompatibleClient(build_config())

        with self.assertRaises(LLMRuntimeError):
            client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})
