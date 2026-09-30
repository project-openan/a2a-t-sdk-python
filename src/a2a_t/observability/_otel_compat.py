"""OpenTelemetry compatibility layer: guarded imports, NoOp fallback, master switch.

Mirrors the degradation pattern of a2a-python's a2a.utils.telemetry: OTel is an
optional dependency; when missing or the master switch is off, every helper
returns a NoOp object that absorbs all calls.
"""

from __future__ import annotations

import contextlib
import logging
import os

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
    "NonRecordingSpan",
    "SpanKind",
    "StatusCode",
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
]

otel_installed = False
try:
    from opentelemetry import context as otel_context
    from opentelemetry import metrics as otel_metrics
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace import (
        NonRecordingSpan,
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


if not otel_installed:  # pragma: no cover - depends on environment
    NonRecordingSpan = None  # type: ignore[assignment,misc]
    SpanKind = None  # type: ignore[assignment,misc]
    StatusCode = None  # type: ignore[assignment,misc]
    format_trace_id = None  # type: ignore[assignment]
    format_span_id = None  # type: ignore[assignment]
    otel_context = None  # type: ignore[assignment]
