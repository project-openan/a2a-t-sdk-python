"""Anthropic adapter for structured LLM extraction."""

from __future__ import annotations

import json
from typing import Any

try:
    from anthropic import Anthropic
except ModuleNotFoundError:  # pragma: no cover - optional dependency in test env
    Anthropic = None

from a2a_t.llm.base import LLMAdapter, LLMResponse
from a2a_t.llm.errors import LLMConfigError, LLMRuntimeError


class AnthropicAdapter(LLMAdapter):
    """LLM adapter using Anthropic tool-use structured output payloads."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        api_key = str(config.get("api_key", "")).strip()
        if not api_key:
            raise LLMConfigError("Anthropic adapter requires a non-empty api_key")
        if Anthropic is None:
            raise LLMConfigError("Anthropic adapter requires the 'anthropic' package to be installed")

        self._client = Anthropic(
            api_key=api_key,
            base_url=str(config.get("base_url", "")).strip() or None,
            timeout=config.get("timeout_seconds"),
        )

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
        system_prompt = "\n\n".join(item["content"] for item in messages if item["role"] == "system") or None
        anthropic_messages = [
            {"role": item["role"], "content": item["content"]}
            for item in messages
            if item["role"] != "system"
        ]
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": int(kwargs.get("max_tokens", self._config.get("max_tokens", 2000))),
            "messages": anthropic_messages,
            "tools": [
                {
                    "name": "prompt_slot_extraction",
                    "description": "Extract prompt slots as structured JSON",
                    "input_schema": json_schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": "prompt_slot_extraction"},
        }
        if system_prompt:
            payload["system"] = system_prompt
        if kwargs.get("temperature") is not None:
            payload["temperature"] = kwargs["temperature"]

        try:
            response = self._client.messages.create(**payload)
        except Exception as exc:  # pragma: no cover - provider failure path
            raise LLMRuntimeError(f"{self.adapter_type} invocation failed: {exc}") from exc

        tool_block = next((item for item in response.content if getattr(item, "type", None) == "tool_use"), None)
        if tool_block is None:
            raise LLMRuntimeError("Anthropic structured response did not contain a tool_use block")

        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=json.dumps(tool_block.input, ensure_ascii=False),
            model=str(getattr(response, "model", self._model)),
            usage={
                "prompt_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            },
            metadata={"response": response},
        )
