"""W3C TraceContext propagation helpers built on the opentelemetry-api default propagator."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

from a2a_t.observability import _otel_compat

TRACEPARENT_HEADER = "traceparent"


def inject_traceparent(headers: MutableMapping[str, str], *, context: Any | None = None) -> None:
    """Inject traceparent of the current (or given) context into headers; NoOp when disabled."""
    if not _otel_compat.is_enabled():
        return
    from opentelemetry.propagate import inject

    try:
        if context is None:
            inject(headers)
        else:
            inject(headers, context=context)
    except Exception:  # noqa: BLE001
        pass


def extract_trace_context(headers: Mapping[str, str]) -> Any | None:
    """Extract an opaque OTel Context from headers (case-insensitive); None when absent."""
    if not _otel_compat.is_enabled():
        return None
    from opentelemetry.propagate import extract

    try:
        lowered = {str(k).lower(): str(v) for k, v in headers.items()}
        context = extract(carrier=lowered)
    except Exception:  # noqa: BLE001
        return None
    span = _otel_compat.otel_trace.get_current_span(context)
    span_context = span.get_span_context()
    if span_context is None or not getattr(span_context, "is_valid", False):
        return None
    return context
