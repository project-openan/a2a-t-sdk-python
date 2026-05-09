"""Response parser interfaces and defaults for composed LLM providers."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from a2a_t.llm.base import LLMResponse
from a2a_t.llm.errors import LLMRuntimeError


class ResponseParser(ABC):
    """Parse raw provider responses into normalized LLM responses."""

    @abstractmethod
    def parse_complete(self, *, response: Any, model: str) -> LLMResponse:
        """Parse the raw response for a completion call."""
        raise NotImplementedError

    @abstractmethod
    def parse_chat(self, *, response: Any, model: str) -> LLMResponse:
        """Parse the raw response for a chat call."""
        raise NotImplementedError

    @abstractmethod
    def parse_structured(self, *, response: Any, model: str) -> LLMResponse:
        """Parse the raw response for a structured call."""
        raise NotImplementedError


class OpenAICompatibleResponseParser(ResponseParser):
    """Parse raw OpenAI-compatible JSON-mode responses into normalized LLM responses."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._provider = str(config.get("provider", "provider")).strip() or "provider"

    def parse_complete(self, *, response: Any, model: str) -> LLMResponse:
        return self._build_response(response=response, model=model)

    def parse_chat(self, *, response: Any, model: str) -> LLMResponse:
        return self._build_response(response=response, model=model)

    def parse_structured(self, *, response: Any, model: str) -> LLMResponse:
        return self._build_response(response=response, model=model)

    def _build_response(self, *, response: Any, model: str) -> LLMResponse:
        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=self._extract_json_object_string(response),
            model=str(getattr(response, "model", model)),
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
            raise LLMRuntimeError(f"{self._provider} returned invalid json: {exc}") from exc
        if not isinstance(parsed, dict):
            raise LLMRuntimeError(f"{self._provider} must return a JSON object string")
        return raw_content

    def _extract_message_text(self, response: Any) -> str:
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise LLMRuntimeError(f"{self._provider} response did not include any choices")
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
            raise LLMRuntimeError(f"{self._provider} response did not include message content")
        return str(content)
