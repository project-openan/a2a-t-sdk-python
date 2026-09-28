"""LLM request/response logger with mock fallback support.

Patches ``OpenAIClient.structured`` and ``_build_structured_payload``. When the schema-routed mock
is enabled, the canned response selected by output-schema signature is returned instead of calling
the real LLM API. Full request/response payloads are printed only when ``A2AT_SAMPLE_DEBUG`` is
enabled (otherwise the console stays focused on the A2A flow); the one-line ``llm-mock`` marker is
always printed so mock usage remains visible.
"""

from __future__ import annotations

from typing import Any

from a2a_t.llm.providers.openai import OpenAIClient

from common.logging_utils import format_payload_log, format_stage_log, resolve_sample_debug
from common.mock_llm import get_mock_payload, get_routed_response, is_mock_enabled

_original_build_payload = OpenAIClient._build_structured_payload
_original_structured = OpenAIClient.structured

_sink: Any = print
_role: str = "llm"
_verbose: bool = False


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
    if _verbose:
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
        result = get_routed_response(messages, json_schema)
        _sink(format_stage_log(role=_role, stage="llm-mock", detail="using canned mock LLM response"))
    else:
        result = _original_structured(
            self,
            messages=messages,
            json_schema=json_schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    if _verbose:
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


def install_llm_logger(role: str, *, verbose: bool | None = None) -> None:
    """Patch OpenAIClient methods to log request/response payloads with the given role label.

    Args:
        role: role label prefixed to every log line (``client`` / ``server``).
        verbose: print the full LLM request/response payloads; ``None`` resolves it from
            ``A2AT_SAMPLE_DEBUG``.
    """
    global _role, _verbose
    _role = role
    _verbose = resolve_sample_debug() if verbose is None else verbose
    OpenAIClient._build_structured_payload = _patched_build_payload
    OpenAIClient.structured = _patched_structured
