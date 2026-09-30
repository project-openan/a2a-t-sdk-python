from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from a2a_t.observability import _otel_compat
from a2a_t.observability.attributes import (
    ATTR_AUTHORIZATION_OPERATION_RISK_LEVEL,
    ATTR_AUTHORIZATION_OPERATION_TYPE,
    ATTR_AUTHORIZATION_POLICY_ID,
    ATTR_NEGOTIATION_TOTAL_ROUNDS,
    ATTR_NOTIFICATION_TOPIC,
    ATTR_TASK_TYPE,
)
from a2a_t.observability.propagation import extract_trace_context, inject_traceparent
from a2a_t.observability.span import A2ATSpan


@pytest.fixture()
def exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    trace.set_tracer_provider(TracerProvider())
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


def test_span_lifecycle_and_attributes(exporter: InMemorySpanExporter) -> None:
    with A2ATSpan("my.op", attributes={"a2at.custom": "x"}) as span:
        span.set_attribute(ATTR_TASK_TYPE, "配置下发")
        span.set_attribute(ATTR_NOTIFICATION_TOPIC, "alarm")
        span.set_attribute(ATTR_NEGOTIATION_TOTAL_ROUNDS, 3)
        span.set_attribute(ATTR_AUTHORIZATION_POLICY_ID, "P1")
        span.set_attribute(ATTR_AUTHORIZATION_OPERATION_TYPE, "变更")
        span.set_attribute(ATTR_AUTHORIZATION_OPERATION_RISK_LEVEL, "medium")
        span.set_attribute("k", 1)
    finished = exporter.get_finished_spans()[-1]
    assert finished.name == "my.op"
    attrs = finished.attributes or {}
    assert attrs["a2at.custom"] == "x"
    assert attrs["gen_ai.agent.a2at.task.type"] == "配置下发"
    assert attrs["gen_ai.agent.a2at.notification.topic"] == "alarm"
    assert attrs["gen_ai.agent.a2at.negotiation.total_rounds"] == 3
    assert attrs["authorization.policy.id"] == "P1"
    assert attrs["authorization.operation_type"] == "变更"
    assert attrs["authorization.operation_risk_level"] == "medium"
    assert attrs["k"] == 1


def test_span_error_status(exporter: InMemorySpanExporter) -> None:
    with pytest.raises(ValueError), A2ATSpan("boom"):
        raise ValueError("x")
    assert exporter.get_finished_spans()[-1].status.status_code == StatusCode.ERROR


def test_span_explicit_parent_context(exporter: InMemorySpanExporter) -> None:
    headers: dict[str, str] = {}
    with trace.get_tracer("t").start_as_current_span("remote-root"):
        inject_traceparent(headers)
    ctx = extract_trace_context(headers)
    assert ctx is not None
    with A2ATSpan("child", context=ctx):
        pass
    spans = {s.name: s for s in exporter.get_finished_spans()}
    child = spans["child"]
    assert child.parent is not None
    assert child.parent.span_id == spans["remote-root"].context.span_id


def test_add_link_pre_enter(exporter: InMemorySpanExporter) -> None:
    # 语义：对另一 A2ATSpan（在其 enter 期间或已结束）建立 link
    source = A2ATSpan("source")
    with source:
        pass
    target = A2ATSpan("target")
    target.add_link(source)
    with target:
        pass
    spans = {s.name: s for s in exporter.get_finished_spans()}
    links = spans["target"].links or []
    assert any(link.context.span_id == spans["source"].context.span_id for link in links)


def test_span_is_single_use(exporter: InMemorySpanExporter) -> None:
    span = A2ATSpan("once")
    with span:
        pass
    with pytest.raises(RuntimeError), span:
        pass
    assert len(exporter.get_finished_spans()) == 1


def test_noop_when_disabled(exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_otel_compat, "_ENABLED", False)
    with A2ATSpan("noop") as span:
        span.set_attribute(ATTR_TASK_TYPE, "x")
        span.set_attribute("k", "v")
    assert not exporter.get_finished_spans()


def test_span_has_no_apply_helpers() -> None:
    # v3 contract: apply_* sugar removed; users call set_attribute(ATTR_*, value) directly.
    for name in (
        "apply_task_type",
        "apply_negotiation_total_rounds",
        "apply_notification_topic",
        "apply_authorization",
    ):
        assert not hasattr(A2ATSpan, name)
