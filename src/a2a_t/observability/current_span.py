"""current_span(): get the active A2A-T span in agent execution code."""

from __future__ import annotations

from typing import Any

from a2a_t.observability import _otel_compat


class A2ATCurrentSpan:
    """Read/append view of the current span (not a span factory; cannot end)."""

    def __init__(self, span: Any) -> None:
        self._span = span

    def set_attribute(self, key: str, value: str | int | bool) -> None:
        try:
            self._span.set_attribute(key, value)
        except Exception:
            pass

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


def current_span() -> A2ATCurrentSpan | None:
    """Get the active A2A-T span; None when no valid span is active."""
    if not _otel_compat.is_trace_enabled():
        return None
    span: Any = _otel_compat.get_current_span()
    ctx = span.get_span_context() if span is not None else None
    if ctx is None or not getattr(ctx, "is_valid", False):
        return None
    return A2ATCurrentSpan(span)
