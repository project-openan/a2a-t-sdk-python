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


class DeepSeekAdapterTest(unittest.TestCase):
    def test_factory_creates_deepseek_adapter(self) -> None:
        adapter = LLMAdapterFactory.create("deepseek", {"model": "deepseek-chat", "transport": lambda payload: {}})
        self.assertEqual(adapter.adapter_type, "deepseek")

    def test_complete_uses_openai_compatible_payload_shape(self) -> None:
        recorded: dict[str, Any] = {}

        def transport(payload: dict[str, Any]) -> dict[str, Any]:
            recorded["payload"] = payload
            return {"output_text": "done", "model": "deepseek-chat", "usage": {"prompt_tokens": 4, "completion_tokens": 1}}

        adapter = LLMAdapterFactory.create("deepseek", {"model": "deepseek-chat", "transport": transport})
        response = adapter.complete("say hi", system_prompt="be short")

        self.assertEqual(response.model, "deepseek-chat")
        self.assertEqual(recorded["payload"]["input"][0]["role"], "system")
        self.assertEqual(recorded["payload"]["input"][1]["role"], "user")


if __name__ == "__main__":
    unittest.main()
