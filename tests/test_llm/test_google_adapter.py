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


class GoogleAdapterTest(unittest.TestCase):
    @patch("a2a_t.llm.adapters.google_adapter.types.GenerateContentConfig")
    @patch("a2a_t.llm.adapters.google_adapter.genai.Client")
    def test_complete_uses_google_generate_content(self, client_cls: Mock, config_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.models.generate_content.return_value = SimpleNamespace(
            text="done",
            model_version="gemini-2.5-flash",
            usage_metadata=SimpleNamespace(prompt_token_count=3, candidates_token_count=1),
        )
        client_cls.return_value = sdk_client
        config_cls.side_effect = lambda **kwargs: kwargs

        adapter = LLMAdapterFactory.create("google", {"model": "gemini-2.5-flash", "api_key": "google-key"})
        response = adapter.complete("say hi", system_prompt="be short", temperature=0.2, max_tokens=64)

        self.assertEqual(response.content, "done")
        sdk_client.models.generate_content.assert_called_once_with(
            model="gemini-2.5-flash",
            contents=[{"role": "user", "parts": [{"text": "say hi"}]}],
            config={"system_instruction": "be short", "temperature": 0.2, "max_output_tokens": 64},
        )

    @patch("a2a_t.llm.adapters.google_adapter.types.GenerateContentConfig")
    @patch("a2a_t.llm.adapters.google_adapter.genai.Client")
    def test_chat_builds_google_history_from_session_messages(self, client_cls: Mock, config_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.models.generate_content.side_effect = [
            SimpleNamespace(
                text="first-reply",
                model_version="gemini-2.5-flash",
                usage_metadata=SimpleNamespace(prompt_token_count=6, candidates_token_count=2),
            ),
            SimpleNamespace(
                text="second-reply",
                model_version="gemini-2.5-flash",
                usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=3),
            ),
        ]
        client_cls.return_value = sdk_client
        config_cls.side_effect = lambda **kwargs: kwargs

        adapter = LLMAdapterFactory.create("google", {"model": "gemini-2.5-flash", "api_key": "google-key", "history_window": 2})
        first = adapter.chat("hello", system_prompt="be concise")
        second = adapter.chat("again", session_id=first.session_id)

        second_call = sdk_client.models.generate_content.call_args_list[1].kwargs
        self.assertEqual(second.session_id, first.session_id)
        self.assertEqual(second_call["contents"][-1], {"role": "user", "parts": [{"text": "again"}]})
        self.assertEqual(second_call["config"]["system_instruction"], "be concise")


if __name__ == "__main__":
    unittest.main()
