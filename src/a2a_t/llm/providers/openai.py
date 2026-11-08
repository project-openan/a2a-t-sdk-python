"""OpenAI-compatible LLM provider client."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from openai import APITimeoutError, OpenAI

from a2a_t.llm.errors import LLMConfigError, LLMRuntimeError
from a2a_t.llm.models import LLMClientConfig, LLMResponse
from a2a_t.llm.provider import LLMClient

_JSON_MODE_INSTRUCTION_DEFAULT = (
    "Return a valid JSON object string. "
    "The output must be valid json. "
    "Do not wrap the response in markdown code fences. "
    "Do not include any explanation outside the JSON object. "
    "The JSON schema below describes the required shape of your output; never echo or repeat "
    "the schema itself."
)

_STRUCTURED_SCHEMA_INSTRUCTION_PREFIX = "Return JSON that conforms to this JSON schema: "

_SYSTEM_MESSAGE_JOINER = "\n\n"


def build_structured_messages(
    messages: list[dict[str, str]],
    json_schema: dict[str, Any],
) -> list[dict[str, str]]:
    """Build the chat messages of one structured call with a single leading system message.

    Strict OpenAI-compatible gateways (oneapi + qwen3.x, and any implementation reading "system
    message must be at the beginning" literally) accept exactly one system message and reject a
    second one with HTTP 400. The JSON-mode instruction, the schema instruction and the caller's
    leading system prompt are therefore joined into one message instead of being sent separately.
    System messages the caller places elsewhere in the list are left in place: reordering caller
    messages is not this layer's business.
    """
    system_parts = [
        _JSON_MODE_INSTRUCTION_DEFAULT,
        f"{_STRUCTURED_SCHEMA_INSTRUCTION_PREFIX}{json.dumps(json_schema, ensure_ascii=False)}",
    ]
    remaining = list(messages)
    if remaining and remaining[0].get("role") == "system":
        caller_system = remaining.pop(0).get("content")
        if isinstance(caller_system, str) and caller_system.strip():
            system_parts.append(caller_system)
    return [{"role": "system", "content": _SYSTEM_MESSAGE_JOINER.join(system_parts)}, *remaining]


def _cached_tokens(usage: Any) -> int | None:
    """Return the provider-reported cached prefix tokens, ``None`` when absent.

    OpenAI-compatible gateways report ``usage.prompt_tokens_details.cached_tokens``; DeepSeek's
    native API reports a top-level ``prompt_cache_hit_tokens``. The value is optional, and ``None``
    is kept distinct from ``0`` (reported miss) so downstream averages can skip unreported calls.
    """
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    if isinstance(cached, int) and not isinstance(cached, bool):
        return cached
    hit = getattr(usage, "prompt_cache_hit_tokens", None)
    if isinstance(hit, int) and not isinstance(hit, bool):
        return hit
    return None


class OpenAIClient(LLMClient):
    """LLM client for providers exposing an OpenAI-compatible chat API."""

    def __init__(self, config: LLMClientConfig, logger: Any | None = None) -> None:
        if not config.api_key.strip():
            raise LLMConfigError(f"{config.provider} client requires a non-empty api_key")
        self._config = config
        self._logger = logger if logger is not None else logging.getLogger(__name__)
        self._call_logger = logging.getLogger("a2a_t.llm.call")
        self._client: Any | None = None

    @property
    def provider(self) -> str:
        """Provider name configured for this client."""
        return self._config.provider

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
        attempts = max(1, self._config.timeout_retries + 1)
        for attempt in range(1, attempts + 1):
            try:
                raw_response = self._get_client().chat.completions.create(**payload)
                break
            except LLMConfigError:
                raise
            except APITimeoutError as exc:
                # A timeout is the one transport error worth retrying: a client-side timeout may
                # leave the server-side prefix computation warm, making the second identical
                # request much cheaper. Other failures are never retried here.
                if attempt >= attempts:
                    if debug_enabled:
                        self._log_error(started, exc)
                    raise LLMRuntimeError(
                        f"{self._config.provider} invocation timed out after {attempts} attempt(s): {exc}"
                    ) from exc
                self._logger.warning(
                    "%s invocation timed out (attempt %s/%s); retrying",
                    self._config.provider,
                    attempt,
                    attempts,
                )
            except Exception as exc:  # pragma: no cover - provider failure path
                if debug_enabled:
                    self._log_error(started, exc)
                raise LLMRuntimeError(f"{self._config.provider} invocation failed: {exc}") from exc
        else:  # pragma: no cover - the loop always breaks or raises
            raise RuntimeError("unreachable")
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
            "response_format": self._response_format(json_schema),
        }
        # A call-site value wins; otherwise the configured default applies. Many call sites pass
        # neither, so without this fallback ``A2AT_LLM_TEMPERATURE`` would be silently ignored.
        resolved_temperature = temperature if temperature is not None else self._config.temperature
        if resolved_temperature is not None:
            payload["temperature"] = resolved_temperature
        # ``A2AT_LLM_MAX_TOKENS`` is deliberately not applied here: a structured call must be able
        # to emit the whole JSON object, and a low cap turns a reasoning model's long output into
        # truncated, invalid JSON. Callers that need a hard cap pass ``max_tokens`` explicitly.
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        # Java parity: the reasoning effort is only forwarded when configured with a non-blank
        # value, so non-reasoning models never receive the parameter.
        if self._config.reasoning_effort:
            payload["reasoning_effort"] = self._config.reasoning_effort
        if self._config.extra_body:
            # The OpenAI SDK merges ``extra_body`` into the JSON request body, so a gateway
            # extension (``chat_template_kwargs`` and the like) reaches the endpoint without a
            # provider-specific branch in this client.
            payload["extra_body"] = dict(self._config.extra_body)
        return payload

    def _response_format(self, json_schema: dict[str, Any]) -> dict[str, Any]:
        """Return the request ``response_format`` selected by the configuration.

        ``json_object`` is the portable default. ``json_schema`` asks the gateway to constrain the
        output to the caller's schema and is opt-in because not every OpenAI-compatible gateway
        accepts it. ``strict`` is false so the existing schemas (which do not set
        ``additionalProperties``) remain valid.
        """
        if self._config.response_format == "json_schema":
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "a2at_structured_response",
                    "schema": json_schema,
                    "strict": False,
                },
            }
        return {"type": "json_object"}

    def _build_structured_messages(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
    ) -> list[dict[str, str]]:
        return build_structured_messages(messages, json_schema)

    def _parse_response(self, response: Any) -> LLMResponse:
        usage = getattr(response, "usage", None)
        # An omitted ``usage`` is unreported, not a reported zero. Fabricating 0/0/0 would make a
        # downstream token average count a call that never supplied a count.
        token_usage: dict[str, int] = {}
        if usage is not None:
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            total_tokens = int(getattr(usage, "total_tokens", 0) or 0) or (
                prompt_tokens + completion_tokens
            )
            token_usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }
            cached_tokens = _cached_tokens(usage)
            if cached_tokens is not None:
                token_usage["cached"] = cached_tokens
        return LLMResponse(
            content=self._extract_json_object_string(response),
            model=str(getattr(response, "model", self._config.model)),
            usage=token_usage,
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
        candidate = self._extract_json_object(raw_content)
        if candidate is None:
            raise LLMRuntimeError(
                f"{self._config.provider} returned invalid json: no JSON object found in response"
            )
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise LLMRuntimeError(f"{self._config.provider} returned invalid json: {exc}") from exc
        if not isinstance(parsed, dict):
            raise LLMRuntimeError(f"{self._config.provider} must return a JSON object string")
        return candidate

    @staticmethod
    def _extract_json_object(content: str) -> str | None:
        """Tolerantly extract the outermost JSON object string from raw LLM content.

        Handles three common cases where an OpenAI-compatible proxy does not enforce
        ``response_format: json_object``:
        1. Markdown code fences: `````json\\n{...}\\n````` or `````\\n{...}\\n`````.
        2. Leading/trailing prose around the JSON object.
        3. Nested braces inside string values (tracked via a depth counter that
           respects string/escape state so braces inside strings do not close the
           object prematurely).
        """
        text = content.strip()
        # Strip markdown code fences.
        if text.startswith("```"):
            newline_idx = text.find("\n")
            if newline_idx != -1:
                text = text[newline_idx + 1:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        # Fast path: the whole thing is already valid JSON.
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass
        # Slow path: scan for the outermost { ... } using a depth counter that
        # respects string/escape state.
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        # Keep scanning for a later valid object.
                        start = text.find("{", i + 1)
                        if start == -1:
                            return None
                        depth = 0
                        i = start - 1  # loop will increment to start
        return None

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
