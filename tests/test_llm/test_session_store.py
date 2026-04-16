from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
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


class InMemorySessionStoreTest(unittest.TestCase):
    def test_get_refreshes_last_accessed_time(self) -> None:
        root = InMemorySessionStore(max_total=5, max_per_provider=5)
        session = ChatSession(session_id="openai-1", provider="openai")
        root.save(session)
        old_time = datetime.now(UTC) - timedelta(minutes=5)
        root._sessions["openai-1"].last_accessed_time = old_time

        refreshed = root.get("openai-1")

        self.assertGreater(refreshed.last_accessed_time, old_time)

    def test_get_does_not_expose_legacy_updated_at_field(self) -> None:
        root = InMemorySessionStore(max_total=5, max_per_provider=5)
        session = ChatSession(session_id="openai-1", provider="openai")
        root.save(session)

        refreshed = root.get("openai-1")

        self.assertFalse(hasattr(refreshed, "updated_at"))

    def test_save_evicts_oldest_session_when_provider_limit_is_exceeded(self) -> None:
        root = InMemorySessionStore(max_total=10, max_per_provider=2)
        old_time = datetime.now(UTC) - timedelta(minutes=10)
        root.save(ChatSession(session_id="openai-1", provider="openai", last_accessed_time=old_time))
        root.save(ChatSession(session_id="openai-2", provider="openai"))
        root.save(ChatSession(session_id="openai-3", provider="openai"))

        self.assertIsNone(root.get("openai-1"))
        self.assertIsNotNone(root.get("openai-2"))
        self.assertIsNotNone(root.get("openai-3"))

    def test_save_evicts_global_oldest_session_when_total_limit_is_exceeded(self) -> None:
        root = InMemorySessionStore(max_total=2, max_per_provider=2)
        old_time = datetime.now(UTC) - timedelta(minutes=10)
        root.save(ChatSession(session_id="openai-1", provider="openai", last_accessed_time=old_time))
        root.save(ChatSession(session_id="deepseek-1", provider="deepseek"))
        root.save(ChatSession(session_id="openai-2", provider="openai"))

        self.assertIsNone(root.get("openai-1"))
        self.assertIsNotNone(root.get("deepseek-1"))
        self.assertIsNotNone(root.get("openai-2"))

    def test_reset_refreshes_timestamps(self) -> None:
        root = InMemorySessionStore(max_total=5, max_per_provider=5)
        old_time = datetime.now(UTC) - timedelta(minutes=10)
        root.save(ChatSession(session_id="openai-1", provider="openai"))
        root._sessions["openai-1"].last_accessed_time = old_time

        refreshed = root.reset("openai-1")

        self.assertIsNotNone(refreshed)
        self.assertGreater(refreshed.last_accessed_time, old_time)
        self.assertFalse(hasattr(refreshed, "updated_at"))

    def test_recent_access_preserves_session_during_provider_eviction(self) -> None:
        root = InMemorySessionStore(max_total=10, max_per_provider=2)
        old_time = datetime.now(UTC) - timedelta(minutes=10)
        root.save(ChatSession(session_id="openai-1", provider="openai"))
        root.save(ChatSession(session_id="openai-2", provider="openai"))
        root._sessions["openai-1"].last_accessed_time = old_time

        root.get("openai-1")
        root.save(ChatSession(session_id="openai-3", provider="openai"))

        self.assertIsNotNone(root.get("openai-1"))
        self.assertIsNone(root.get("openai-2"))


if __name__ == "__main__":
    unittest.main()
