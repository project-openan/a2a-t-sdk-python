from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import a2a_t.observability._otel_compat as _otel_compat


@pytest.fixture
def exporter_setup() -> Iterator[InMemorySpanExporter]:
    """In-memory span exporter; each test wires its own global TracerProvider."""
    exporter = InMemorySpanExporter()
    yield exporter
    exporter.clear()


def test_import_exposes_helpers() -> None:
    import a2a_t.observability._otel_compat as compat

    assert isinstance(compat.otel_installed, bool)
    assert callable(compat.get_tracer)
    assert callable(compat.get_meter)
    assert callable(compat.is_enabled)


def test_noop_absorbs_everything() -> None:
    import a2a_t.observability._otel_compat as compat

    compat._ENABLED = False  # 模拟总开关关闭
    try:
        assert compat.is_enabled() is False
        tracer = compat.get_tracer()
        with tracer.start_as_current_span("x") as span:
            span.set_attribute("k", "v")
        meter = compat.get_meter()
        meter.create_histogram("h").record(1.0)
        meter.create_counter("c").add(1)
        span.end()  # 重复 end 亦吸收
    finally:
        compat._ENABLED = True


def test_current_span_noop_safe() -> None:
    import a2a_t.observability._otel_compat as compat

    span = compat.get_current_span()
    assert span is not None  # NoOpSpan 或真实 INVALID_SPAN，均为对象


def test_signal_toggles_default_true(monkeypatch) -> None:
    import a2a_t.observability._otel_compat as compat

    monkeypatch.setattr(compat, "_ENABLED", True)
    assert compat.is_trace_enabled() is True
    assert compat.is_metric_enabled() is True
    assert compat.is_log_enabled() is True


def test_signal_toggles_independent(monkeypatch) -> None:
    import a2a_t.observability._otel_compat as compat

    monkeypatch.setattr(compat, "_ENABLED", True)
    monkeypatch.setattr(compat, "_TRACE_ENABLED", False)
    monkeypatch.setattr(compat, "_METRIC_ENABLED", False)
    monkeypatch.setattr(compat, "_LOG_ENABLED", False)
    assert compat.is_trace_enabled() is False
    assert compat.is_metric_enabled() is False
    assert compat.is_log_enabled() is False


def test_master_switch_overrides_signals(monkeypatch) -> None:
    import a2a_t.observability._otel_compat as compat

    monkeypatch.setattr(compat, "_ENABLED", False)
    monkeypatch.setattr(compat, "_TRACE_ENABLED", True)
    assert compat.is_trace_enabled() is False  # master off → all off


def test_signal_toggles_accept_1(monkeypatch) -> None:
    import a2a_t.observability._otel_compat as compat

    monkeypatch.setattr(compat, "_ENABLED", True)
    monkeypatch.setattr(compat, "_TRACE_ENABLED", True)
    assert compat._to_bool("1") is True
    assert compat._to_bool("yes") is True
    assert compat._to_bool("on") is True
    assert compat._to_bool("false") is False


def test_use_span_helper(exporter_setup) -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    trace.set_tracer_provider(TracerProvider())
    span = trace.get_tracer("t").start_span("test")
    with _otel_compat.use_span(span, end_on_exit=False):
        assert trace.get_current_span() is span
    assert span.is_recording()  # end_on_exit=False → still recording
    span.end()
