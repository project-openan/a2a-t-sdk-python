"""LLM integration layer for a2a_t."""

from __future__ import annotations

from a2a_t.llm.base import LLMAdapter, LLMResponse
from a2a_t.llm.errors import LLMConfigError, LLMError, LLMRuntimeError
from a2a_t.llm.factory import LLMAdapterFactory
from a2a_t.llm.session_store import InMemorySessionStore, SessionStore

__all__ = [
    "ChatMessage",
    "ChatSession",
    "InMemorySessionStore",
    "LLMAdapter",
    "LLMAdapterFactory",
    "LLMConfigError",
    "LLMError",
    "LLMResponse",
    "LLMRuntimeError",
    "SessionStore",
]


def __getattr__(name: str):
    if name in {"ChatMessage", "ChatSession"}:
        from a2a_t.llm import base as llm_base

        try:
            return getattr(llm_base, name)
        except AttributeError as exc:
            raise AttributeError(
                f"module 'a2a_t.llm' has no attribute '{name}' in this phase"
            ) from exc
    raise AttributeError(f"module 'a2a_t.llm' has no attribute '{name}'")
