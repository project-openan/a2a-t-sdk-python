"""OTel auto-configuration: TracerProvider + MeterProvider + LoggerProvider + Exporters + Resource.

Zero-code setup for all three signals (Trace/Metric/Log). Console exporter fallback
when no OTLP endpoint is configured. Per-signal toggles respected. Existing user
providers are never overridden.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from a2a_t.observability import _otel_compat
from a2a_t.observability.config import A2ATObservabilityConfig

logger = logging.getLogger("a2at.observability")

_otel_configured = False
_OTLP_LOG_HANDLER_ATTACHED = False
_SDK_AVAILABLE: bool | None = None


def _sdk_available() -> bool:
    """Check (once) whether opentelemetry-sdk is importable; cache the result."""
    global _SDK_AVAILABLE
    if _SDK_AVAILABLE is None:
        try:
            import opentelemetry.sdk  # noqa: F401
            _SDK_AVAILABLE = True
        except ImportError:
            logger.warning("opentelemetry-sdk not installed; OTel auto-config skipped")
            _SDK_AVAILABLE = False
    return _SDK_AVAILABLE


def is_otel_configured() -> bool:
    return _otel_configured


def _normalize_protocol(raw: str | None) -> str:
    """Lowercase, strip and alias-normalize the OTLP protocol; invalid → grpc (warned).

    Shared by the env path (``get_protocol``) and the config path
    (``_do_setup``): ``http`` is an alias for ``http/protobuf``; case and
    surrounding whitespace are insignificant; anything else falls back to
    ``grpc`` with a warning instead of silently dialing gRPC against an HTTP
    port.
    """
    value = (raw or "grpc").strip().lower()
    if value in ("grpc", "http/protobuf", "http"):
        return "http/protobuf" if value.startswith("http") else "grpc"
    logger.warning("Invalid A2AT exporter protocol %r; falling back to grpc", value)
    return "grpc"


def get_protocol() -> str:
    """Return the OTLP protocol ('grpc' or 'http/protobuf'); invalid values fall back to grpc."""
    return _normalize_protocol(os.getenv("A2AT_EXPORTER_PROTOCOL", "grpc"))


def ensure_otel_configured(config: A2ATObservabilityConfig | None = None) -> None:
    """Idempotent trigger: called by decorators on first use.

    When ``config`` is provided, its ``endpoint`` / ``protocol`` / ``service_name``
    fields are honored (they already carry the env fallbacks resolved at config
    construction, so an unset field keeps behaving like the env-only path);
    ``None`` keeps the pure env behavior.
    """
    if _otel_configured or not _otel_compat.is_enabled():
        return
    if config is None:
        _do_setup()
        return
    _do_setup(endpoint=config.endpoint, protocol=config.protocol, service_name=config.service_name)


def setup(endpoint: str | None = None, service_name: str | None = None) -> None:
    """Explicit setup entry point; parameters override env vars."""
    global _otel_configured
    if _otel_configured:
        return
    _do_setup(endpoint=endpoint, service_name=service_name)


def _do_setup(
    endpoint: str | None = None, service_name: str | None = None, protocol: str | None = None
) -> None:
    global _otel_configured
    if not _otel_compat.otel_installed:
        logger.warning("OpenTelemetry not installed; auto-config skipped")
        return
    if not _sdk_available():
        return
    from opentelemetry import trace as trace_api
    from opentelemetry.sdk.trace import TracerProvider

    # Step 1: respect existing provider. ProxyTracerProvider lives at the API level
    # (opentelemetry.trace) in OTel 1.44.0; sdk.trace.export has no such class.
    if not isinstance(trace_api.get_tracer_provider(), trace_api.ProxyTracerProvider):
        logger.debug("User already configured OTel; skipping auto-config")
        return

    try:
        resolved_endpoint = endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        resolved_name = (
            service_name or os.getenv("OTEL_SERVICE_NAME") or os.getenv("A2AT_SERVICE_NAME") or "a2a-t-agent"
        )
        resolved_protocol = _normalize_protocol(protocol)
        resource = _build_resource(resolved_name)
        span_exporter, metric_exporter, log_exporter = _build_exporters(resolved_endpoint, resolved_protocol)

        # Step 3: Providers (per-signal)
        if span_exporter is not None and _otel_compat.is_trace_enabled():
            tp = TracerProvider(resource=resource)
            tp.add_span_processor(_build_span_processor(span_exporter))
            trace_api.set_tracer_provider(tp)

        if metric_exporter is not None and _otel_compat.is_metric_enabled():
            _setup_meter_provider(resource, metric_exporter)

        if log_exporter is not None and _otel_compat.is_log_enabled():
            _setup_logger_provider(resource, log_exporter)

        _otel_configured = True
        if resolved_endpoint is None and span_exporter is not None:
            logger.warning("No OTLP endpoint configured; using Console exporters (dev mode)")
    except Exception:
        logger.warning("OTel auto-config failed", exc_info=True)


def _build_resource(service_name: str) -> Any:
    from opentelemetry.sdk.resources import Resource

    from a2a_t import __version__

    attributes: dict[str, str] = {
        "service.name": service_name,
        "service.version": __version__,
    }
    # agent.role: env opt-in only (no default); decorators do not set it implicitly.
    agent_role = os.getenv("A2AT_AGENT_ROLE")
    if agent_role:
        attributes["agent.role"] = agent_role
    return Resource.create(attributes)


def _build_console_exporters() -> tuple[Any, Any, Any]:
    from opentelemetry.sdk._logs.export import ConsoleLogExporter
    from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    return ConsoleSpanExporter(), ConsoleMetricExporter(), ConsoleLogExporter()


def _build_http_exporters(endpoint: str) -> tuple[Any, Any, Any]:
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    base = endpoint.rstrip("/")
    return (
        OTLPSpanExporter(endpoint=f"{base}/v1/traces"),
        OTLPMetricExporter(endpoint=f"{base}/v1/metrics"),
        OTLPLogExporter(endpoint=f"{base}/v1/logs"),
    )


def _build_grpc_exporters(endpoint: str) -> tuple[Any, Any, Any]:
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    return (
        OTLPSpanExporter(endpoint=endpoint),
        OTLPMetricExporter(endpoint=endpoint),
        OTLPLogExporter(endpoint=endpoint),
    )


def _console_exporter_enabled() -> bool:
    """``A2AT_CONSOLE_EXPORTER`` (default true): the dev-mode Console fallback can
    be turned off for production processes where stdout spam is undesirable."""
    return os.getenv("A2AT_CONSOLE_EXPORTER", "true").strip().lower() not in {"0", "false", "no", "off"}


def _build_exporters(endpoint: str | None, protocol: str) -> tuple[Any, Any, Any]:
    if endpoint is None:
        if not _console_exporter_enabled():
            logger.info(
                "A2AT_CONSOLE_EXPORTER=false and no OTLP endpoint configured: "
                "signals stay NoOp (no exporters installed)"
            )
            return None, None, None
        return _build_console_exporters()

    try:
        if protocol == "http/protobuf":
            return _build_http_exporters(endpoint)
        return _build_grpc_exporters(endpoint)
    except ImportError:
        if not _console_exporter_enabled():
            logger.warning(
                "OTLP %s exporter package unavailable and A2AT_CONSOLE_EXPORTER=false: signals stay NoOp",
                protocol,
            )
            return None, None, None
        logger.warning(
            "OTLP %s exporter package unavailable; falling back to Console exporters (dev mode)",
            protocol,
            exc_info=True,
        )
        return _build_console_exporters()


def _build_span_processor(exporter: Any) -> Any:
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    return BatchSpanProcessor(exporter)


def _setup_meter_provider(resource: Any, exporter: Any) -> None:
    from opentelemetry import metrics as metrics_api
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

    reader = PeriodicExportingMetricReader(exporter)
    metrics_api.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))


def _setup_logger_provider(resource: Any, exporter: Any) -> None:
    global _OTLP_LOG_HANDLER_ATTACHED
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

    lp = LoggerProvider(resource=resource)
    lp.add_log_record_processor(BatchLogRecordProcessor(exporter))
    set_logger_provider(lp)

    if not _OTLP_LOG_HANDLER_ATTACHED:
        handler = LoggingHandler(level=logging.DEBUG, logger_provider=lp)
        logging.getLogger("a2at.observability").addHandler(handler)
        _OTLP_LOG_HANDLER_ATTACHED = True
