"""OpenAI-compatible LLM provider client."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from openai import OpenAI

from a2a_t.llm.errors import LLMConfigError, LLMRuntimeError
from a2a_t.llm.models import LLMClientConfig, LLMResponse
from a2a_t.llm.provider import LLMClient

_JSON_MODE_INSTRUCTION_DEFAULT = (
    "Return a valid JSON object string. "
    "The output must be valid json. "
    "Do not wrap the response in markdown code fences. "
    "Do not include any explanation outside the JSON object."
)


class OpenAIClient(LLMClient):
    """LLM client for providers exposing an OpenAI-compatible chat API."""

    def __init__(self, config: LLMClientConfig, logger: Any | None = None) -> None:
        if not config.api_key.strip():
            raise LLMConfigError(f"{config.provider} client requires a non-empty api_key")
        self._config = config
        self._logger = logger if logger is not None else logging.getLogger(__name__)
        self._call_logger = logging.getLogger("a2a_t.llm.call")
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._config.base_url:
            raise LLMConfigError(f"{self._config.provider} client requires a non-empty base_url")
        client_options: dict[str, Any] = {
            "api_key": self._config.api_key,
            "timeout": self._config.timeout_seconds,
            "base_url": self._config.base_url,
        }
        if not self._config.ssl_verify:
            self._logger.warning(
                "TLS certificate chain and hostname verification are disabled for the %s LLM client"
                " (A2AT_LLM_SSL_VERIFY=false); use only in controlled environments with trusted networks",
                self._config.provider,
            )
            client_options["http_client"] = httpx.Client(verify=False)
        self._client = OpenAI(**client_options)
        return self._client

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
        started = time.perf_counter()
        debug_enabled = self._call_logger.isEnabledFor(logging.DEBUG)
        if debug_enabled:
            self._log_request(payload)
        try:
            raw_response = self._get_client().chat.completions.create(**payload)
        except LLMConfigError:
            raise
        except Exception as exc:  # pragma: no cover - provider failure path
            if debug_enabled:
                self._log_error(started, exc)
            raise LLMRuntimeError(f"{self._config.provider} invocation failed: {exc}") from exc
        try:
            response = self._parse_response(raw_response)
        except LLMRuntimeError as exc:
            if debug_enabled:
                self._log_error(started, exc)
            raise
        if debug_enabled:
            self._log_response(started, raw_response, response)
        return response

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
        # Java parity: the reasoning effort is only forwarded when configured with a non-blank
        # value, so non-reasoning models never receive the parameter.
        if self._config.reasoning_effort:
            payload["reasoning_effort"] = self._config.reasoning_effort
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
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", 0) or 0) or (prompt_tokens + completion_tokens)
        return LLMResponse(
            content=self._extract_json_object_string(response),
            model=str(getattr(response, "model", self._config.model)),
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
            metadata={"response": response},
        )

    def _log_request(self, payload: dict[str, Any]) -> None:
        messages_json = json.dumps(payload["messages"], ensure_ascii=False)
        temperature = payload.get("temperature")
        max_tokens = payload.get("max_tokens")
        self._call_logger.debug(
            "llm_call event=request ts=%s provider=%s model=%s messages=%s chars=%s temperature=%s max_tokens=%s",
            self._utc_now(),
            self._config.provider,
            self._config.model,
            len(payload["messages"]),
            len(messages_json),
            "-" if temperature is None else temperature,
            "-" if max_tokens is None else max_tokens,
        )
        if self._config.detail_log_enabled:
            self._call_logger.debug(
                "llm_call event=request_body ts=%s provider=%s model=%s messages_json=%s",
                self._utc_now(),
                self._config.provider,
                self._config.model,
                messages_json,
            )

    def _log_response(self, started: float, raw_response: Any, response: LLMResponse) -> None:
        usage = response.usage
        self._call_logger.debug(
            "llm_call event=response ts=%s provider=%s model=%s elapsed_ms=%s prompt_tokens=%s"
            " completion_tokens=%s total_tokens=%s content_chars=%s response_id=%s",
            self._utc_now(),
            self._config.provider,
            response.model,
            self._elapsed_ms(started),
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            usage.get("total_tokens", 0),
            len(response.content),
            getattr(raw_response, "id", None) or "-",
        )
        if self._config.detail_log_enabled:
            self._call_logger.debug(
                "llm_call event=response_body ts=%s provider=%s model=%s content=%s",
                self._utc_now(),
                self._config.provider,
                response.model,
                response.content,
            )

    def _log_error(self, started: float, exc: Exception) -> None:
        self._call_logger.debug(
            "llm_call event=error ts=%s provider=%s model=%s elapsed_ms=%s error_code=%s error=%s",
            self._utc_now(),
            self._config.provider,
            self._config.model,
            self._elapsed_ms(started),
            type(exc).__name__,
            str(exc),
        )

    @staticmethod
    def _elapsed_ms(started: float) -> str:
        return f"{(time.perf_counter() - started) * 1000.0:.1f}"

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _extract_json_object_string(self, response: Any) -> str:
        raw_content = self._extract_message_text(response)
        # Java parity guard of the reasoning models: a blank content (the reasoning went into the
        # reasoning channel, or the model was rate limited) is a coded response-contract violation,
        # never a silent empty string.
        if raw_content is None or not raw_content.strip():
            raise LLMRuntimeError(f"{self._config.provider} returned empty content (rate limit or model timeout)")
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
