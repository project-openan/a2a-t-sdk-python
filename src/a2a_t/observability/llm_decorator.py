"""A2ATLLMClientDecorator: SDK LLM client wrapper (spec 2.2/4.2/5.5, a2a-java parity).

``A2ATClient``/``A2ATServer`` wrap their internally created LLM client with this
decorator at construction. After every ``structured`` call the decorator:

- stashes the token usage into the :mod:`~a2a_t.observability.llm_stash` contextvar
  bridge — the client transport decorator reads (and clears) the stash when creating
  the next entry span so the tokens land on ``gen_ai.usage.input_tokens`` /
  ``gen_ai.usage.output_tokens`` / ``gen_ai.request.model`` (spec 4.2);
- records the L2 metric ``gen_ai.client.token.usage`` histogram immediately, one
  sample per token type (``gen_ai.token.type=input`` / ``output``, unit ``{token}``)
  (spec 5.5).

Everything is guarded: observability failures are swallowed with a WARNING on
logger ``a2at.observability`` and never break the LLM call result (spec 8.2).
"""

from __future__ import annotations

import logging
from typing import Any

from a2a_t.observability import _otel_compat
from a2a_t.observability.attributes import ATTR_GEN_AI_TOKEN_TYPE
from a2a_t.observability.llm_stash import LLMUsageStash, set_llm_usage

logger = logging.getLogger("a2at.observability")

_METRIC_TOKEN_USAGE = "gen_ai.client.token.usage"
_METRIC_INSTRUMENTS: dict[str, Any] = {}


def _metric_histogram(name: str, unit: str, description: str) -> Any | None:
    """Lazily create and cache one histogram per metric name (shared v2 pattern)."""
    instrument = _METRIC_INSTRUMENTS.get(name)
    if instrument is None:
        meter: Any = _otel_compat.get_meter()
        instrument = meter.create_histogram(name, unit=unit, description=description)
        _METRIC_INSTRUMENTS[name] = instrument
    return instrument


def _usage_int(usage: Any, key: str) -> int | None:
    """Read one token count from a dict- or object-shaped usage payload; None when absent."""
    try:
        if isinstance(usage, dict):
            value = usage.get(key)
        else:
            value = getattr(usage, key, None)
    except Exception:  # noqa: BLE001 - structural access must never raise
        return None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class A2ATLLMClientDecorator:
    """Structural LLMClient wrapper: usage stash + L2 token metric (spec 2.2)."""

    _inner: Any

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") or name == "_inner":
            raise AttributeError(name)
        inner = self.__dict__.get("_inner")
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        response = self._inner.structured(
            messages=messages,
            json_schema=json_schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self._report_usage(response)
        return response

    def _report_usage(self, response: Any) -> None:
        """Stash + metric; never raises (spec 8.2: observability never breaks the flow)."""
        try:
            usage = getattr(response, "usage", None)
            input_tokens = _usage_int(usage, "prompt_tokens")
            output_tokens = _usage_int(usage, "completion_tokens")
            set_llm_usage(
                LLMUsageStash(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    request_model=getattr(response, "model", None),
                )
            )
            self._record_token_metric(input_tokens, output_tokens)
        except Exception:  # noqa: BLE001
            logger.warning("a2at: failed to report llm token usage", exc_info=True)

    def _record_token_metric(self, input_tokens: int | None, output_tokens: int | None) -> None:
        if not _otel_compat.is_metric_enabled():
            return
        histogram = _metric_histogram(_METRIC_TOKEN_USAGE, "{token}", "GenAI client token usage")
        if histogram is None:
            return
        if input_tokens is not None:
            histogram.record(input_tokens, attributes={ATTR_GEN_AI_TOKEN_TYPE: "input"})
        if output_tokens is not None:
            histogram.record(output_tokens, attributes={ATTR_GEN_AI_TOKEN_TYPE: "output"})
