"""Google adapter for structured LLM extraction."""

from __future__ import annotations

from typing import Any

from a2a_t.llm.base import LLMAdapter, LLMResponse


class GoogleAdapter(LLMAdapter):
    """LLM adapter using Google response schema structured output payloads."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._model = config.get("model", "")
        self._transport = config.get("transport")

    @property
    def adapter_type(self) -> str:
        return "google"

    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        raise NotImplementedError("Google adapter only supports structured extraction in this phase")

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        raise NotImplementedError("Google adapter only supports structured extraction in this phase")

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, Any], **kwargs: Any) -> LLMResponse:
        if not callable(self._transport):
            raise NotImplementedError("Google adapter requires a transport callable")

        payload = {
            "model": self._model,
            "contents": messages,
            "generation_config": {
                "response_mime_type": "application/json",
                "response_json_schema": json_schema,
            },
        }
        response = self._transport(payload)
        return LLMResponse(
            content=str(response.get("text", "")),
            model=str(response.get("model", self._model)),
            usage=response.get("usage", {}),
            metadata=response,
        )
