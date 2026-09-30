from __future__ import annotations

import json
import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from a2a_t.observability.config import A2ATObservabilityConfig
from a2a_t.observability.logs import log_event


def test_log_event_json_with_trace_ids(caplog) -> None:
    trace.set_tracer_provider(TracerProvider())
    with caplog.at_level(logging.INFO, logger="a2at.observability"):
        with trace.get_tracer("t").start_as_current_span("s") as span:
            log_event("task.status_changed", logging.INFO, fields={"gen_ai.agent.a2at.task.id": "T1", "gen_ai.agent.a2at.task.status": "working"})
    record = next(r for r in caplog.records if r.message.startswith("{"))
    data = json.loads(record.message)
    assert data["event"] == "task.status_changed"
    assert data["gen_ai.agent.a2at.task.id"] == "T1"
    assert data["trace_id"] == format(span.get_span_context().trace_id, "032x")


def test_payload_disabled_by_default(caplog) -> None:
    with caplog.at_level(logging.DEBUG, logger="a2at.observability"):
        log_event("task.request", logging.DEBUG, fields={}, payload="secret-text", config=A2ATObservabilityConfig())
    assert all("secret-text" not in r.message for r in caplog.records)


def test_log_event_silent_when_disabled(monkeypatch, caplog) -> None:
    from a2a_t.observability import _otel_compat

    monkeypatch.setattr(_otel_compat, "_ENABLED", False)
    with caplog.at_level(logging.DEBUG, logger="a2at.observability"):
        log_event("task.request", logging.DEBUG, fields={"gen_ai.agent.a2at.task.id": "T1"})
    assert caplog.records == []


def test_payload_truncated_and_redacted(caplog) -> None:
    config = A2ATObservabilityConfig(
        payload_log_enabled=True,
        payload_log_max_length=10,
        payload_redactor=lambda s: s.replace("secret", "***"),
    )
    with caplog.at_level(logging.DEBUG, logger="a2at.observability"):
        log_event("task.request", logging.DEBUG, fields={}, payload="secret-0123456789abcdef", config=config)
    data = json.loads(caplog.records[-1].message)
    assert data["payload"].startswith("***")
    assert data["payload"].endswith("[truncated]")
    assert len(data["payload"]) <= 10 + len("[truncated]")


def test_task_response_event(caplog) -> None:
    trace.set_tracer_provider(TracerProvider())
    config = A2ATObservabilityConfig(payload_log_enabled=True)
    with caplog.at_level(logging.DEBUG, logger="a2at.observability"):
        with trace.get_tracer("t").start_as_current_span("s") as span:
            log_event("task.response", logging.DEBUG, fields={"gen_ai.agent.a2at.task.id": "T1"}, payload='{"text": "done"}', config=config)
    record = next(r for r in caplog.records if r.message.startswith("{"))
    data = json.loads(record.message)
    assert data["event"] == "task.response"
    assert data["gen_ai.agent.a2at.task.id"] == "T1"
    assert data["payload"] == '{"text": "done"}'
    assert data["trace_id"] == format(span.get_span_context().trace_id, "032x")


def test_all_eight_event_names_emit(caplog) -> None:
    events: list[tuple[str, int, dict[str, object]]] = [
        ("task.request", logging.DEBUG, {"gen_ai.agent.a2at.extension.name": "ext-a", "gen_ai.agent.a2at.task.id": "T1"}),
        ("task.response", logging.DEBUG, {"gen_ai.agent.a2at.task.id": "T1"}),
        ("task.status_changed", logging.INFO, {"gen_ai.agent.a2at.task.id": "T1", "gen_ai.agent.a2at.task.status": "completed"}),
        ("task.artifact", logging.INFO, {"gen_ai.agent.a2at.task.id": "T1", "artifact.name": "result"}),
        ("negotiation.message", logging.DEBUG, {"gen_ai.agent.a2at.negotiation.id": "N1", "gen_ai.agent.a2at.negotiation.round": 1, "gen_ai.agent.a2at.negotiation.performative": "propose"}),
        ("authorization.delivery", logging.INFO, {"policy.operation.type": "payment", "gen_ai.agent.a2at.extension.name": "ext-a"}),
        ("notification.subscription", logging.INFO, {"topic": "orders", "gen_ai.agent.a2at.extension.name": "ext-a"}),
        ("notification.push", logging.INFO, {"gen_ai.agent.a2at.task.id": "T1", "gen_ai.agent.a2at.push.notification.url": "https://example.com/push", "gen_ai.agent.a2at.streaming.event.kind": "status-update"}),
    ]
    with caplog.at_level(logging.DEBUG, logger="a2at.observability"):
        for event, level, fields in events:
            log_event(event, level, fields=fields)
    emitted = [json.loads(r.message)["event"] for r in caplog.records if r.message.startswith("{")]
    assert emitted == [name for name, _, _ in events]
