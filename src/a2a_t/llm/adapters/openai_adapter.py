"""OpenAI adapter for structured LLM extraction."""

from __future__ import annotations

from typing import Any

from a2a_t.llm.base import ChatMessage, LLMAdapter, LLMResponse
from a2a_t.llm.errors import LLMConfigError


class OpenAIAdapter(LLMAdapter):
    """LLM adapter using OpenAI-compatible structured output payloads."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._transport = config.get("transport")
        if not callable(self._transport):
            raise LLMConfigError("OpenAI adapter requires a transport callable")

    @property
    def adapter_type(self) -> str:
        return "openai"

    def _generate_from_messages(self, messages: list[ChatMessage], **kwargs: Any) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "input": [{"role": item.role, "content": item.content} for item in messages],
        }
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]

        response = self._transport(payload)
        return LLMResponse(
            content=str(response.get("output_text", "")),
            model=str(response.get("model", self._model)),
            usage=response.get("usage", {}),
            metadata=response,
        )

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, Any], **kwargs: Any) -> LLMResponse:
        payload = {
            "model": self._model,
            "input": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "prompt_slot_extraction",
                    "schema": json_schema,
                },
            },
        }
        response = self._transport(payload)
        return LLMResponse(
            content=str(response.get("output_text", "")),
            model=str(response.get("model", self._model)),
            usage=response.get("usage", {}),
            metadata=response,
        )
