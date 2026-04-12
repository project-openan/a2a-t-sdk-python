"""Anthropic adapter for structured LLM extraction."""

from __future__ import annotations

import json
from typing import Any

from a2a_t.llm.base import LLMAdapter, LLMResponse
from a2a_t.llm.errors import LLMRuntimeError


class AnthropicAdapter(LLMAdapter):
    """LLM adapter using Anthropic tool-use structured output payloads."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._model = config.get("model", "")
        self._transport = config.get("transport")

    @property
    def adapter_type(self) -> str:
        return "anthropic"

    def complete(self, prompt: str, system_prompt: str | None = None, **kwargs: Any) -> LLMResponse:
        raise LLMRuntimeError("Anthropic adapter does not support complete() in phase 1")

    def chat(
        self,
        message: str,
        system_prompt: str | None = None,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        raise LLMRuntimeError("Anthropic adapter does not support chat() in phase 1")

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, Any], **kwargs: Any) -> LLMResponse:
        if not callable(self._transport):
            raise LLMRuntimeError("Anthropic adapter requires a transport callable")

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
