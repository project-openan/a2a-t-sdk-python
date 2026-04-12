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


class GoogleAdapterTest(unittest.TestCase):
    def test_complete_builds_google_contents_payload(self) -> None:
        recorded: dict[str, Any] = {}

        def transport(payload: dict[str, Any]) -> dict[str, Any]:
            recorded["payload"] = payload
            return {"text": "done", "model": "gemini-2.5-flash", "usage": {"prompt_tokens": 3, "completion_tokens": 1}}

        adapter = LLMAdapterFactory.create("google", {"model": "gemini-2.5-flash", "transport": transport})
        response = adapter.complete("say hi", system_prompt="be short")

        self.assertEqual(response.content, "done")
        self.assertEqual(recorded["payload"]["contents"][0]["role"], "user")
        self.assertEqual(recorded["payload"]["contents"][0]["parts"][0]["text"], "be short")
        self.assertEqual(recorded["payload"]["contents"][1]["parts"][0]["text"], "say hi")
        self.assertEqual(len(recorded["payload"]["contents"]), 2)

    def test_chat_builds_contents_from_session_history(self) -> None:
        recorded: dict[str, Any] = {}

        def transport(payload: dict[str, Any]) -> dict[str, Any]:
            recorded["payload"] = payload
            return {"text": "reply", "model": "gemini-2.5-flash", "usage": {"prompt_tokens": 8, "completion_tokens": 2}}

        adapter = LLMAdapterFactory.create(
            "google",
            {"model": "gemini-2.5-flash", "transport": transport, "history_window": 2},
        )
        first = adapter.chat("hello", system_prompt="be concise")
        second = adapter.chat("again", session_id=first.session_id)

        self.assertEqual(second.session_id, first.session_id)
        self.assertEqual(recorded["payload"]["contents"][-1]["parts"][0]["text"], "again")
        self.assertEqual(recorded["payload"]["contents"][1]["parts"][0]["text"], "hello")
        self.assertEqual(recorded["payload"]["contents"][2]["parts"][0]["text"], "reply")
        self.assertEqual(len(recorded["payload"]["contents"]), 4)


if __name__ == "__main__":
    unittest.main()
