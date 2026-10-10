from __future__ import annotations

import importlib
import logging
from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider


@pytest.fixture(autouse=True)
def _reset_otel_globals() -> Iterator[None]:
    """Reset OTel globals before and after each test (mirrors conftest)."""
    import opentelemetry.metrics as metrics_api
    import opentelemetry.trace as trace_api
    from opentelemetry.metrics._internal import (
        _METER_PROVIDER_SET_ONCE,
    )

    trace_api._TRACER_PROVIDER = None
    metrics_api._METER_PROVIDER = None
    metrics_api._METER_PROVIDER_SET_ONCE = None  # type: ignore[assignment]
    for lock in (getattr(trace_api, "_TRACER_PROVIDER_SET_ONCE", None), _METER_PROVIDER_SET_ONCE):
        if lock is not None and hasattr(lock, "_set"):
            lock._set = False  # type: ignore[attr-defined]
        if lock is not None and hasattr(lock, "_done"):
            lock._done = False  # type: ignore[attr-defined]
    # v3: the package attr "setup" is the exported setup() function (spec §5.1),
    # shadowing the submodule name — importlib.import_module always yields the module.
    setup_module = importlib.import_module("a2a_t.observability.setup")

    setup_module._otel_configured = False
    yield
    setup_module._otel_configured = False


def test_setup_creates_providers_with_console(monkeypatch) -> None:
    import io

    from opentelemetry.sdk._logs.export import ConsoleLogExporter
    from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    from a2a_t.observability.setup import is_otel_configured, setup

    # Console exporters write to an in-memory sink instead of stdout: a REAL
    # stdout-bound console provider flushes at interpreter shutdown when pytest's
    # stdout is already closed ("ValueError: I/O operation on closed file" noise).
    sink = io.StringIO()
    setup_module = importlib.import_module("a2a_t.observability.setup")
    monkeypatch.setattr(
        setup_module,
        "_build_console_exporters",
        lambda: (ConsoleSpanExporter(out=sink), ConsoleMetricExporter(out=sink), ConsoleLogExporter(out=sink)),
    )

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    setup()
    assert is_otel_configured() is True
    # Provider is set (not proxy)
    from opentelemetry.sdk.trace import TracerProvider as SDKProvider

    assert isinstance(trace.get_tracer_provider(), SDKProvider)


def test_setup_console_exporter_opt_out(monkeypatch) -> None:
    """A2AT_CONSOLE_EXPORTER=false + no endpoint → signals stay NoOp (no stdout
    console provider), setup still marks configured to avoid repeated attempts."""
    from a2a_t.observability.setup import is_otel_configured, setup

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("A2AT_CONSOLE_EXPORTER", "false")
    setup()
    assert is_otel_configured() is True
    # Provider NOT replaced: the ProxyTracerProvider remains (NoOp behavior)
    from opentelemetry.trace import ProxyTracerProvider

    assert isinstance(trace.get_tracer_provider(), ProxyTracerProvider)


def test_setup_respects_existing_provider(monkeypatch) -> None:
    from a2a_t.observability.setup import is_otel_configured, setup

    # Pre-set a provider → setup should not override
    trace.set_tracer_provider(TracerProvider())
    setup()
    assert is_otel_configured() is False  # not configured by us


def test_setup_idempotent() -> None:
    from a2a_t.observability.setup import is_otel_configured, setup

    setup()
    assert is_otel_configured() is True
    setup()  # second call: no-op
    assert is_otel_configured() is True


def test_setup_with_otlp_endpoint(monkeypatch) -> None:
    from a2a_t.observability.setup import is_otel_configured, setup

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    setup()
    assert is_otel_configured() is True


def test_setup_per_signal_disabled(monkeypatch) -> None:
    import a2a_t.observability._otel_compat as compat
    from a2a_t.observability.setup import setup

    monkeypatch.setattr(compat, "_ENABLED", True)
    monkeypatch.setattr(compat, "_TRACE_ENABLED", False)
    monkeypatch.setattr(compat, "_METRIC_ENABLED", False)
    monkeypatch.setattr(compat, "_LOG_ENABLED", False)
    setup()  # should not crash even with all signals off


def test_setup_sdk_missing_no_crash(monkeypatch) -> None:
    """api-without-sdk: _do_setup must not raise; warning once; not configured."""
    import a2a_t.observability.setup  # noqa: F401 - ensures submodule is loaded
    from a2a_t.observability.setup import _sdk_available, is_otel_configured, setup

    # import a.b as c prefers the (function) package attribute; import_module has the module.
    setup_module = importlib.import_module("a2a_t.observability.setup")

    # Simulate sdk missing by caching False
    monkeypatch.setattr(setup_module, "_SDK_AVAILABLE", False)
    setup()  # must not raise
    assert is_otel_configured() is False
    assert _sdk_available() is False  # cached


# --------------------------------------------------------------------------------------
# v3 fix wave: A2ATObservabilityConfig exporter/resource wiring
# --------------------------------------------------------------------------------------


def test_ensure_otel_configured_honors_explicit_config(monkeypatch) -> None:
    """A config-provided ensure call forwards endpoint/protocol/service_name to _do_setup."""
    from a2a_t.observability.config import A2ATObservabilityConfig
    from a2a_t.observability.setup import ensure_otel_configured

    # import a.b as c prefers the (function) package attribute; import_module has the module.
    setup_module = importlib.import_module("a2a_t.observability.setup")
    captured = {}
    monkeypatch.setattr(setup_module, "_do_setup", lambda **kwargs: captured.update(kwargs))

    ensure_otel_configured(
        config=A2ATObservabilityConfig(endpoint="http://test:4317", protocol="http/protobuf", service_name="svc")
    )

    assert captured == {"endpoint": "http://test:4317", "protocol": "http/protobuf", "service_name": "svc"}


def test_ensure_otel_configured_without_config_keeps_env_behavior(monkeypatch) -> None:
    """The env-only call path stays unchanged (no kwargs forwarded)."""
    from a2a_t.observability.setup import ensure_otel_configured

    setup_module = importlib.import_module("a2a_t.observability.setup")
    captured = {}
    monkeypatch.setattr(setup_module, "_do_setup", lambda **kwargs: captured.update(kwargs))

    ensure_otel_configured()

    assert captured == {}


def test_ensure_otel_configured_idempotent_with_config(monkeypatch) -> None:
    """Once configured, later ensure calls (with or without config) stay no-ops."""
    from a2a_t.observability.config import A2ATObservabilityConfig
    from a2a_t.observability.setup import ensure_otel_configured

    setup_module = importlib.import_module("a2a_t.observability.setup")
    calls = []
    monkeypatch.setattr(setup_module, "_do_setup", lambda **kwargs: calls.append(kwargs))

    ensure_otel_configured()
    setup_module._otel_configured = True
    ensure_otel_configured(config=A2ATObservabilityConfig(endpoint="http://test:4317"))

    assert len(calls) == 1


async def test_handler_decorator_passes_config_to_setup(monkeypatch) -> None:
    """Decorator construction with explicit config → setup resolves that endpoint (not env)."""
    from a2a_t.observability.config import A2ATObservabilityConfig
    from a2a_t.observability.server.handler_decorator import A2ATRequestHandlerDecorator
    from a2a_t.observability.setup import is_otel_configured
    from tests.observability.stubs import FakeServerCallContext, make_send_request

    # import a.b as c prefers the (function) package attribute; import_module has the module.
    setup_module = importlib.import_module("a2a_t.observability.setup")
    captured = {}

    def spy_build_exporters(endpoint, protocol):
        captured["endpoint"] = endpoint
        captured["protocol"] = protocol
        # Console exporters: the fake endpoint must never actually dial the network.
        return setup_module._build_console_exporters()

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(setup_module, "_build_exporters", spy_build_exporters)

    class Inner:
        async def on_message_send(self, params, context):
            return "ok"

    decorator = A2ATRequestHandlerDecorator(Inner(), config=A2ATObservabilityConfig(endpoint="http://test:4317"))

    result = await decorator.on_message_send(
        make_send_request({}, task_id="T1", context_id="C1"), FakeServerCallContext()
    )

    assert result == "ok"
    assert captured["endpoint"] == "http://test:4317"
    assert captured["protocol"] == "grpc"
    assert is_otel_configured() is True


# --------------------------------------------------------------------------------------
# v3 fix wave: Resource attributes (agent.role + real SDK service.version)
# --------------------------------------------------------------------------------------


def test_resource_uses_sdk_version_and_agent_role(monkeypatch) -> None:
    from a2a_t import __version__

    setup_module = importlib.import_module("a2a_t.observability.setup")

    monkeypatch.setenv("A2AT_AGENT_ROLE", "client")
    attrs = dict(setup_module._build_resource("svc").attributes)

    assert attrs["service.version"] == __version__
    assert attrs["agent.role"] == "client"


def test_resource_omits_agent_role_when_unset(monkeypatch) -> None:
    """No A2AT_AGENT_ROLE env → the attribute is omitted entirely (no default)."""
    setup_module = importlib.import_module("a2a_t.observability.setup")

    monkeypatch.delenv("A2AT_AGENT_ROLE", raising=False)
    attrs = dict(setup_module._build_resource("svc").attributes)

    assert "agent.role" not in attrs


# --------------------------------------------------------------------------------------
# M5: config 路径的协议归一（别名/大小写/非法值不得静默走 gRPC）
# --------------------------------------------------------------------------------------


def test_config_protocol_alias_and_case_normalized(monkeypatch) -> None:
    """M5: A2AT_EXPORTER_PROTOCOL=http（别名）/ HTTP（大写）经 config 路径必须归一为
    http/protobuf——旧行为下 config 值恒非空使 get_protocol() 成为死代码，别名静默
    走 gRPC（gRPC 拨 HTTP 端口只会得到难排查的连接错误）。"""
    from a2a_t.observability.config import A2ATObservabilityConfig

    setup_module = importlib.import_module("a2a_t.observability.setup")
    calls: list[tuple[str, str]] = []

    def fake_http(endpoint: str):
        calls.append(("http", endpoint))
        return None, None, None

    def fake_grpc(endpoint: str):
        calls.append(("grpc", endpoint))
        return None, None, None

    monkeypatch.setattr(setup_module, "_build_http_exporters", fake_http)
    monkeypatch.setattr(setup_module, "_build_grpc_exporters", fake_grpc)

    for raw in ("http", "HTTP", "Http/Protobuf", "http/protobuf"):
        calls.clear()
        setup_module._otel_configured = False
        config = A2ATObservabilityConfig(endpoint="http://collector:4318", protocol=raw)
        setup_module.ensure_otel_configured(config)
        assert calls == [("http", "http://collector:4318")], raw


def test_config_protocol_invalid_falls_back_to_grpc_with_warning(monkeypatch, caplog) -> None:
    """M5: 非法协议值经 config 路径回退 grpc 并告警（不再静默）。"""
    from a2a_t.observability.config import A2ATObservabilityConfig

    setup_module = importlib.import_module("a2a_t.observability.setup")
    calls: list[str] = []

    def fake_http(endpoint: str):
        calls.append("http")
        return None, None, None

    def fake_grpc(endpoint: str):
        calls.append("grpc")
        return None, None, None

    monkeypatch.setattr(setup_module, "_build_http_exporters", fake_http)
    monkeypatch.setattr(setup_module, "_build_grpc_exporters", fake_grpc)

    with caplog.at_level(logging.WARNING, logger="a2at.observability"):
        config = A2ATObservabilityConfig(endpoint="http://collector:4317", protocol="thrift")
        setup_module.ensure_otel_configured(config)

    assert calls == ["grpc"]
    assert any("thrift" in record.message for record in caplog.records)
