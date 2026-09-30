"""A2ATClientTransportDecorator tests: span naming, links/parent, stash, metrics, degradation."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from opentelemetry import metrics as metrics_api
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from a2a_t.observability import _otel_compat
from a2a_t.observability.attributes import (
    ATTR_AUTHORIZATION_POLICY_OPERATION_TYPE,
    ATTR_EXTENSION_NAME,
    ATTR_GEN_AI_CONVERSATION_ID,
    ATTR_GEN_AI_OPERATION_NAME,
    ATTR_GEN_AI_REQUEST_MODEL,
    ATTR_GEN_AI_USAGE_INPUT_TOKENS,
    ATTR_GEN_AI_USAGE_OUTPUT_TOKENS,
    ATTR_TASK_ID,
    ATTR_TASK_STATUS,
    ATTR_TASK_TYPE,
)
from a2a_t.observability.client import transport_decorator
from a2a_t.observability.client.transport_decorator import A2ATClientTransportDecorator
from a2a_t.observability.config import A2ATObservabilityConfig
from a2a_t.observability.llm_stash import LLMUsageStash, clear_llm_usage, get_llm_usage, set_llm_usage
from tests.observability.stubs import (
    FakeClientTransport,
    FakeContext,
    FakeMessage,
    FakeStreamResponse,
    make_artifact_event,
    make_final_event,
    make_send_request,
    make_status_event,
)

_EXTENSION_BASE = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/"
_TASK_T_URI = f"{_EXTENSION_BASE}Task-T"
_NEGOTIATION_T_URI = f"{_EXTENSION_BASE}Negotiation-T"


class FakeSyncTransport:
    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, Any, Any]] = []

    async def send_message(self, request: Any, *, context: Any = None) -> Any:
        self.calls.append(("send_message", request, context))
        if self.error is not None:
            raise self.error
        return self.result

    async def get_task(self, request: Any, *, context: Any = None) -> Any:
        self.calls.append(("get_task", request, context))
        if self.error is not None:
            raise self.error
        return self.result

    async def close(self) -> None:
        self.calls.append(("close", None, None))


class FakeStreamingTransport:
    def __init__(self, events: list[Any], error: BaseException | None = None) -> None:
        self.events = events
        self.error = error
        self.calls: list[tuple[str, Any, Any]] = []

    async def send_message_streaming(self, request: Any, *, context: Any = None) -> Any:
        self.calls.append(("send_message_streaming", request, context))
        for event in self.events:
            yield event
        if self.error is not None:
            raise self.error


@pytest.fixture()
def exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    trace.set_tracer_provider(TracerProvider())
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


@pytest.fixture(autouse=True)
def _isolated_meter_provider() -> Iterator[InMemoryMetricReader]:
    """Give every test a fresh MeterProvider (set_meter_provider is once-per-test).

    Isolates the decorator's lazily-created histograms from any meter provider left
    global by test_setup (an OTLP reader pointed at a fake collector) — otherwise they
    export there at shutdown and litter stderr with dial failures.
    """
    reader = InMemoryMetricReader()
    metrics_api.set_meter_provider(MeterProvider(metric_readers=[reader]))
    transport_decorator._METRIC_INSTRUMENTS.clear()
    yield reader


@pytest.fixture()
def metric_reader(_isolated_meter_provider: InMemoryMetricReader) -> InMemoryMetricReader:
    return _isolated_meter_provider


def _message_response(task_id: str = "T1", context_id: str = "C1") -> FakeStreamResponse:
    return FakeStreamResponse("message", FakeMessage(metadata={}, task_id=task_id, context_id=context_id))


def _metric_names(reader: InMemoryMetricReader) -> set[str]:
    data = reader.get_metrics_data()
    if data is None:
        return set()
    return {
        metric.name
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


def _histogram_point_attributes(reader: InMemoryMetricReader, name: str) -> list[dict[str, Any]]:
    data = reader.get_metrics_data()
    points: list[dict[str, Any]] = []
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == name:
                    points.extend(dict(point.attributes) for point in metric.data.data_points)
    return points


async def test_send_message_entry_span_attributes(exporter: InMemorySpanExporter) -> None:
    request = make_send_request({_TASK_T_URI: "## 任务类型(Task Type)\n配置下发"}, task_id="T1", context_id="C1")
    inner = FakeSyncTransport(result=_message_response())
    decorator = A2ATClientTransportDecorator(inner)
    context = FakeContext()

    result = await decorator.send_message(request, context=context)

    assert result is inner.result
    assert inner.calls == [("send_message", request, context)]
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["SendMessage"]
    span = spans[0]
    assert span.kind == SpanKind.CLIENT
    attrs = span.attributes or {}
    assert attrs[ATTR_GEN_AI_OPERATION_NAME] == "SendMessage"
    assert attrs["gen_ai.agent.a2a.operation.name"] == "SendMessage"
    assert attrs[ATTR_GEN_AI_CONVERSATION_ID] == "C1"
    assert attrs["gen_ai.agent.a2a.context_id"] == "C1"
    assert attrs[ATTR_TASK_ID] == "T1"
    assert attrs["gen_ai.agent.a2a.task_id"] == "T1"
    assert attrs[ATTR_EXTENSION_NAME] == "Task-T"
    assert attrs[ATTR_TASK_TYPE] == "配置下发"
    assert attrs["gen_ai.agent.a2a.role"] == "ROLE_USER"
    assert attrs["gen_ai.agent.a2a.parts.number"] == 0
    assert span.status.status_code == StatusCode.OK


async def test_send_message_records_l1_l3_metrics(
    exporter: InMemorySpanExporter, metric_reader: InMemoryMetricReader
) -> None:
    request = make_send_request({}, task_id="T1", context_id="C1")
    inner = FakeSyncTransport(result=_message_response())
    decorator = A2ATClientTransportDecorator(inner)

    await decorator.send_message(request, context=FakeContext())

    names = _metric_names(metric_reader)
    assert "gen_ai.client.operation.duration" in names
    assert "a2at.task.request.duration" in names
    for name in ("gen_ai.client.operation.duration", "a2at.task.request.duration"):
        points = _histogram_point_attributes(metric_reader, name)
        assert points, name
        for attrs in points:
            assert attrs[ATTR_GEN_AI_OPERATION_NAME] == "SendMessage"


async def test_send_message_streaming_event_link_spans(exporter: InMemorySpanExporter) -> None:
    events = [
        make_status_event("T9", "TASK_STATE_WORKING"),
        FakeStreamResponse("artifact_update", make_artifact_event("T9")),
        make_final_event("T9"),
    ]
    inner = FakeStreamingTransport(events)
    decorator = A2ATClientTransportDecorator(inner)
    request = make_send_request({}, task_id="T9", context_id="C9")

    collected = [event async for event in decorator.send_message_streaming(request, context=FakeContext())]

    assert collected == events
    spans = exporter.get_finished_spans()
    event_spans = [span for span in spans if span.name == "SendStreamingMessage-event"]
    assert len(event_spans) == 3
    entry = next(span for span in spans if span.name == "SendStreamingMessage")
    for span in event_spans:
        links = span.links or ()
        assert any(link.context.span_id == entry.context.span_id for link in links)
        attrs = span.attributes or {}
        assert attrs[ATTR_GEN_AI_OPERATION_NAME] == "SendStreamingMessage"
    assert [(span.attributes or {})["gen_ai.agent.a2at.streaming.event.kind"] for span in event_spans] == [
        "status",
        "artifact",
        "completed",
    ]
    status_attrs = event_spans[0].attributes or {}
    assert status_attrs[ATTR_TASK_ID] == "T9"
    assert status_attrs[ATTR_TASK_STATUS] == "working"


async def test_streaming_negotiation_message_creates_parent_span(exporter: InMemorySpanExporter) -> None:
    metadata = {
        _NEGOTIATION_T_URI: "negotiation prompt",
        "negotiationContext": {"id": "N1", "round": 2, "maxRounds": 5, "performative": "PROPOSE"},
    }
    message = FakeMessage(metadata, task_id="T2", context_id="C2")
    inner = FakeStreamingTransport([message])
    decorator = A2ATClientTransportDecorator(inner)
    request = make_send_request({}, task_id="T2", context_id="C2")

    collected = [event async for event in decorator.send_message_streaming(request, context=FakeContext())]

    assert collected == [message]
    spans = exporter.get_finished_spans()
    assert sorted(span.name for span in spans) == ["SendStreamingMessage", "SendStreamingMessage-negotiation"]
    entry = next(span for span in spans if span.name == "SendStreamingMessage")
    negotiation = next(span for span in spans if span.name.endswith("-negotiation"))
    assert negotiation.parent is not None
    assert negotiation.parent.span_id == entry.context.span_id
    attrs = negotiation.attributes or {}
    assert attrs[ATTR_EXTENSION_NAME] == "Negotiation-T"
    assert attrs["gen_ai.agent.a2at.negotiation.id"] == "N1"
    assert attrs["gen_ai.agent.a2at.negotiation.round"] == 2
    assert attrs["gen_ai.agent.a2at.negotiation.max_rounds"] == 5
    assert attrs["gen_ai.agent.a2at.negotiation.performative"] == "PROPOSE"


async def test_streaming_non_negotiation_message_sets_entry_attrs_only(exporter: InMemorySpanExporter) -> None:
    message = FakeMessage({_TASK_T_URI: "regular reply"}, task_id="T3", context_id="C3")
    inner = FakeStreamingTransport([message])
    decorator = A2ATClientTransportDecorator(inner)
    request = make_send_request({}, task_id="T3", context_id="C3")

    collected = [event async for event in decorator.send_message_streaming(request, context=FakeContext())]

    assert collected == [message]
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["SendStreamingMessage"]
    attrs = spans[0].attributes or {}
    assert attrs[ATTR_TASK_ID] == "T3"
    assert attrs["gen_ai.agent.a2a.task_id"] == "T3"
    assert attrs[ATTR_EXTENSION_NAME] == "Task-T"


async def test_streaming_error_emits_error_link_span(exporter: InMemorySpanExporter) -> None:
    events = [make_status_event("T4", "TASK_STATE_WORKING")]
    inner = FakeStreamingTransport(events, error=RuntimeError("stream boom"))
    decorator = A2ATClientTransportDecorator(inner)
    request = make_send_request({}, task_id="T4", context_id="C4")

    with pytest.raises(RuntimeError, match="stream boom"):
        async for _ in decorator.send_message_streaming(request, context=FakeContext()):
            pass

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert "SendStreamingMessage-error" in spans
    entry = spans["SendStreamingMessage"]
    error_span = spans["SendStreamingMessage-error"]
    links = error_span.links or ()
    assert any(link.context.span_id == entry.context.span_id for link in links)
    assert (error_span.attributes or {})["error.type"] == "RuntimeError"
    assert entry.status.status_code == StatusCode.ERROR


async def test_trace_disabled_no_spans_but_metrics_and_logs(
    exporter: InMemorySpanExporter,
    metric_reader: InMemoryMetricReader,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§8.1: A2AT_TRACE_ENABLED=false → zero spans, metrics + logs still recorded."""
    monkeypatch.setattr(_otel_compat, "_TRACE_ENABLED", False)

    sync_inner = FakeSyncTransport(result=_message_response())
    sync_decorator = A2ATClientTransportDecorator(sync_inner)
    sync_request = make_send_request({_TASK_T_URI: "## 任务类型(Task Type)\n配置下发"}, task_id="T5", context_id="C5")
    assert await sync_decorator.send_message(sync_request, context=FakeContext()) is sync_inner.result

    events = [make_status_event("T5", "TASK_STATE_WORKING"), make_final_event("T5")]
    streaming_inner = FakeStreamingTransport(events)
    streaming_decorator = A2ATClientTransportDecorator(streaming_inner)
    streaming_request = make_send_request({}, task_id="T5", context_id="C5")
    with caplog.at_level(logging.DEBUG, logger="a2at.observability"):
        collected = [
            event async for event in streaming_decorator.send_message_streaming(streaming_request, context=None)
        ]

    assert collected == events
    assert not exporter.get_finished_spans()
    assert len(sync_inner.calls) == 1
    assert len(streaming_inner.calls) == 1
    names = _metric_names(metric_reader)
    assert "gen_ai.client.operation.duration" in names
    assert "a2at.task.request.duration" in names
    points = _histogram_point_attributes(metric_reader, "gen_ai.client.operation.duration")
    assert points
    assert any("task.status_changed" in record.getMessage() for record in caplog.records)


async def test_config_trace_enabled_false_no_spans_but_metrics(
    exporter: InMemorySpanExporter, metric_reader: InMemoryMetricReader
) -> None:
    """Explicit config trace_enabled=false → no spans, metrics survive (§8.1)."""
    config = A2ATObservabilityConfig(trace_enabled=False)
    inner = FakeSyncTransport(result=_message_response())
    decorator = A2ATClientTransportDecorator(inner, config=config)

    result = await decorator.send_message(make_send_request({}, task_id="T15", context_id="C15"), context=FakeContext())

    assert result is inner.result
    assert not exporter.get_finished_spans()
    assert "gen_ai.client.operation.duration" in _metric_names(metric_reader)


async def test_trace_off_clears_llm_stash(exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch) -> None:
    """The LLM stash is span-bound: with trace off it is dropped, not leaked."""
    monkeypatch.setattr(_otel_compat, "_TRACE_ENABLED", False)
    set_llm_usage(LLMUsageStash(input_tokens=7, output_tokens=8, request_model="qwen-max"))
    inner = FakeSyncTransport(result=_message_response())
    decorator = A2ATClientTransportDecorator(inner)

    try:
        await decorator.send_message(make_send_request({}, task_id="T16", context_id="C16"), context=FakeContext())
        assert get_llm_usage() is None
    finally:
        clear_llm_usage()


async def test_traceparent_injected_into_service_parameters(exporter: InMemorySpanExporter) -> None:
    request = make_send_request({}, task_id="T6", context_id="C6")
    inner = FakeSyncTransport(result=_message_response())
    decorator = A2ATClientTransportDecorator(inner)
    context = FakeContext()

    await decorator.send_message(request, context=context)

    traceparent = context.service_parameters.get("traceparent")
    assert traceparent is not None
    assert traceparent.startswith("00-")
    entry = exporter.get_finished_spans()[-1]
    assert traceparent[3:35] == format(entry.context.trace_id, "032x")


async def test_llm_stash_usage_attributes_on_entry_span(exporter: InMemorySpanExporter) -> None:
    set_llm_usage(LLMUsageStash(input_tokens=120, output_tokens=45, request_model="qwen-max"))
    request = make_send_request({}, task_id="T7", context_id="C7")
    inner = FakeSyncTransport(result=_message_response())
    decorator = A2ATClientTransportDecorator(inner)

    try:
        await decorator.send_message(request, context=FakeContext())
    finally:
        leftover = get_llm_usage()
        clear_llm_usage()

    attrs = exporter.get_finished_spans()[-1].attributes or {}
    assert attrs[ATTR_GEN_AI_USAGE_INPUT_TOKENS] == 120
    assert attrs[ATTR_GEN_AI_USAGE_OUTPUT_TOKENS] == 45
    assert attrs[ATTR_GEN_AI_REQUEST_MODEL] == "qwen-max"
    assert leftover is None


async def test_get_task_creates_named_span(exporter: InMemorySpanExporter) -> None:
    inner = FakeSyncTransport(result=SimpleNamespace(id="T8"))
    decorator = A2ATClientTransportDecorator(inner)
    request = SimpleNamespace(id="T8")

    result = await decorator.get_task(request, context=None)

    assert result is inner.result
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["GetTask"]
    span = spans[0]
    assert span.kind == SpanKind.CLIENT
    attrs = span.attributes or {}
    assert attrs[ATTR_GEN_AI_OPERATION_NAME] == "GetTask"


async def test_close_and_getattr_delegate_to_inner() -> None:
    inner = FakeSyncTransport(result="business-value")
    decorator = A2ATClientTransportDecorator(inner)

    await decorator.close()

    assert inner.calls == [("close", None, None)]
    assert decorator.result == "business-value"


async def test_decorator_failure_never_breaks_business_flow(
    exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ExplodingTracer:
        def start_span(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("otel exploded")

    monkeypatch.setattr(_otel_compat, "get_tracer", lambda *args, **kwargs: ExplodingTracer())
    request = make_send_request({_TASK_T_URI: "## 任务类型(Task Type)\n配置下发"}, task_id="T10", context_id="C10")
    inner = FakeSyncTransport(result=_message_response())
    decorator = A2ATClientTransportDecorator(inner)

    result = await decorator.send_message(request, context=FakeContext())

    assert result is inner.result
    assert not exporter.get_finished_spans()


async def test_request_response_payload_attributes_when_enabled(exporter: InMemorySpanExporter) -> None:
    config = A2ATObservabilityConfig(extract_request=True, extract_response=True)
    request = make_send_request({}, task_id="T11", context_id="C11")
    # Plain message result (not a StreamResponse): FakeStreamResponse's duck-typed
    # DESCRIPTOR (None) makes hasattr() True and MessageToDict fail → payload degrades
    # to None, so the non-protobuf {"raw": ...} branch is exercised with a real object.
    inner = FakeSyncTransport(result=FakeMessage(metadata={}, task_id="T11", context_id="C11"))
    decorator = A2ATClientTransportDecorator(inner, config=config)

    await decorator.send_message(request, context=FakeContext())

    attrs = exporter.get_finished_spans()[-1].attributes or {}
    assert "gen_ai.agent.a2a.request" in attrs
    assert "gen_ai.agent.a2a.response" in attrs


async def test_payload_attributes_absent_by_default(exporter: InMemorySpanExporter) -> None:
    request = make_send_request({}, task_id="T12", context_id="C12")
    inner = FakeSyncTransport(result=_message_response())
    decorator = A2ATClientTransportDecorator(inner)

    await decorator.send_message(request, context=FakeContext())

    attrs = exporter.get_finished_spans()[-1].attributes or {}
    assert "gen_ai.agent.a2a.request" not in attrs
    assert "gen_ai.agent.a2a.response" not in attrs


async def test_authorization_operation_type_regex_attribute(exporter: InMemorySpanExporter) -> None:
    request = make_send_request(
        {_EXTENSION_BASE + "Authorization-T": "## 授权策略的操作类型\n变更授权策略"}, task_id="T13", context_id="C13"
    )
    inner = FakeSyncTransport(result=_message_response())
    decorator = A2ATClientTransportDecorator(inner)

    await decorator.send_message(request, context=FakeContext())

    attrs = exporter.get_finished_spans()[-1].attributes or {}
    assert attrs[ATTR_AUTHORIZATION_POLICY_OPERATION_TYPE] == "变更授权策略"
    assert attrs[ATTR_EXTENSION_NAME] == "Authorization-T"


async def test_regex_opt_out_via_empty_string(exporter: InMemorySpanExporter) -> None:
    config = A2ATObservabilityConfig(task_type_regex="")
    request = make_send_request({_TASK_T_URI: "## 任务类型(Task Type)\n配置下发"}, task_id="T14", context_id="C14")
    inner = FakeSyncTransport(result=_message_response())
    decorator = A2ATClientTransportDecorator(inner, config=config)

    await decorator.send_message(request, context=FakeContext())

    attrs = exporter.get_finished_spans()[-1].attributes or {}
    assert ATTR_TASK_TYPE not in attrs


async def test_config_enabled_false_full_pass_through(
    exporter: InMemorySpanExporter, metric_reader: InMemoryMetricReader
) -> None:
    """Config enabled=false → full pass-through: no spans, no metrics, inner still called."""
    config = A2ATObservabilityConfig(enabled=False)
    inner = FakeSyncTransport(result=_message_response())
    decorator = A2ATClientTransportDecorator(inner, config=config)

    result = await decorator.send_message(make_send_request({}, task_id="T20", context_id="C20"), context=FakeContext())

    assert result is inner.result
    assert len(inner.calls) == 1
    assert not exporter.get_finished_spans()
    assert _metric_names(metric_reader) == set()


async def test_metric_disabled_records_no_metrics_but_spans(
    exporter: InMemorySpanExporter,
    metric_reader: InMemoryMetricReader,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§8.1: A2AT_METRIC_ENABLED=false → no metric samples; spans keep working."""
    monkeypatch.setattr(_otel_compat, "_METRIC_ENABLED", False)
    inner = FakeSyncTransport(result=_message_response())
    decorator = A2ATClientTransportDecorator(inner)

    result = await decorator.send_message(make_send_request({}, task_id="T21", context_id="C21"), context=FakeContext())

    assert result is inner.result
    assert [span.name for span in exporter.get_finished_spans()] == ["SendMessage"]
    assert _metric_names(metric_reader) == set()


async def test_log_disabled_no_auto_logs_but_spans_and_metrics(
    exporter: InMemorySpanExporter,
    metric_reader: InMemoryMetricReader,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§8.1: A2AT_LOG_ENABLED=false → no auto logs; spans + metrics keep working."""
    monkeypatch.setattr(_otel_compat, "_LOG_ENABLED", False)
    events = [make_status_event("T22", "TASK_STATE_WORKING"), make_final_event("T22")]
    inner = FakeStreamingTransport(events)
    decorator = A2ATClientTransportDecorator(inner)

    with caplog.at_level(logging.DEBUG, logger="a2at.observability"):
        collected = [
            event
            async for event in decorator.send_message_streaming(
                make_send_request({}, task_id="T22", context_id="C22"), context=None
            )
        ]

    assert collected == events
    spans = exporter.get_finished_spans()
    assert sorted(span.name for span in spans) == [
        "SendStreamingMessage",
        "SendStreamingMessage-event",
        "SendStreamingMessage-event",
    ]
    assert "a2at.task.request.duration" in _metric_names(metric_reader)
    assert not [
        record
        for record in caplog.records
        if "task.status_changed" in record.getMessage() or "task.artifact" in record.getMessage()
    ]


async def test_double_wrap_is_idempotent(exporter: InMemorySpanExporter) -> None:
    """Double decoration must not double-span: the outer decorator adopts the inner transport."""
    inner = FakeSyncTransport(result=_message_response())
    once = A2ATClientTransportDecorator(inner)
    twice = A2ATClientTransportDecorator(once)

    assert twice._inner is inner

    result = await twice.send_message(make_send_request({}, task_id="T23", context_id="C23"), context=FakeContext())

    assert result is inner.result
    assert len(inner.calls) == 1
    assert [span.name for span in exporter.get_finished_spans()] == ["SendMessage"]


async def test_subscribe_wraps_async_generator_stream(exporter: InMemorySpanExporter) -> None:
    """ABC shape: subscribe is an async generator — stream-wrapped ``SubscribeToTask`` entry span."""
    events = [make_status_event("T24", "TASK_STATE_WORKING"), make_final_event("T24")]
    inner = FakeClientTransport(events=events)
    decorator = A2ATClientTransportDecorator(inner)
    request = make_send_request({}, task_id="T24", context_id="C24")

    collected = [event async for event in decorator.subscribe(request, context=None)]

    assert collected == events
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["SubscribeToTask"]
    assert spans[0].kind == SpanKind.CLIENT
    assert spans[0].status.status_code == StatusCode.OK
    assert len(inner.calls) == 1


@pytest.mark.parametrize(
    ("method", "span_name"),
    [
        ("list_tasks", "ListTasks"),
        ("cancel_task", "CancelTask"),
        ("create_task_push_notification_config", "CreateTaskPushNotificationConfig"),
        ("get_task_push_notification_config", "GetTaskPushNotificationConfig"),
        ("list_task_push_notification_configs", "ListTaskPushNotificationConfigs"),
        ("delete_task_push_notification_config", "DeleteTaskPushNotificationConfig"),
        ("get_extended_agent_card", "GetExtendedAgentCard"),
    ],
)
async def test_simple_methods_create_named_client_spans(
    exporter: InMemorySpanExporter, method: str, span_name: str
) -> None:
    inner = FakeClientTransport(result=SimpleNamespace(id="T25"))
    decorator = A2ATClientTransportDecorator(inner)
    request = SimpleNamespace(id="T25")

    result = await getattr(decorator, method)(request, context=None)

    assert result is inner.result
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == [span_name]
    assert spans[0].kind == SpanKind.CLIENT
    assert (spans[0].attributes or {})[ATTR_GEN_AI_OPERATION_NAME] == span_name
