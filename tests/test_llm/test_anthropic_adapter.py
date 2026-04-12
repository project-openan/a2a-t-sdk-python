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

from a2a_t.llm.errors import LLMRuntimeError
from a2a_t.llm.factory import LLMAdapterFactory


class AnthropicAdapterTest(unittest.TestCase):
    @patch("a2a_t.llm.adapters.anthropic_adapter.Anthropic")
    def test_structured_uses_anthropic_messages_api(self, anthropic_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.messages.create.return_value = SimpleNamespace(
            model="claude-sonnet-4-5",
            usage=SimpleNamespace(input_tokens=9, output_tokens=2),
            content=[
                SimpleNamespace(
                    type="tool_use",
                    input={"slots": {"device_type": "router"}, "notes": [], "confidence": 0.7},
                )
            ],
        )
        anthropic_cls.return_value = sdk_client

        adapter = LLMAdapterFactory.create("anthropic", {"model": "claude-sonnet-4-5", "api_key": "anthropic-key"})
        response = adapter.structured(
            messages=[{"role": "user", "content": "extract"}],
            json_schema={"type": "object"},
        )

        sdk_client.messages.create.assert_called_once()
        self.assertIn('"device_type": "router"', response.content)
        self.assertEqual(response.model, "claude-sonnet-4-5")

    @patch("a2a_t.llm.adapters.anthropic_adapter.Anthropic")
    def test_complete_and_chat_remain_unsupported(self, anthropic_cls: Mock) -> None:
        anthropic_cls.return_value = Mock()
        adapter = LLMAdapterFactory.create("anthropic", {"model": "claude-sonnet-4-5", "api_key": "anthropic-key"})

        with self.assertRaises(LLMRuntimeError):
            adapter.complete("hello")

        with self.assertRaises(LLMRuntimeError):
            adapter.chat("hello")


if __name__ == "__main__":
    unittest.main()
