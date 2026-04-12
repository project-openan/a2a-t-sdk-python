"""Google adapter for structured LLM extraction."""

from __future__ import annotations

from typing import Any

from a2a_t.llm.base import ChatMessage, LLMAdapter, LLMResponse
from a2a_t.llm.errors import LLMConfigError


class GoogleAdapter(LLMAdapter):
    """LLM adapter using Google response schema structured output payloads."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._transport = config.get("transport")
        if not callable(self._transport):
            raise LLMConfigError("Google adapter requires a transport callable")

    @property
    def adapter_type(self) -> str:
        return "google"

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        **kwargs: Any,
    ) -> LLMResponse:
        chat_messages = [ChatMessage(role=item["role"], content=item["content"]) for item in messages]
        return self._generate_from_messages(
            chat_messages,
            response_mime_type="application/json",
            response_json_schema=json_schema,
            **kwargs,
        )

    def _generate_from_messages(
        self,
        messages: list[ChatMessage],
        *,
        response_mime_type: str | None = None,
        response_json_schema: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "contents": [
                {
                    "role": "user" if item.role == "system" else item.role,
                    "parts": [{"text": item.content}],
                }
                for item in messages
            ],
        }
        generation_config: dict[str, Any] = {}
        if response_mime_type is not None:
            generation_config["response_mime_type"] = response_mime_type
        if response_json_schema is not None:
            generation_config["response_json_schema"] = response_json_schema
        if "temperature" in kwargs:
            generation_config["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            generation_config["max_output_tokens"] = kwargs["max_tokens"]
        if generation_config:
            payload["generation_config"] = generation_config

        response = self._transport(payload)
        return LLMResponse(
            content=str(response.get("text", "")),
            model=str(response.get("model", self._model)),
            usage=response.get("usage", {}),
            metadata=response,
        )
