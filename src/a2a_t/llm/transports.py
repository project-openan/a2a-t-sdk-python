"""Transport interfaces for composed LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from openai import OpenAI

from a2a_t.llm.errors import LLMConfigError, LLMRuntimeError


class TransportAdapter(ABC):
    """Send one provider-ready payload and return the raw provider response."""

    @abstractmethod
    def invoke(self, *, payload: dict[str, Any]) -> Any:
        """Execute a provider request with the supplied payload."""
        raise NotImplementedError


class OpenAICompatibleTransportAdapter(TransportAdapter):
    """Transport adapter for providers exposing the OpenAI-compatible chat API."""

    def __init__(self, config: dict[str, Any]) -> None:
        api_key = str(config.get("api_key", "")).strip()
        if not api_key:
            provider = str(config.get("provider", "provider")).strip() or "provider"
            raise LLMConfigError(f"{provider} adapter requires a non-empty api_key")
        base_url = str(config.get("base_url", "")).strip() or None
        self._provider = str(config.get("provider", "provider")).strip() or "provider"
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=config.get("timeout_seconds"),
        )

    def invoke(self, *, payload: dict[str, Any]) -> Any:
        """Execute one OpenAI-compatible chat-completions request."""
        try:
            return self._client.chat.completions.create(**payload)
        except Exception as exc:  # pragma: no cover - provider failure path
            raise LLMRuntimeError(f"{self._provider} invocation failed: {exc}") from exc
