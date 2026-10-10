"""OpenTelemetry compatibility layer: guarded imports, NoOp fallback, master switch.

Mirrors the degradation pattern of a2a-python's a2a.utils.telemetry: OTel is an
optional dependency; when missing or the master switch is off, every helper
returns a NoOp object that absorbs all calls.
"""

from __future__ import annotations

import contextlib
import logging
import os
import random
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger("a2at.observability")

ENABLED_ENV_VAR = "OTEL_INSTRUMENTATION_A2AT_SDK_ENABLED"
INSTRUMENTING_MODULE_NAME = "a2at-observability"
INSTRUMENTING_MODULE_VERSION = "1.0.0"

# mypy strict disables implicit re-export; declare the OTel handles this compat layer
# forwards to downstream observability modules (None placeholders when OTel is absent).
__all__ = [
    "ENABLED_ENV_VAR",
    "INSTRUMENTING_MODULE_NAME",
    "INSTRUMENTING_MODULE_VERSION",
    "INVALID_SPAN_CONTEXT",
    "NonRecordingSpan",
    "SpanContext",
    "SpanKind",
    "StatusCode",
    "same_trace_orphan_context",
    "format_span_id",
    "format_trace_id",
    "get_current_span",
    "get_meter",
    "get_tracer",
    "is_enabled",
    "is_log_enabled",
    "is_metric_enabled",
    "is_trace_enabled",
    "otel_context",
    "otel_installed",
    "otel_metrics",
    "otel_trace",
    "use_span",
    "use_span_leakproof",
]

otel_installed = False
try:
    from opentelemetry import context as otel_context
    from opentelemetry import metrics as otel_metrics
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace import (
        INVALID_SPAN_CONTEXT,
        NonRecordingSpan,
        SpanContext,
        SpanKind,
        StatusCode,
        format_span_id,
        format_trace_id,
    )

    otel_installed = True
except ImportError:
    logger.debug(
        "OpenTelemetry not found. Tracing will be disabled. "
        "Install with: pip install 'a2a-t-sdk[observability]'"
    )

_ENABLED = os.getenv(ENABLED_ENV_VAR, "true").strip().lower() == "true"

if otel_installed and not _ENABLED:
    logger.debug("A2AT OTEL instrumentation disabled via %s.", ENABLED_ENV_VAR)


def _to_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Per-signal toggles, read once at import: the master switch (_ENABLED) gates
# everything; each signal stays independently controllable while it is on.
_TRACE_ENABLED = _to_bool(os.getenv("A2AT_TRACE_ENABLED", "true"))
_METRIC_ENABLED = _to_bool(os.getenv("A2AT_METRIC_ENABLED", "true"))
_LOG_ENABLED = _to_bool(os.getenv("A2AT_LOG_ENABLED", "true"))


class _NoOpSpan:
    def set_attribute(self, key: str, value: object) -> None: ...

    def set_attributes(self, attributes: object) -> None: ...

    def set_status(self, status: object, description: str | None = None) -> None: ...

    def record_exception(self, exception: BaseException) -> None: ...

    def end(self) -> None: ...

    def add_link(self, context: object, attributes: object = None) -> None: ...

    def get_span_context(self) -> None:
        return None


class _NoOpSpanContext:
    def __enter__(self) -> _NoOpSpan:
        return _NoOpSpan()

    def __exit__(self, *args: object) -> None: ...


class _NoOpTracer:
    def start_as_current_span(self, name: str, **kwargs: object) -> _NoOpSpanContext:
        return _NoOpSpanContext()

    def start_span(self, name: str, **kwargs: object) -> _NoOpSpan:
        return _NoOpSpan()


class _NoOpMetric:
    def record(self, value: object, attributes: object = None) -> None: ...

    def add(self, value: object, attributes: object = None) -> None: ...


class _NoOpMeter:
    def create_histogram(self, name: str, **kwargs: object) -> _NoOpMetric:
        return _NoOpMetric()

    def create_counter(self, name: str, **kwargs: object) -> _NoOpMetric:
        return _NoOpMetric()


_NOOP_TRACER = _NoOpTracer()
_NOOP_METER = _NoOpMeter()
_NOOP_SPAN = _NoOpSpan()


def is_enabled() -> bool:
    return otel_installed and _ENABLED


def is_trace_enabled() -> bool:
    return otel_installed and _ENABLED and _TRACE_ENABLED


def is_metric_enabled() -> bool:
    return otel_installed and _ENABLED and _METRIC_ENABLED


def is_log_enabled() -> bool:
    return otel_installed and _ENABLED and _LOG_ENABLED


def get_tracer() -> object:
    if is_enabled():
        return otel_trace.get_tracer(INSTRUMENTING_MODULE_NAME, INSTRUMENTING_MODULE_VERSION)
    return _NOOP_TRACER


def get_meter() -> object:
    if is_enabled():
        return otel_metrics.get_meter(INSTRUMENTING_MODULE_NAME, INSTRUMENTING_MODULE_VERSION)
    return _NOOP_METER


def get_current_span() -> object:
    if otel_installed:
        return otel_trace.get_current_span()
    return _NOOP_SPAN


def use_span(span: object, *, end_on_exit: bool = False) -> object:
    """Safe wrapper for opentelemetry.trace.use_span; NoOp when disabled."""
    if not is_enabled():
        return contextlib.nullcontext()
    from opentelemetry.trace import use_span as _otel_use_span

    return _otel_use_span(span, end_on_exit=end_on_exit)  # type: ignore[arg-type]


@contextlib.contextmanager
def use_span_leakproof(span: Any) -> Iterator[None]:
    """Attach ``span`` as the current span with a leak-proof restore.

    a2a-sdk consumes SSE streams across async contexts: the async generator
    that activated the span is resumed/closed from a different execution
    context, where OTel's ``detach`` fails ("Token was created in a different
    Context") and swallows the error internally (a log line only) - the span
    stays attached to the ambient context and subsequent unrelated work
    chains into the leaked span's trace. Snapshot the ambient context before
    attaching; after ``detach``, verify the span is actually no longer
    current and force-restore the snapshot otherwise (covers both the raising
    and the silently-swallowed failure modes).
    """
    if span is None or not is_enabled():
        yield
        return
    try:
        previous = otel_context.get_current()
        token = otel_context.attach(otel_trace.set_span_in_context(span))
    except Exception:  # noqa: BLE001 - activation failure must not break the flow
        logger.warning("a2at: span activation failed", exc_info=True)
        yield
        return
    try:
        yield
    finally:
        try:
            otel_context.detach(token)
        except Exception:  # noqa: BLE001 - restore explicitly
            logger.warning("a2at: span context detach failed; restoring ambient snapshot")
            otel_context.attach(previous)
        else:
            if get_current_span() is span:
                # detach silently no-oped (OTel swallows cross-Context token
                # errors internally): the span is still attached - repair.
                otel_context.attach(previous)


def same_trace_orphan_context(span_context: Any) -> Any | None:
    """Build a context that starts the NEXT span in the SAME trace as
    ``span_context`` but WITHOUT a parent-child relation (random parent span
    id): the new span renders at trace top level, shares the trace id, and
    closes independently of the entry span. Pair it with an explicit LINK to
    the entry span for the association - long-running async tasks must be able
    to close and report their entry span without waiting for event spans.
    Returns None (caller falls back to the ambient) when OTel is absent or the
    span context is invalid."""
    if not otel_installed or span_context is None:
        return None
    if not getattr(span_context, "is_valid", False):
        return None
    from opentelemetry.trace import NonRecordingSpan, set_span_in_context

    orphan = SpanContext(
        trace_id=span_context.trace_id,
        # 0 is not a valid W3C span id - guard the astronomically unlikely draw.
        span_id=random.getrandbits(64) or 1,
        is_remote=span_context.is_remote,
        trace_flags=span_context.trace_flags,
        trace_state=span_context.trace_state,
    )
    return set_span_in_context(NonRecordingSpan(orphan))


if not otel_installed:  # pragma: no cover - depends on environment
    INVALID_SPAN_CONTEXT = None  # type: ignore[assignment]
    NonRecordingSpan = None  # type: ignore[assignment,misc]
    SpanContext = None  # type: ignore[assignment,misc]
    SpanKind = None  # type: ignore[assignment,misc]
    StatusCode = None  # type: ignore[assignment,misc]
    format_trace_id = None  # type: ignore[assignment]
    format_span_id = None  # type: ignore[assignment]
    otel_context = None  # type: ignore[assignment]

