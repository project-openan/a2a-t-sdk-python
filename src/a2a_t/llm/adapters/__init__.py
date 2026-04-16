"""LLM adapters for a2a_t."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "AnthropicAdapter",
    "DeepSeekAdapter",
    "GoogleAdapter",
    "OpenAIAdapter",
    "anthropic_adapter",
    "deepseek_adapter",
    "google_adapter",
    "openai_adapter",
]

_ADAPTER_MODULES = {
    "AnthropicAdapter": ("a2a_t.llm.adapters.anthropic_adapter", "AnthropicAdapter"),
    "DeepSeekAdapter": ("a2a_t.llm.adapters.deepseek_adapter", "DeepSeekAdapter"),
    "GoogleAdapter": ("a2a_t.llm.adapters.google_adapter", "GoogleAdapter"),
    "OpenAIAdapter": ("a2a_t.llm.adapters.openai_adapter", "OpenAIAdapter"),
}

_MODULE_EXPORTS = {
    "anthropic_adapter": "a2a_t.llm.adapters.anthropic_adapter",
    "deepseek_adapter": "a2a_t.llm.adapters.deepseek_adapter",
    "google_adapter": "a2a_t.llm.adapters.google_adapter",
    "openai_adapter": "a2a_t.llm.adapters.openai_adapter",
}


def __getattr__(name: str):
    module_name = _MODULE_EXPORTS.get(name)
    if module_name is not None:
        return import_module(module_name)

    target = _ADAPTER_MODULES.get(name)
    if target is None:
        raise AttributeError(f"module 'a2a_t.llm.adapters' has no attribute '{name}'")

    module_name, class_name = target
    module = import_module(module_name)
    return getattr(module, class_name)
