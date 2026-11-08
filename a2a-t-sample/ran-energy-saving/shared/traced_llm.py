"""Traced SDK ``LLMClient`` providers of the RAN energy-saving sample (Run Explorer design §5.1).

The A2A-T SDK resolves its provider through ``LLMClientFactory`` and calls ``structured`` for every
LLM-assisted step (Task-T extraction, validations, Negotiation-T extraction). This module provides:

* :class:`TracedOpenAIClient` — live mode, captures the raw OpenAI response before the JSON
  validation of :class:`~a2a_t.llm.providers.openai.OpenAIClient`;
* :class:`TracedStructuredMixin` — the span wrapper a provider (including the demo's offline
  client) subclasses.

The mixin emits one ``llm`` span per call (request, raw content, parsed JSON, usage, latency,
error) and **never changes behavior**: the wrapped response and every exception are returned/raised
unchanged, and a failing trace is swallowed.

The stage of a call comes from :func:`~shared.trace.trace_stage` or the response metadata. Offline
calls report ``n/a (scripted)`` tokens instead of fabricating a zero usage.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Final

from a2a_t.llm.factory import LLMClientFactory
from a2a_t.llm.models import LLMResponse
from a2a_t.llm.providers.openai import OpenAIClient

from shared.json_text import first_json_object_text
from shared.trace import (
    current_trace,
    elapsed_ms,
    emit_activity,
    phase_for_stage,
    producer_span_id,
    record_span,
)

#: Provider name of the traced OpenAI client (live mode).
TRACED_OPENAI_PROVIDER: Final[str] = "openai_traced"

#: Provider name of the traced scripted client (offline mode).
TRACED_SCRIPTED_PROVIDER: Final[str] = "scripted_traced"

#: Label reported instead of a fabricated token count in offline mode.
SCRIPTED_TOKENS_LABEL: Final[str] = "n/a (scripted)"

#: Per-call raw-response capture of :class:`TracedOpenAIClient` (no state on the client instance).
_RAW_RESPONSE: ContextVar[dict[str, object] | None] = ContextVar("a2at_sample_llm_raw", default=None)


def sdk_provider_name(*, offline: bool) -> str:
    """Return the SDK provider name the sample writes into its temporary ``.env``."""
    return TRACED_SCRIPTED_PROVIDER if offline else TRACED_OPENAI_PROVIDER


def register_traced_providers() -> None:
    """Register the traced OpenAI provider with the SDK factory (idempotent).

    The offline traced provider belongs to the demo package and is registered by
    :func:`demo.composition.install_offline_provider`; this module never imports it.
    """
    available = set(LLMClientFactory.available_providers())
    if TRACED_OPENAI_PROVIDER not in available:
        LLMClientFactory.register(TRACED_OPENAI_PROVIDER, TracedOpenAIClient)


def is_traced_provider_registered(provider: str) -> bool:
    """Return whether one traced provider name is registered."""
    return provider in LLMClientFactory.available_providers()


def _now_iso() -> str:
    """Return the current UTC time in ISO 8601 form."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _metadata_stage(response: LLMResponse | None) -> str | None:
    """Return the ``scripted_stage`` of one scripted response, ``None`` otherwise."""
    if response is None:
        return None
    value = response.metadata.get("scripted_stage")
    return value if isinstance(value, str) and value else None


def _infer_stage(messages: list[dict[str, str]], json_schema: dict[str, Any]) -> str | None:
    """Return ``None``: producers set their stage through ``trace_stage`` or response metadata."""
    del messages, json_schema
    return None


def _parse_json(content: str | None) -> tuple[object | None, str | None]:
    """Return the parsed JSON object of one raw content and its parse error, if any.

    A model that wrapped the object in a fence is not a parse failure for the call: the traced
    provider accepts it, so the display parses the same embedded object instead of flagging an
    anomaly the run never hit.
    """
    if not content:
        return None, None
    try:
        return json.loads(content), None
    except (TypeError, ValueError):
        pass
    try:
        return json.loads(first_json_object_text(content)), None
    except (TypeError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _token_map(response: LLMResponse) -> dict[str, object]:
    """Render the token usage of one response; the scripted provider reports ``n/a``."""
    if response.model == TRACED_SCRIPTED_PROVIDER or "scripted_stage" in response.metadata:
        return {
            "prompt": None,
            "completion": None,
            "total": None,
            "cached": None,
            "estimated": True,
            "scripted": True,
            "label": SCRIPTED_TOKENS_LABEL,
        }
    usage = response.usage or {}
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    if total is None and isinstance(prompt, int) and isinstance(completion, int):
        total = prompt + completion
    return {
        "prompt": prompt,
        "completion": completion,
        "total": total,
        "cached": _cached_tokens(usage),
        "estimated": False,
    }


def _cached_tokens(usage: Mapping[str, object]) -> int | None:
    """Return the cached prefix tokens of one OpenAI-compatible usage, ``None`` when absent.

    The field is optional: the SDK normalizes it to ``cached``, cloud providers report
    ``prompt_tokens_details.cached_tokens``, some gateways use ``prompt_cache_hit_tokens``
    (DeepSeek native), others omit it entirely. ``None`` (not ``0``) marks "the provider did not
    report it" so averages skip it.
    """
    normalized = usage.get("cached")
    if isinstance(normalized, int) and not isinstance(normalized, bool):
        return normalized
    details = usage.get("prompt_tokens_details")
    if isinstance(details, Mapping):
        cached = details.get("cached_tokens")
        if isinstance(cached, int) and not isinstance(cached, bool):
            return cached
    hit = usage.get("prompt_cache_hit_tokens")
    if isinstance(hit, int) and not isinstance(hit, bool):
        return hit
    return None


def _llm_span_prefix(provider_label: str) -> str:
    """Return the producer id namespace of one SDK LLM span (role-scoped, cross-process safe)."""
    return f"llm_{current_trace().role or 'client'}_{provider_label}"


def _request_detail(
    messages: list[dict[str, str]],
    json_schema: dict[str, Any],
    temperature: float | None,
    max_tokens: int | None,
) -> dict[str, object]:
    """Render the request side of one structured call."""
    return {
        "messages": [dict(message) for message in messages],
        "jsonSchema": json_schema,
        "temperature": temperature,
        "maxTokens": max_tokens,
        "messageCount": len(messages),
    }


class TracedStructuredMixin:
    """``structured`` wrapper shared by the traced providers.

    Subclasses implement :meth:`_call_structured` by delegating to their provider implementation
    explicitly (no ``super()`` chain through this mixin) and declare :attr:`_provider_label`.
    """

    _provider_label: str = ""

    def _call_structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None,
        max_tokens: int | None,
    ) -> LLMResponse:
        """Call the wrapped provider implementation (implemented by the subclasses)."""
        raise NotImplementedError

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Call the wrapped provider and emit one ``llm`` span; behavior is never changed."""
        started = time.perf_counter()
        started_at = _now_iso()
        capture: dict[str, object] = {}
        token = _RAW_RESPONSE.set(capture)
        # The llm span below is written only when the call returns; publish the wait first so a
        # slow provider (the yellow zone sees 80-260 s) still shows progress on the page.
        activity_id = producer_span_id("activity")
        stage_hint = current_trace().stage
        _emit_activity(activity_id, stage=stage_hint, status="running")
        try:
            response = self._call_structured(
                messages=messages,
                json_schema=json_schema,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            _RAW_RESPONSE.reset(token)
            _emit_activity(activity_id, stage=stage_hint, status="error")
            stage = current_trace().stage or _infer_stage(messages, json_schema)
            record_span(
                kind="llm",
                phase=phase_for_stage(stage),
                title=f"LLM call · {stage or 'structured'}",
                purpose=f"Structured LLM call of stage {stage or 'unknown'}",
                prefix=_llm_span_prefix(self._provider_label),
                stage=stage,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                detail={
                    "provider": self._provider_label,
                    "request": _request_detail(messages, json_schema, temperature, max_tokens),
                    "response": dict(capture) or None,
                },
                started_at=started_at,
                ended_at=_now_iso(),
                duration_ms=elapsed_ms(started),
            )
            raise
        _RAW_RESPONSE.reset(token)

        stage = current_trace().stage or _metadata_stage(response) or _infer_stage(messages, json_schema)
        raw_content = capture.get("rawContent")
        if not isinstance(raw_content, str):
            raw_content = response.content
        parsed, parse_error = _parse_json(raw_content)
        response_detail: dict[str, object] = {
            "rawContent": raw_content,
            "parsed": parsed,
            "parseError": parse_error,
            "model": response.model,
            "usage": dict(response.usage or {}),
        }
        if capture.get("responseId") is not None:
            response_detail["responseId"] = capture["responseId"]
        if capture.get("finishReason") is not None:
            response_detail["finishReason"] = capture["finishReason"]
        record_span(
            kind="llm",
            phase=phase_for_stage(stage),
            title=f"LLM call · {stage or 'structured'}",
            purpose=f"Structured LLM call of stage {stage or 'unknown'}",
            prefix=_llm_span_prefix(self._provider_label),
            stage=stage,
            status="ok",
            model=response.model,
            tokens=_token_map(response),
            detail={
                "provider": self._provider_label,
                "request": _request_detail(messages, json_schema, temperature, max_tokens),
                "response": response_detail,
            },
            started_at=started_at,
            ended_at=_now_iso(),
            duration_ms=elapsed_ms(started),
        )
        _emit_activity(activity_id, stage=stage, status="done")
        return response


def _emit_activity(activity_id: str, *, stage: str | None, status: str) -> None:
    """Publish the wait state of one structured call; a failure never changes the LLM result."""
    try:
        emit_activity(
            activity_id=activity_id,
            activity_kind="model",
            status=status,
            label_key="waitingModel",
            stage=stage,
            detail={"producer": "sdk"},
        )
    except Exception:  # noqa: BLE001 - tracing is an observer
        return


class TracedOpenAIClient(TracedStructuredMixin, OpenAIClient):
    """Live ``OpenAIClient`` that captures the raw response before JSON validation.

    The sample disables the OpenAI SDK's own HTTP retries: ``A2AT_LLM_MAX_ATTEMPTS`` is the
    SDK's protocol-level retry budget, not the transport retry count. With the SDK default
    ``max_retries=2``, one ``A2AT_LLM_TIMEOUT_SECONDS`` timeout becomes three attempts and can
    overrun the client's A2A budget. One attempt keeps every budget predictable; transient
    failures surface as a structured protocol message instead of a hidden long retry.
    """

    _provider_label = TRACED_OPENAI_PROVIDER

    def _get_client(self) -> Any:
        """Return the SDK OpenAI client with HTTP retries disabled."""
        client = OpenAIClient._get_client(self)
        client.max_retries = 0
        return client

    def _extract_message_text(self, response: Any) -> str:
        """Return the message text, tolerating a fenced or prose-wrapped JSON object.

        Some OpenAI-compatible gateways ignore ``response_format`` and wrap the object in a
        ```json fence (or a sentence). The SDK validates the returned text with ``json.loads``, so
        handing it the embedded object keeps the call successful; the object must still decode, so
        validation is not weakened.
        """
        return first_json_object_text(OpenAIClient._extract_message_text(self, response))

    def _call_structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None,
        max_tokens: int | None,
    ) -> LLMResponse:
        """Delegate to the SDK OpenAI implementation."""
        return OpenAIClient.structured(
            self,
            messages=messages,
            json_schema=json_schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _parse_response(self, response: Any) -> LLMResponse:
        """Stash the raw response content before the SDK validates its JSON object."""
        capture = _RAW_RESPONSE.get()
        if capture is not None:
            try:
                # Record what the model actually sent (fence included); the normalized text is what
                # the SDK validates and returns as ``LLMResponse.content``.
                capture["rawContent"] = OpenAIClient._extract_message_text(self, response)
            except Exception as exc:  # noqa: BLE001 - capture must never break the call
                capture["rawTextError"] = str(exc)
            response_id = getattr(response, "id", None)
            if response_id is not None:
                capture["responseId"] = str(response_id)
            choices = getattr(response, "choices", None) or []
            if choices:
                finish_reason = getattr(choices[0], "finish_reason", None)
                if finish_reason is not None:
                    capture["finishReason"] = str(finish_reason)
        return OpenAIClient._parse_response(self, response)


__all__ = [
    "SCRIPTED_TOKENS_LABEL",
    "TRACED_OPENAI_PROVIDER",
    "TRACED_SCRIPTED_PROVIDER",
    "TracedOpenAIClient",
    "TracedStructuredMixin",
    "is_traced_provider_registered",
    "register_traced_providers",
    "sdk_provider_name",
]
