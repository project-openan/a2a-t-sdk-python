from __future__ import annotations

import sys
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.llm.base import ChatMessage, ChatSession
from a2a_t.llm.errors import LLMConfigError
from a2a_t.llm.session_store import InMemorySessionStore, ProviderScopedSessionStore


class ProviderScopedSessionStoreTest(unittest.TestCase):
    def test_foreign_provider_session_is_invisible(self) -> None:
        root = InMemorySessionStore(max_total=5, max_per_provider=5)
        openai_store = ProviderScopedSessionStore("openai", root)
        deepseek_store = ProviderScopedSessionStore("deepseek", root)
        session = ChatSession(
            session_id="openai-1",
            provider="openai",
            messages=[ChatMessage(role="user", content="hello")],
        )

        openai_store.save(session)

        self.assertIsNotNone(openai_store.get("openai-1"))
        self.assertIsNone(deepseek_store.get("openai-1"))

    def test_save_rejects_mismatched_provider_metadata(self) -> None:
        root = InMemorySessionStore(max_total=5, max_per_provider=5)
        openai_store = ProviderScopedSessionStore("openai", root)
        session = ChatSession(session_id="deepseek-1", provider="deepseek")

        with self.assertRaises(LLMConfigError):
            openai_store.save(session)

    def test_save_rejects_mismatched_session_id_prefix(self) -> None:
        root = InMemorySessionStore(max_total=5, max_per_provider=5)
        openai_store = ProviderScopedSessionStore("openai", root)
        session = ChatSession(session_id="other-1", provider="openai")

        with self.assertRaises(LLMConfigError):
            openai_store.save(session)


if __name__ == "__main__":
    unittest.main()
