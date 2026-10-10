"""LLM request/response logger with mock fallback support.

Patches OpenAIClient.structured and _build_structured_payload to print
request/response payloads. When mock is enabled, returns canned responses
instead of calling the real LLM API.
"""

from __future__ import annotations

from typing import Any

from a2a_t.llm.providers.openai import OpenAIClient

from common.logging_utils import format_payload_log, format_stage_log
from common.mock_llm import get_mock_payload, get_mock_response, is_mock_enabled

_original_build_payload = OpenAIClient._build_structured_payload
_original_structured = OpenAIClient.structured

_sink: Any = print
_role: str = "llm"


def set_llm_log_sink(sink: object) -> None:
    """Set the output sink for LLM request/response logs (default: print)."""
    global _sink
    _sink = sink


def _patched_build_payload(
    self: OpenAIClient,
    *,
    messages: list[dict[str, str]],
    json_schema: dict[str, Any],
    temperature: float | None,
    max_tokens: int | None,
) -> dict[str, Any]:
    if is_mock_enabled():
        payload = get_mock_payload(
            messages=messages,
            json_schema=json_schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    else:
        payload = _original_build_payload(
            self,
            messages=messages,
            json_schema=json_schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    _sink(format_payload_log(role=_role, stage="llm-request", payload=payload))
    return payload


def _patched_structured(
    self: OpenAIClient,
    *,
    messages: list[dict[str, str]],
    json_schema: dict[str, Any],
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> Any:
    if is_mock_enabled():
        self._build_structured_payload(
            messages=messages,
            json_schema=json_schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        result = get_mock_response()
        _sink(format_stage_log(role=_role, stage="llm-mock", detail="using canned mock LLM response"))
    else:
        result = _original_structured(
            self,
            messages=messages,
            json_schema=json_schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    _sink(
        format_payload_log(
            role=_role,
            stage="llm-response",
            payload={
                "content": result.content,
                "model": result.model,
                "usage": result.usage,
            },
        )
    )
    return result


def install_llm_logger(role: str) -> None:
    """Patch OpenAIClient methods to log request/response payloads with the given role label."""
    global _role
    _role = role
    OpenAIClient._build_structured_payload = _patched_build_payload
    OpenAIClient.structured = _patched_structured
