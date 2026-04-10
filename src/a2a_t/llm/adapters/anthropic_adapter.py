"""Anthropic adapter for structured LLM extraction."""

from __future__ import annotations

import json
from typing import Any

from a2a_t.llm.base import LLMAdapter, LLMResponse


class AnthropicAdapter(LLMAdapter):
    """LLM adapter using Anthropic tool-use structured output payloads."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._model = config.get("model", "")
        self._transport = config.get("transport")

    @property
    def adapter_type(self) -> str:
        return "anthropic"

    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        raise NotImplementedError("Anthropic adapter only supports structured extraction in this phase")

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        raise NotImplementedError("Anthropic adapter only supports structured extraction in this phase")

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, Any], **kwargs: Any) -> LLMResponse:
        if not callable(self._transport):
            raise NotImplementedError("Anthropic adapter requires a transport callable")

        payload = {
            "model": self._model,
            "messages": messages,
            "tools": [
                {
                    "name": "prompt_slot_extraction",
                    "description": "Extract prompt slots as structured JSON",
                    "input_schema": json_schema,
                }
            ],
        }
        response = self._transport(payload)
        tool_input = response.get("tool_input", {})
        return LLMResponse(
            content=json.dumps(tool_input, ensure_ascii=False),
            model=str(response.get("model", self._model)),
            usage=response.get("usage", {}),
            metadata=response,
        )
