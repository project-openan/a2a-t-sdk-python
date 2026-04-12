"""Factory for creating LLM adapters."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from a2a_t.llm.base import LLMAdapter
from a2a_t.llm.errors import LLMConfigError


class LLMAdapterFactory:
    """Factory for creating LLM adapters by type."""

    _adapters: dict[str, type[LLMAdapter]] = {}
    _adapter_imports: dict[str, tuple[str, str]] = {
        "openai": ("a2a_t.llm.adapters.openai_adapter", "OpenAIAdapter"),
        "deepseek": ("a2a_t.llm.adapters.deepseek_adapter", "DeepSeekAdapter"),
        "anthropic": ("a2a_t.llm.adapters.anthropic_adapter", "AnthropicAdapter"),
        "google": ("a2a_t.llm.adapters.google_adapter", "GoogleAdapter"),
    }

    @classmethod
    def register(cls, adapter_type: str, adapter_class: type[LLMAdapter]) -> None:
        """Register an adapter class for a type."""
        cls._adapters[adapter_type] = adapter_class

    @classmethod
    def create(cls, adapter_type: str, config: dict[str, Any]) -> LLMAdapter:
        """Create an adapter instance by type."""
        adapter_class = cls._resolve(adapter_type)
        return adapter_class(config)

    @classmethod
    def available_types(cls) -> list[str]:
        """List all registered adapter types."""
        return sorted({*cls._adapter_imports.keys(), *cls._adapters.keys()})

    @classmethod
    def _resolve(cls, adapter_type: str) -> type[LLMAdapter]:
        adapter_class = cls._adapters.get(adapter_type)
        if adapter_class is not None:
            return adapter_class

        import_target = cls._adapter_imports.get(adapter_type)
        if import_target is None:
            available = cls.available_types()
            raise LLMConfigError(f"Unknown adapter type: {adapter_type}. Available: {available}")

        module_name, class_name = import_target
        module = import_module(module_name)
        adapter_class = getattr(module, class_name)
        cls._adapters[adapter_type] = adapter_class
        return adapter_class
