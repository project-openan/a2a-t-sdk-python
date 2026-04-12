# ruff: noqa: E402, I001

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.llm.factory import LLMAdapterFactory


class OpenAIAdapterTest(unittest.TestCase):
    @patch("a2a_t.llm.adapters.openai_adapter.OpenAI")
    def test_complete_uses_openai_chat_completions_sdk(self, openai_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4.1",
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"result":"done"}'))],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=1),
        )
        openai_cls.return_value = sdk_client

        adapter = LLMAdapterFactory.create("openai", {"model": "gpt-4.1", "api_key": "sk-test"})
        response = adapter.complete("say hi", system_prompt="be short", temperature=0.2, max_tokens=64)

        self.assertEqual(response.content, '{"result":"done"}')
        sdk_client.chat.completions.create.assert_called_once()
        self.assertEqual(
            sdk_client.chat.completions.create.call_args.kwargs,
            {
                "model": "gpt-4.1",
                "messages": [
                    {"role": "system", "content": "be short"},
                    {"role": "user", "content": "say hi"},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
                "max_tokens": 64,
            },
        )

    @patch("a2a_t.llm.adapters.openai_adapter.OpenAI")
    def test_chat_uses_existing_session_id_and_returns_it(self, openai_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.side_effect = [
            SimpleNamespace(
                model="gpt-4.1",
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"reply":"first"}'))],
                usage=SimpleNamespace(prompt_tokens=6, completion_tokens=2),
            ),
            SimpleNamespace(
                model="gpt-4.1",
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"reply":"second"}'))],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
            ),
        ]
        openai_cls.return_value = sdk_client

        adapter = LLMAdapterFactory.create("openai", {"model": "gpt-4.1", "api_key": "sk-test", "history_window": 2})
        first = adapter.chat("hello", system_prompt="be concise")
        second = adapter.chat("again", session_id=first.session_id)

        self.assertEqual(second.session_id, first.session_id)
        second_call = sdk_client.chat.completions.create.call_args_list[1].kwargs
        self.assertEqual(second_call["response_format"], {"type": "json_object"})
        self.assertEqual(second_call["messages"][0], {"role": "system", "content": "be concise"})
        self.assertEqual(second_call["messages"][-1], {"role": "user", "content": "again"})


if __name__ == "__main__":
    unittest.main()
