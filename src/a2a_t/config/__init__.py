"""Configuration management for a2a_t."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "A2ATConfig",
    "ConfigError",
    "ConfigFileNotFoundError",
    "LlmRuntimeConfig",
    "PromptRuntimeConfig",
]


def __getattr__(name: str) -> Any:
    if name in {"ConfigError", "ConfigFileNotFoundError"}:
        value = getattr(import_module("a2a_t.config.errors"), name)
    elif name in {"A2ATConfig", "LlmRuntimeConfig"}:
        value = getattr(import_module("a2a_t.config.models"), name)
    elif name in {"PromptRuntimeConfig"}:
        value = getattr(import_module("a2a_t.config.models"), name)
    else:
        raise AttributeError(f"module 'a2a_t.config' has no attribute {name!r}")

    globals()[name] = value
    return value
