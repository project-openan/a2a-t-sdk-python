from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.llm.factory import LLMAdapterFactory


class OpenAIAdapterTest(unittest.TestCase):
    def test_complete_builds_openai_input_payload(self) -> None:
        recorded: dict[str, Any] = {}

        def transport(payload: dict[str, Any]) -> dict[str, Any]:
            recorded["payload"] = payload
            return {"output_text": "done", "model": "gpt-4.1", "usage": {"prompt_tokens": 4, "completion_tokens": 1}}

        adapter = LLMAdapterFactory.create("openai", {"model": "gpt-4.1", "transport": transport})
        response = adapter.complete("say hi", system_prompt="be short")

        self.assertEqual(response.content, "done")
        self.assertEqual(recorded["payload"]["model"], "gpt-4.1")
        self.assertEqual(recorded["payload"]["input"][0]["role"], "system")
        self.assertEqual(recorded["payload"]["input"][1]["content"], "say hi")

    def test_chat_uses_existing_session_id_and_returns_it(self) -> None:
        recorded: dict[str, Any] = {}

        def transport(payload: dict[str, Any]) -> dict[str, Any]:
            recorded["payload"] = payload
            return {"output_text": "reply", "model": "gpt-4.1", "usage": {"prompt_tokens": 8, "completion_tokens": 2}}

        adapter = LLMAdapterFactory.create("openai", {"model": "gpt-4.1", "transport": transport, "history_window": 2})
        first = adapter.chat("hello", system_prompt="be concise")
        second = adapter.chat("again", session_id=first.session_id)

        self.assertEqual(second.session_id, first.session_id)
        self.assertEqual(recorded["payload"]["input"][0]["role"], "system")
        self.assertEqual(recorded["payload"]["input"][-1]["content"], "again")


if __name__ == "__main__":
    unittest.main()
