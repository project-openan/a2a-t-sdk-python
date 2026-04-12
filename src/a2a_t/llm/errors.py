"""Error types for LLM integration."""

from __future__ import annotations


class LLMError(Exception):
    """Base exception for llm module."""


class LLMConfigError(LLMError):
    """Raised when llm configuration is invalid."""


class LLMRuntimeError(LLMError):
    """Raised when llm invocation fails at runtime."""
