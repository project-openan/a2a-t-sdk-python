"""Google adapter backed by the official Gemini SDK."""

from __future__ import annotations

from typing import Any

from google import genai
from google.genai import types

from a2a_t.llm.base import ChatMessage, LLMAdapter, LLMResponse
from a2a_t.llm.errors import LLMConfigError, LLMRuntimeError


class GoogleAdapter(LLMAdapter):
    """LLM adapter using Google response schema structured output payloads."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        api_key = str(config.get("api_key", "")).strip()
        if not api_key:
            raise LLMConfigError("Google adapter requires a non-empty api_key")

        self._client = genai.Client(api_key=api_key)

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
        system_instruction = next((item.content for item in messages if item.role == "system"), None)
        contents = [
            {
                "role": "model" if item.role == "assistant" else "user",
                "parts": [{"text": item.content}],
            }
            for item in messages
            if item.role != "system"
        ]

        config_kwargs: dict[str, Any] = {}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if response_mime_type is not None:
            config_kwargs["response_mime_type"] = response_mime_type
        if response_json_schema is not None:
            config_kwargs["response_json_schema"] = response_json_schema
        if kwargs.get("temperature") is not None:
            config_kwargs["temperature"] = kwargs["temperature"]
        if kwargs.get("max_tokens") is not None:
            config_kwargs["max_output_tokens"] = kwargs["max_tokens"]

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs) if config_kwargs else None,
            )
        except Exception as exc:  # pragma: no cover - provider failure path
            raise LLMRuntimeError(f"{self.adapter_type} invocation failed: {exc}") from exc

        usage = getattr(response, "usage_metadata", None)
        return LLMResponse(
            content=str(getattr(response, "text", "") or ""),
            model=str(getattr(response, "model_version", self._model)),
            usage={
                "prompt_tokens": int(getattr(usage, "prompt_token_count", 0) or 0),
                "completion_tokens": int(getattr(usage, "candidates_token_count", 0) or 0),
            },
            metadata={"response": response},
        )
