"""HTTP API adapter for LLM integration."""

from __future__ import annotations

from typing import Any

from a2a_t.llm.base import LLMAdapter, LLMResponse


class HTTPAdapter(LLMAdapter):
    """LLM adapter using HTTP/REST API."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._base_url = config.get("base_url", "")
        self._api_key = config.get("api_key", "")
        self._model = config.get("model", "default")

    @property
    def adapter_type(self) -> str:
        return "http"

    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        """Generate completion via HTTP API."""
        raise NotImplementedError("HTTP adapter requires backend integration")

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        """Generate chat completion via HTTP API."""
        raise NotImplementedError("HTTP adapter requires backend integration")
