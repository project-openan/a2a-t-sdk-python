"""Factory for creating LLM adapters."""

from __future__ import annotations

from typing import Any

from a2a_t.llm.base import LLMAdapter


class LLMAdapterFactory:
    """Factory for creating LLM adapters by type."""

    _adapters: dict[str, type[LLMAdapter]] = {}

    @classmethod
    def register(cls, adapter_type: str, adapter_class: type[LLMAdapter]) -> None:
        """Register an adapter class for a type."""
        cls._adapters[adapter_type] = adapter_class

    @classmethod
    def create(cls, adapter_type: str, config: dict[str, Any]) -> LLMAdapter:
        """Create an adapter instance by type."""
        if adapter_type not in cls._adapters:
            available = list(cls._adapters.keys())
            raise ValueError(f"Unknown adapter type: {adapter_type}. Available: {available}")
        return cls._adapters[adapter_type](config)

    @classmethod
    def available_types(cls) -> list[str]:
        """List all registered adapter types."""
        return list(cls._adapters.keys())


from a2a_t.llm.adapters import (
    AnthropicAdapter,
    DeepSeekAdapter,
    GoogleAdapter,
    OpenAIAdapter,
)


LLMAdapterFactory.register("openai", OpenAIAdapter)
LLMAdapterFactory.register("deepseek", DeepSeekAdapter)
LLMAdapterFactory.register("anthropic", AnthropicAdapter)
LLMAdapterFactory.register("google", GoogleAdapter)
