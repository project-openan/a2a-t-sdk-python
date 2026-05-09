"""Payload builder interfaces and defaults for composed LLM providers."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from a2a_t.llm.base import ChatMessage

_JSON_MODE_INSTRUCTION_DEFAULT = (
    "Return a valid JSON object string. "
    "The output must be valid json. "
    "Do not wrap the response in markdown code fences. "
    "Do not include any explanation outside the JSON object."
)


class PayloadBuilder(ABC):
    """Build provider-ready payloads for LLM operations."""

    @abstractmethod
    def build_complete(
        self,
        *,
        model: str,
        prompt: str,
        system_prompt: str | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        """Build the payload for a one-shot completion call."""
        raise NotImplementedError

    @abstractmethod
    def build_chat(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        """Build the payload for a chat call."""
        raise NotImplementedError

    @abstractmethod
    def build_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        """Build the payload for a structured generation call."""
        raise NotImplementedError


class OpenAICompatiblePayloadBuilder(PayloadBuilder):
    """Build provider-ready payloads for OpenAI-compatible JSON-mode providers."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    def build_complete(
        self,
        *,
        model: str,
        prompt: str,
        system_prompt: str | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        messages: list[ChatMessage] = []
        if system_prompt:
            messages.append(ChatMessage(role="system", content=system_prompt))
        messages.append(ChatMessage(role="user", content=prompt))
        return self._build_payload(
            model=model,
            messages=self._build_api_messages(messages),
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def build_chat(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        return self._build_payload(
            model=model,
            messages=self._build_api_messages(messages),
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def build_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        return self._build_payload(
            model=model,
            messages=self._build_structured_messages(messages, json_schema),
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _build_payload(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        return payload

    def _build_api_messages(self, messages: list[ChatMessage]) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": _JSON_MODE_INSTRUCTION_DEFAULT},
            *({"role": item.role, "content": item.content} for item in messages),
        ]

    def _build_structured_messages(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
    ) -> list[dict[str, str]]:
        schema_text = json.dumps(json_schema, ensure_ascii=False)
        return [
            {"role": "system", "content": _JSON_MODE_INSTRUCTION_DEFAULT},
            {"role": "system", "content": f"Return JSON that conforms to this JSON schema: {schema_text}"},
            *messages,
        ]
