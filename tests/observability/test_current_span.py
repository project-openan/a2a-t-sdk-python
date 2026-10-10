from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import a2a_t.observability._otel_compat as _otel_compat
from a2a_t.observability.current_span import A2ATCurrentSpan, current_span
from a2a_t.observability.span import A2ATSpan


@pytest.fixture()
def exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    trace.set_tracer_provider(TracerProvider())
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


def test_current_span_inside_active_otel_span(exporter: InMemorySpanExporter) -> None:
    with trace.get_tracer("t").start_as_current_span("active") as otel_span:
        current = current_span()
        assert isinstance(current, A2ATCurrentSpan)
        assert current.is_valid is True
        ctx = otel_span.get_span_context()
        assert current.trace_id == trace.format_trace_id(ctx.trace_id)
        assert current.span_id == trace.format_span_id(ctx.span_id)
        current.set_attribute("a2at.custom", "v")
    finished = exporter.get_finished_spans()[-1]
    assert (finished.attributes or {})["a2at.custom"] == "v"


def test_current_span_inside_a2at_span(exporter: InMemorySpanExporter) -> None:
    with A2ATSpan("manual.op"):
        current = current_span()
        assert current is not None
        assert current.is_valid is True
        assert current.trace_id is not None
        assert current.span_id is not None
        current.set_attribute("a2at.inside", 1)
    finished = exporter.get_finished_spans()[-1]
    assert (finished.attributes or {})["a2at.inside"] == 1
    assert trace.format_trace_id(finished.context.trace_id) == current.trace_id


def test_current_span_none_when_no_span() -> None:
    assert current_span() is None


def test_current_span_none_when_current_span_already_ended(exporter: InMemorySpanExporter) -> None:
    """泄漏场景防御（2026-09-30）：请求结束后 detach 失败使已结束的 entry span
    残留为 current（OTel 吞掉 detach 异常）。已结束的 span 不是"活跃"span——
    current_span() 必须返回 None，避免调用方拿到 stale 归因或向已结束 span
    追加属性（静默丢弃）。"""
    with trace.get_tracer("t").start_as_current_span("ended-request") as span:
        pass
    assert span.is_recording() is False
    with trace.use_span(span, end_on_exit=False):
        # 模拟泄漏：已结束的 span 仍为 current
        assert current_span() is None


def test_current_span_none_when_trace_signal_disabled(
    exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_otel_compat, "_TRACE_ENABLED", False)
    with trace.get_tracer("t").start_as_current_span("active"):
        assert current_span() is None


def test_current_span_none_when_master_disabled(
    exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_otel_compat, "_ENABLED", False)
    with trace.get_tracer("t").start_as_current_span("active"):
        assert current_span() is None


def test_current_span_set_attribute_absorbs_errors() -> None:
    class Boom:
        def set_attribute(self, key: str, value: object) -> None:
            raise RuntimeError("boom")

    wrapper = A2ATCurrentSpan(Boom())
    wrapper.set_attribute("k", "v")


def test_current_span_invalid_context_reports_not_valid() -> None:
    class FakeCtx:
        is_valid = False
        trace_id = 0
        span_id = 0

    class FakeSpan:
        def get_span_context(self) -> FakeCtx:
            return FakeCtx()

    wrapper = A2ATCurrentSpan(FakeSpan())
    assert wrapper.is_valid is False
    assert wrapper.trace_id is None
    assert wrapper.span_id is None


def test_current_span_none_span_context_reports_not_valid() -> None:
    class FakeSpan:
        def get_span_context(self) -> None:
            return None

    wrapper = A2ATCurrentSpan(FakeSpan())
    assert wrapper.is_valid is False
    assert wrapper.trace_id is None
    assert wrapper.span_id is None
