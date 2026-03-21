"""Message queue adapter for LLM integration."""

from __future__ import annotations

from typing import Any

from a2a_t_sdk.llm.base import LLMAdapter, LLMResponse


class MQAdapter(LLMAdapter):
    """LLM adapter using message queue (MQ)."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._broker = config.get("broker", "")
        self._queue = config.get("queue", "")
        self._model = config.get("model", "default")

    @property
    def adapter_type(self) -> str:
        return "mq"

    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        """Generate completion via message queue."""
        raise NotImplementedError("MQ adapter requires backend integration")

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        """Generate chat completion via message queue."""
        raise NotImplementedError("MQ adapter requires backend integration")
