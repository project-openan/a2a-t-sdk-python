from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.llm.base import ChatMessage, LLMAdapter, LLMResponse
from a2a_t.llm import ChatMessage as PublicChatMessage, ChatSession, InMemorySessionStore, LLMAdapterFactory, LLMClient
from a2a_t.llm.errors import LLMRuntimeError


class DummyAdapter(LLMAdapter):
    @property
    def adapter_type(self) -> str:
        return "dummy"

    def _generate_from_messages(self, messages: list[ChatMessage], **kwargs: Any) -> LLMResponse:
        text = " | ".join(f"{item.role}:{item.content}" for item in messages)
        return LLMResponse(content=text, model="dummy-model", usage={}, metadata={"messages": messages, "kwargs": kwargs})

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, Any], **kwargs: Any) -> LLMResponse:
        raise LLMRuntimeError("structured not used in this test")


class BaseChatFlowTest(unittest.TestCase):
    def test_chat_creates_session_and_returns_session_id(self) -> None:
        adapter = DummyAdapter({"model": "dummy-model", "history_window": 2})

        response = adapter.chat("hello", system_prompt="be concise")

        self.assertIsNotNone(response.session_id)
        self.assertIn("system:be concise", response.content)
        self.assertIn("user:hello", response.content)

    def test_complete_builds_system_and_user_messages_without_session(self) -> None:
        adapter = DummyAdapter({"model": "dummy-model", "history_window": 2})

        response = adapter.complete("hello", system_prompt="be concise")

        self.assertIsNone(response.session_id)
        self.assertIn("system:be concise", response.content)
        self.assertIn("user:hello", response.content)

    def test_chat_reuses_existing_session_and_ignores_new_system_prompt(self) -> None:
        adapter = DummyAdapter({"model": "dummy-model", "history_window": 2})
        first = adapter.chat("hello", system_prompt="first prompt")

        second = adapter.chat("continue", system_prompt="second prompt", session_id=first.session_id)

        self.assertEqual(second.session_id, first.session_id)
        self.assertIn("system:first prompt", second.content)
        self.assertNotIn("system:second prompt", second.content)

    def test_chat_creates_provider_prefixed_session_id(self) -> None:
        adapter = DummyAdapter({"model": "dummy-model", "provider": "dummy", "history_window": 2})

        response = adapter.chat("hello")

        self.assertIsNotNone(response.session_id)
        self.assertTrue(response.session_id.startswith("dummy-"))

    def test_cross_provider_session_reuse_raises_runtime_error(self) -> None:
        shared_store = InMemorySessionStore(max_total=10, max_per_provider=10)
        openai_adapter = DummyAdapter(
            {"model": "dummy-model", "provider": "openai", "history_window": 2, "session_store": shared_store}
        )
        deepseek_adapter = DummyAdapter(
            {"model": "dummy-model", "provider": "deepseek", "history_window": 2, "session_store": shared_store}
        )

        first = openai_adapter.chat("hello")

        with self.assertRaises(LLMRuntimeError):
            deepseek_adapter.chat("continue", session_id=first.session_id)

    def test_cross_provider_reset_session_raises_runtime_error(self) -> None:
        shared_store = InMemorySessionStore(max_total=10, max_per_provider=10)
        openai_adapter = DummyAdapter(
            {"model": "dummy-model", "provider": "openai", "history_window": 2, "session_store": shared_store}
        )
        deepseek_adapter = DummyAdapter(
            {"model": "dummy-model", "provider": "deepseek", "history_window": 2, "session_store": shared_store}
        )

        first = openai_adapter.chat("hello")

        with self.assertRaises(LLMRuntimeError):
            deepseek_adapter.reset_session(first.session_id)

    def test_cross_provider_delete_session_does_not_remove(self) -> None:
        shared_store = InMemorySessionStore(max_total=10, max_per_provider=10)
        openai_adapter = DummyAdapter(
            {"model": "dummy-model", "provider": "openai", "history_window": 2, "session_store": shared_store}
        )
        deepseek_adapter = DummyAdapter(
            {"model": "dummy-model", "provider": "deepseek", "history_window": 2, "session_store": shared_store}
        )

        first = openai_adapter.chat("hello")

        deepseek_adapter.delete_session(first.session_id)

        second = openai_adapter.chat("continue", session_id=first.session_id)

        self.assertEqual(second.session_id, first.session_id)

    def test_chat_does_not_expose_legacy_updated_at_field(self) -> None:
        shared_store = InMemorySessionStore(max_total=10, max_per_provider=10)
        adapter = DummyAdapter(
            {"model": "dummy-model", "provider": "dummy", "history_window": 2, "session_store": shared_store}
        )

        response = adapter.chat("hello")

        session = shared_store._sessions[response.session_id]

        self.assertIsNotNone(session)
        self.assertFalse(hasattr(session, "updated_at"))
        self.assertIsNotNone(session.last_accessed_time)

    def test_reset_session_clears_history_and_system_prompt(self) -> None:
        adapter = DummyAdapter({"model": "dummy-model", "history_window": 2})
        first = adapter.chat("hello", system_prompt="first prompt")

        adapter.reset_session(first.session_id)
        second = adapter.chat("after reset", system_prompt="new prompt", session_id=first.session_id)

        self.assertIn("system:new prompt", second.content)
        self.assertNotIn("hello", second.content)

    def test_delete_session_causes_runtime_error_on_reuse(self) -> None:
        adapter = DummyAdapter({"model": "dummy-model", "history_window": 2})
        first = adapter.chat("hello")
        adapter.delete_session(first.session_id)

        with self.assertRaises(LLMRuntimeError):
            adapter.chat("continue", session_id=first.session_id)

    def test_history_window_keeps_recent_complete_rounds(self) -> None:
        adapter = DummyAdapter({"model": "dummy-model", "history_window": 1})

        first = adapter.chat("first")
        second = adapter.chat("second", session_id=first.session_id)

        self.assertNotIn("user:first", second.content)
        self.assertIn("user:second", second.content)

    def test_chat_does_not_forward_history_window_to_provider_kwargs(self) -> None:
        adapter = DummyAdapter({"model": "dummy-model", "history_window": 2})

        response = adapter.chat("hello", history_window=1)

        self.assertNotIn("history_window", response.metadata["kwargs"])

    def test_history_window_two_keeps_latest_completed_round(self) -> None:
        adapter = DummyAdapter({"model": "dummy-model", "history_window": 2})

        first = adapter.chat("first")
        second = adapter.chat("second", session_id=first.session_id)
        third = adapter.chat("third", session_id=first.session_id)
        outbound = third.metadata["messages"]

        self.assertEqual([item.role for item in outbound], ["user", "assistant", "user"])
        self.assertEqual(outbound[0].content, "second")
        self.assertEqual(outbound[1].content, second.content)
        self.assertEqual(outbound[2].content, "third")

    def test_chat_trims_persisted_history_to_history_window(self) -> None:
        store = InMemorySessionStore(max_total=10, max_per_provider=10)
        adapter = DummyAdapter(
            {"model": "dummy-model", "provider": "dummy", "history_window": 2, "session_store": store}
        )

        first = adapter.chat("first")
        adapter.chat("second", session_id=first.session_id)
        adapter.chat("third", session_id=first.session_id)
        stored = store.get(first.session_id)

        self.assertEqual([item.role for item in stored.messages], ["user", "assistant", "user", "assistant"])
        self.assertEqual(stored.messages[0].content, "second")


class LLMModuleExportsTest(unittest.TestCase):
    def test_public_exports_cover_chat_primitives(self) -> None:
        self.assertIsNotNone(PublicChatMessage)
        self.assertIsNotNone(ChatSession)
        self.assertIsNotNone(InMemorySessionStore)
        self.assertIsNotNone(LLMAdapterFactory)
        self.assertIsNotNone(LLMClient)


if __name__ == "__main__":
    unittest.main()
