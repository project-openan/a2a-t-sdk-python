"""current_span(): get the active A2A-T span in agent execution code."""

from __future__ import annotations

import logging
from typing import Any

from a2a_t.observability import _otel_compat

logger = logging.getLogger("a2at.observability")


class A2ATCurrentSpan:
    """Read/append view of the current span (not a span factory; cannot end)."""

    def __init__(self, span: Any) -> None:
        self._span = span

    def set_attribute(self, key: str, value: str | int | bool) -> None:
        try:
            self._span.set_attribute(key, value)
        except Exception:
            logger.debug("a2at: failed to set attribute on current span", exc_info=True)

    @property
    def trace_id(self) -> str | None:
        ctx = self._span.get_span_context()
        if ctx is None or not getattr(ctx, "is_valid", False):
            return None
        return _otel_compat.format_trace_id(ctx.trace_id)

    @property
    def span_id(self) -> str | None:
        ctx = self._span.get_span_context()
        if ctx is None or not getattr(ctx, "is_valid", False):
            return None
        return _otel_compat.format_span_id(ctx.span_id)

    @property
    def is_valid(self) -> bool:
        ctx = self._span.get_span_context()
        return bool(ctx is not None and getattr(ctx, "is_valid", False))


def _span_is_recording(span: Any) -> bool:
    """``is_recording`` is a method on current OTel spans (a property on some
    versions) - tolerate both shapes; treat unreadable as recording."""
    flag = getattr(span, "is_recording", None)
    if callable(flag):
        try:
            return bool(flag())
        except Exception:  # noqa: BLE001
            return True
    if flag is None:
        return True
    return bool(flag)


def current_span() -> A2ATCurrentSpan | None:
    """Get the active A2A-T span; None when no valid recording span is active.

    Leak defense (2026-09-30): a2a-sdk consumes SSE streams across async
    contexts where OTel's detach fails silently, leaving the PREVIOUS request's
    ended entry span attached as current. An ended span is not an active span -
    return None instead of a stale wrapper (whose ``set_attribute`` would be a
    silent no-op and whose ``trace_id`` would misattribute logs/attributes).
    """
    if not _otel_compat.is_trace_enabled():
        return None
    span: Any = _otel_compat.get_current_span()
    ctx = span.get_span_context() if span is not None else None
    if ctx is None or not getattr(ctx, "is_valid", False):
        return None
    if not _span_is_recording(span):
        return None
    return A2ATCurrentSpan(span)
