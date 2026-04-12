"""DeepSeek adapter backed by DeepSeek's OpenAI-compatible API."""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from a2a_t.llm.base import ChatMessage, LLMAdapter, LLMResponse
from a2a_t.llm.errors import LLMConfigError, LLMRuntimeError

_JSON_MODE_INSTRUCTION_DEFAULT = (
    "Return a valid JSON object string. "
    "The output must be valid json. "
    "Do not wrap the response in markdown code fences. "
    "Do not include any explanation outside the JSON object."
)

class DeepSeekAdapter(LLMAdapter):
    """LLM adapter for DeepSeek's OpenAI-compatible API."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        api_key = str(config.get("api_key", "")).strip()
        if not api_key:
            raise LLMConfigError("DeepSeek adapter requires a non-empty api_key")

        base_url = str(config.get("base_url", "")).strip() or "https://api.deepseek.com"
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=config.get("timeout_seconds"),
        )

    @property
    def adapter_type(self) -> str:
        return "deepseek"

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, Any], **kwargs: Any) -> LLMResponse:
        payload = self._build_payload(
            messages=self._build_structured_messages(messages, json_schema),
            temperature=kwargs.get("temperature"),
            max_tokens=kwargs.get("max_tokens"),
        )
        return self._invoke_chat_completions(payload)

    def _generate_from_messages(self, messages: list[ChatMessage], **kwargs: Any) -> LLMResponse:
        payload = self._build_payload(
            messages=self._build_api_messages(messages),
            temperature=kwargs.get("temperature"),
            max_tokens=kwargs.get("max_tokens"),
        )
        return self._invoke_chat_completions(payload)

    def _build_payload(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
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
            {
                "role": "system",
                "content": f"Return JSON that conforms to this JSON schema: {schema_text}",
            },
            *messages,
        ]

    def _invoke_chat_completions(self, payload: dict[str, Any]) -> LLMResponse:
        try:
            response = self._client.chat.completions.create(**payload)
        except Exception as exc:  # pragma: no cover - provider failure path
            raise LLMRuntimeError(f"{self.adapter_type} invocation failed: {exc}") from exc

        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=self._extract_json_object_string(response),
            model=str(getattr(response, "model", self._model)),
            usage={
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            },
            metadata={"response": response},
        )

    def _extract_json_object_string(self, response: Any) -> str:
        raw_content = self._extract_message_text(response)
        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise LLMRuntimeError(f"{self.adapter_type} returned invalid json: {exc}") from exc

        if not isinstance(parsed, dict):
            raise LLMRuntimeError(f"{self.adapter_type} must return a JSON object string")
        return raw_content

    def _extract_message_text(self, response: Any) -> str:
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise LLMRuntimeError(f"{self.adapter_type} response did not include any choices")

        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
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
        if content is None:
            raise LLMRuntimeError(f"{self.adapter_type} response did not include message content")
        return str(content)
