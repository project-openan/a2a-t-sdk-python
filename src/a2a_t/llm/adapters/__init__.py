"""LLM adapters for a2a_t."""

from __future__ import annotations

from a2a_t.llm.adapters import composed_adapter, openai_compatible
from a2a_t.llm.adapters.composed_adapter import ComposedLLMAdapter
from a2a_t.llm.adapters.openai_compatible import OpenAICompatibleAdapter

__all__ = [
    "ComposedLLMAdapter",
    "OpenAICompatibleAdapter",
    "composed_adapter",
    "openai_compatible",
]
