"""OpenAI adapter for structured LLM extraction."""

from __future__ import annotations

from typing import Any

from a2a_t.llm.base import LLMAdapter, LLMResponse


class OpenAIAdapter(LLMAdapter):
    """LLM adapter using OpenAI-compatible structured output payloads."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._model = config.get("model", "")
        self._transport = config.get("transport")

    @property
    def adapter_type(self) -> str:
        return "openai"

    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        raise NotImplementedError("OpenAI adapter only supports structured extraction in this phase")

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        raise NotImplementedError("OpenAI adapter only supports structured extraction in this phase")

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, Any], **kwargs: Any) -> LLMResponse:
        if not callable(self._transport):
            raise NotImplementedError("OpenAI adapter requires a transport callable")

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
