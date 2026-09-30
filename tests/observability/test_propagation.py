from __future__ import annotations

import pytest
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from a2a_t.observability.propagation import TRACEPARENT_HEADER, extract_trace_context, inject_traceparent


@pytest.fixture()
def tracer_provider() -> TracerProvider:
    trace.set_tracer_provider(TracerProvider())
    return trace.get_tracer_provider()


def test_roundtrip_inject_extract(tracer_provider: TracerProvider) -> None:
    headers: dict[str, str] = {}
    with trace.get_tracer("t").start_as_current_span("root") as span:
        inject_traceparent(headers)
    assert headers[TRACEPARENT_HEADER].startswith("00-")
    assert format(span.get_span_context().trace_id, "032x") in headers[TRACEPARENT_HEADER]

    token = otel_context.attach(extract_trace_context(headers))
    try:
        with trace.get_tracer("t").start_as_current_span("child") as child:
            assert child.get_span_context().trace_id == span.get_span_context().trace_id
            assert child.parent is not None
            assert child.parent.span_id == span.get_span_context().span_id
    finally:
        otel_context.detach(token)


def test_extract_missing_returns_none() -> None:
    assert extract_trace_context({"other": "x"}) is None


def test_extract_case_insensitive() -> None:
    headers = {"TraceParent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}
    assert extract_trace_context(headers) is not None


def test_noop_when_disabled(tracer_provider: TracerProvider, monkeypatch: pytest.MonkeyPatch) -> None:
    from a2a_t.observability import _otel_compat

    monkeypatch.setattr(_otel_compat, "_ENABLED", False)
    headers: dict[str, str] = {}
    inject_traceparent(headers)
    assert headers == {}
    assert extract_trace_context({TRACEPARENT_HEADER: "00-x-y-01"}) is None


def test_extract_invalid_traceparent_returns_none(tracer_provider: TracerProvider) -> None:
    assert extract_trace_context({"traceparent": "garbage"}) is None
