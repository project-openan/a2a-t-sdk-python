"""OpenAI adapter backed by the official OpenAI SDK."""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from a2a_t.llm.base import ChatMessage, LLMAdapter, LLMResponse
from a2a_t.llm.errors import LLMConfigError, LLMRuntimeError


class OpenAIAdapter(LLMAdapter):
    """LLM adapter using OpenAI-compatible chat completions."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        api_key = str(config.get("api_key", "")).strip()
        if not api_key:
            raise LLMConfigError("OpenAI adapter requires a non-empty api_key")

        self._client = OpenAI(
            api_key=api_key,
            base_url=self._resolve_base_url(config),
            timeout=config.get("timeout_seconds"),
        )

    @property
    def adapter_type(self) -> str:
        return "openai"

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, Any], **kwargs: Any) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": item["role"], "content": item["content"]} for item in messages],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "prompt_slot_extraction",
                    "schema": json_schema,
                },
            },
        }
        if kwargs.get("temperature") is not None:
            payload["temperature"] = kwargs["temperature"]
        if kwargs.get("max_tokens") is not None:
            payload["max_tokens"] = kwargs["max_tokens"]

        return self._invoke_chat_completions(payload)

    def _generate_from_messages(self, messages: list[ChatMessage], **kwargs: Any) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
        }
        if kwargs.get("temperature") is not None:
            payload["temperature"] = kwargs["temperature"]
        if kwargs.get("max_tokens") is not None:
            payload["max_tokens"] = kwargs["max_tokens"]

        return self._invoke_chat_completions(payload)

    def _invoke_chat_completions(self, payload: dict[str, Any]) -> LLMResponse:
        try:
            response = self._client.chat.completions.create(**payload)
        except Exception as exc:  # pragma: no cover - provider failure path
            raise LLMRuntimeError(f"{self.adapter_type} invocation failed: {exc}") from exc

        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=self._extract_response_text(response),
            model=str(getattr(response, "model", self._model)),
            usage={
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            },
            metadata={"response": response},
        )

    def _resolve_base_url(self, config: dict[str, Any]) -> str | None:
        base_url = str(config.get("base_url", "")).strip()
        return base_url or None

    def _extract_response_text(self, response: Any) -> str:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""

        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                text = getattr(item, "text", None)
                if text is not None:
                    parts.append(str(text))
                elif isinstance(item, dict) and "text" in item:
                    parts.append(str(item["text"]))
                else:
                    parts.append(str(item))
            return "".join(parts)
        return "" if content is None else str(content)
