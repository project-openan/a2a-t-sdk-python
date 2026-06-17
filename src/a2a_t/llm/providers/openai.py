"""OpenAI-compatible LLM provider client."""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from a2a_t.llm.errors import LLMConfigError, LLMRuntimeError
from a2a_t.llm.models import LLMClientConfig, LLMResponse

_JSON_MODE_INSTRUCTION_DEFAULT = (
    "Return a valid JSON object string. "
    "The output must be valid json. "
    "Do not wrap the response in markdown code fences. "
    "Do not include any explanation outside the JSON object."
)


class OpenAICompatibleClient:
    """LLM client for providers exposing an OpenAI-compatible chat API."""

    def __init__(self, config: LLMClientConfig, logger: Any | None = None) -> None:
        if not config.api_key.strip():
            raise LLMConfigError(f"{config.provider} client requires a non-empty api_key")
        self._config = config
        self._logger = logger
        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
        )

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate a structured response constrained by a JSON schema."""
        payload = self._build_structured_payload(
            messages=messages,
            json_schema=json_schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        try:
            raw_response = self._client.chat.completions.create(**payload)
        except Exception as exc:  # pragma: no cover - provider failure path
            raise LLMRuntimeError(f"{self._config.provider} invocation failed: {exc}") from exc
        return self._parse_response(raw_response)

    def _build_structured_payload(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": self._build_structured_messages(messages, json_schema),
            "response_format": {"type": "json_object"},
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        return payload

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

    def _parse_response(self, response: Any) -> LLMResponse:
        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=self._extract_json_object_string(response),
            model=str(getattr(response, "model", self._config.model)),
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
            raise LLMRuntimeError(f"{self._config.provider} returned invalid json: {exc}") from exc
        if not isinstance(parsed, dict):
            raise LLMRuntimeError(f"{self._config.provider} must return a JSON object string")
        return raw_content

    def _extract_message_text(self, response: Any) -> str:
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise LLMRuntimeError(f"{self._config.provider} response did not include any choices")
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
            raise LLMRuntimeError(f"{self._config.provider} response did not include message content")
        return str(content)
