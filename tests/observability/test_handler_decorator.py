"""A2ATRequestHandlerDecorator tests: SERVER entry spans, per-event PARENT spans, push LINK, negotiation metrics, degradation."""

from __future__ import annotations

import asyncio
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
    ATTR_A2A_CONTEXT_ID,
    ATTR_A2A_OPERATION_NAME,
    ATTR_A2A_PARTS_NUMBER,
    ATTR_A2A_REQUEST,
    ATTR_A2A_RESPONSE,
    ATTR_A2A_TASK_ID,
    ATTR_EXTENSION_NAME,
    ATTR_GEN_AI_CONVERSATION_ID,
    ATTR_GEN_AI_OPERATION_NAME,
    ATTR_PUSH_NOTIFICATION_URL,
    ATTR_STREAMING_EVENT_KIND,
    ATTR_TASK_ID,
    ATTR_TASK_STATUS,
    ATTR_TASK_TYPE,
)
from a2a_t.observability.config import A2ATObservabilityConfig
from a2a_t.observability.current_span import current_span
from a2a_t.observability.negotiation_metrics import _METRIC_INSTRUMENTS as _NEGOTIATION_METRIC_INSTRUMENTS
from a2a_t.observability.negotiation_metrics import report_negotiation_rounds
from a2a_t.observability.server import handler_decorator
from a2a_t.observability.server.handler_decorator import (
    A2ATAgentExecutorDecorator,
    A2ATPushSenderDecorator,
    A2ATRequestHandlerDecorator,
)
from tests.observability.stubs import (
    FakeEventQueue,
    FakeMessage,
    FakeRequestContext,
    FakeRequestHandler,
    FakeServerCallContext,
    FakeStreamResponse,
    make_artifact_event,
    make_final_event,
    make_send_request,
    make_status_event,
)

_EXTENSION_BASE = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/"
_TASK_T_URI = f"{_EXTENSION_BASE}Task-T"


class FakeSimpleHandler:
    """RequestHandler-shaped stub: on_message_send / on_get_task + decorator-touchable attrs."""

    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, Any, Any]] = []
        self.agent_executor: Any = None
        self._push_sender: Any = None
        self.observed_trace_id: str | None = None

    async def on_message_send(self, params: Any, context: Any) -> Any:
        self.calls.append(("on_message_send", params, context))
        span = current_span()
        self.observed_trace_id = span.trace_id if span is not None else None
        if self.error is not None:
            raise self.error
        return self.result

    async def on_get_task(self, params: Any, context: Any) -> Any:
        self.calls.append(("on_get_task", params, context))
        if self.error is not None:
            raise self.error
        return self.result


class FakeExecutor:
    """AgentExecutor-shaped stub enqueuing events into the queue handed to it."""

    def __init__(self, events: list[Any] | None = None) -> None:
        self.events = events or []
        self.received_queues: list[Any] = []
        self.observed_trace_id: str | None = None

    async def execute(self, context: Any, event_queue: Any) -> None:
        self.received_queues.append(event_queue)
        span = current_span()
        self.observed_trace_id = span.trace_id if span is not None else None
        for event in self.events:
            await event_queue.enqueue_event(event)

    async def cancel(self, context: Any, event_queue: Any) -> None:
        self.received_queues.append(event_queue)


class FakeStreamingHandler:
    """Simulates DefaultRequestHandler: creates its own queue, executor enqueues into it."""

    def __init__(self, executor: Any, error: BaseException | None = None) -> None:
        self.agent_executor = executor
        self._push_sender: Any = None
        self.error = error
        self.calls = 0

    async def on_message_send_stream(self, params: Any, context: Any) -> Any:
        self.calls += 1
        queue = FakeEventQueue()
        request_context = SimpleNamespace(
            task_id=params.message.task_id,
            context_id=params.message.context_id,
            call_context=context,
        )
        await self.agent_executor.execute(request_context, queue)
        if self.error is not None:
            raise self.error
        for event in list(queue.events):
            yield event


class GatedStreamingHandler(FakeStreamingHandler):
    """FakeStreamingHandler whose executor dispatch waits on a gate (concurrency control)."""

    def __init__(self, executor: Any, gate: asyncio.Event) -> None:
        super().__init__(executor)
        self._gate = gate

    async def on_message_send_stream(self, params: Any, context: Any) -> Any:
        await self._gate.wait()
        async for event in super().on_message_send_stream(params, context):
            yield event


class FakeSyncQueueHandler:
    """DefaultRequestHandler-shaped sync handler: creates a queue and hands it to the executor."""

    def __init__(self, executor: Any, result: Any = None) -> None:
        self.agent_executor = executor
        self._push_sender: Any = None
        self.result = result

    async def on_message_send(self, params: Any, context: Any) -> Any:
        queue = FakeEventQueue()
        request_context = SimpleNamespace(
            task_id=params.message.task_id,
            context_id=params.message.context_id,
            call_context=context,
        )
        await self.agent_executor.execute(request_context, queue)
        return self.result


class FakePushSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def send_notification(self, task_id: str, event: Any) -> None:
        self.calls.append((task_id, event))


class FakePushInfo:
    def __init__(self, url: str) -> None:
        self.url = url


class FakeDispatchPushSender:
    """BasePushNotificationSender-shaped stub: fans out one dispatch per push_info."""

    def __init__(self, urls: list[str]) -> None:
        self.push_infos = [FakePushInfo(url) for url in urls]
        self.dispatched: list[tuple[str, str]] = []

    async def send_notification(self, task_id: str, event: Any) -> None:
        for push_info in self.push_infos:
            await self._dispatch_notification(event, push_info, task_id)

    async def _dispatch_notification(self, event: Any, push_info: Any, task_id: str) -> bool:
        self.dispatched.append((task_id, push_info.url))
        return True


@pytest.fixture()
def exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    trace.set_tracer_provider(TracerProvider())
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


@pytest.fixture(autouse=True)
def _isolated_meter_provider() -> Iterator[InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    metrics_api.set_meter_provider(MeterProvider(metric_readers=[reader]))
    handler_decorator._METRIC_INSTRUMENTS.clear()
    _NEGOTIATION_METRIC_INSTRUMENTS.clear()
    yield reader
    handler_decorator._TASK_SPAN_REGISTRY.clear()


@pytest.fixture()
def metric_reader(_isolated_meter_provider: InMemoryMetricReader) -> InMemoryMetricReader:
    return _isolated_meter_provider


def _make_traceparent() -> tuple[str, Any]:
    tracer = trace.get_tracer("test-parent")
    parent = tracer.start_span("parent")
    span_context = parent.get_span_context()
    return f"00-{span_context.trace_id:032x}-{span_context.span_id:016x}-01", span_context


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
    if data is None:
        return []
    points: list[dict[str, Any]] = []
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == name:
                    points.extend(dict(point.attributes) for point in metric.data.data_points)
    return points


def _negotiation_points(reader: InMemoryMetricReader) -> list[Any]:
    data = reader.get_metrics_data()
    if data is None:
        return []
    points: list[Any] = []
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == "a2at.negotiation.total_rounds":
                    points.extend(metric.data.data_points)
    return points


async def test_on_message_send_server_entry_span(exporter: InMemorySpanExporter) -> None:
    traceparent, parent_span_context = _make_traceparent()
    params = make_send_request({_TASK_T_URI: "## 任务类型(Task Type)\n配置下发"}, task_id="T1", context_id="C1")
    inner = FakeSimpleHandler(result=FakeMessage(metadata={}, task_id="T1", context_id="C1"))
    decorator = A2ATRequestHandlerDecorator(inner)
    context = FakeServerCallContext(headers={"traceparent": traceparent})

    result = await decorator.on_message_send(params, context)

    assert result is inner.result
    assert inner.calls == [("on_message_send", params, context)]
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["SendMessage"]
    span = spans[0]
    assert span.kind == SpanKind.SERVER
    assert span.context.trace_id == parent_span_context.trace_id
    assert span.parent is not None
    assert span.parent.span_id == parent_span_context.span_id
    attrs = span.attributes or {}
    assert attrs[ATTR_GEN_AI_OPERATION_NAME] == "SendMessage"
    assert attrs[ATTR_A2A_OPERATION_NAME] == "SendMessage"
    assert attrs[ATTR_GEN_AI_CONVERSATION_ID] == "C1"
    assert attrs[ATTR_A2A_CONTEXT_ID] == "C1"
    assert attrs[ATTR_TASK_ID] == "T1"
    assert attrs[ATTR_A2A_TASK_ID] == "T1"
    assert attrs[ATTR_EXTENSION_NAME] == "Task-T"
    assert attrs[ATTR_TASK_TYPE] == "配置下发"
    assert attrs[ATTR_A2A_PARTS_NUMBER] == 0
    assert span.status.status_code == StatusCode.OK


async def test_current_span_is_server_entry_inside_handler(exporter: InMemorySpanExporter) -> None:
    traceparent, parent_span_context = _make_traceparent()
    inner = FakeSimpleHandler(result=FakeMessage(metadata={}, task_id="T1", context_id="C1"))
    decorator = A2ATRequestHandlerDecorator(inner)

    await decorator.on_message_send(
        make_send_request({}, task_id="T1", context_id="C1"),
        FakeServerCallContext(headers={"traceparent": traceparent}),
    )

    assert inner.observed_trace_id == format(parent_span_context.trace_id, "032x")


async def test_stream_entry_and_per_event_parent_spans(exporter: InMemorySpanExporter) -> None:
    events = [
        make_status_event("T9", "TASK_STATE_WORKING"),
        FakeStreamResponse("artifact_update", make_artifact_event("T9")),
        make_final_event("T9"),
    ]
    executor = FakeExecutor(events=events)
    inner = FakeStreamingHandler(executor)
    decorator = A2ATRequestHandlerDecorator(inner)
    params = make_send_request({}, task_id="T9", context_id="C9")

    collected = [event async for event in decorator.on_message_send_stream(params, FakeServerCallContext())]

    assert collected == events
    assert len(executor.received_queues) == 1
    assert not isinstance(executor.received_queues[0], FakeEventQueue)
    spans = exporter.get_finished_spans()
    entry = next(span for span in spans if span.name == "SendStreamingMessage")
    assert entry.kind == SpanKind.SERVER
    event_spans = [span for span in spans if span.name == "SendStreamingMessage-event"]
    assert len(event_spans) == 3
    for span in event_spans:
        assert span.kind == SpanKind.INTERNAL
        assert span.parent is not None
        assert span.parent.span_id == entry.context.span_id
        attrs = span.attributes or {}
        assert attrs[ATTR_GEN_AI_OPERATION_NAME] == "SendStreamingMessage"
        assert attrs[ATTR_GEN_AI_CONVERSATION_ID] == "C9"
    assert [(span.attributes or {})[ATTR_STREAMING_EVENT_KIND] for span in event_spans] == [
        "status",
        "artifact",
        "completed",
    ]
    status_attrs = event_spans[0].attributes or {}
    assert status_attrs[ATTR_TASK_ID] == "T9"
    assert status_attrs[ATTR_TASK_STATUS] == "working"


async def test_push_sender_span_links_to_entry(exporter: InMemorySpanExporter) -> None:
    executor = FakeExecutor(events=[make_status_event("T9", "TASK_STATE_WORKING")])
    inner = FakeStreamingHandler(executor)
    inner._push_sender = FakePushSender()
    decorator = A2ATRequestHandlerDecorator(inner)
    params = make_send_request({}, task_id="T9", context_id="C9")

    async for _ in decorator.on_message_send_stream(params, FakeServerCallContext()):
        pass
    await inner._push_sender.send_notification("T9", make_status_event("T9", "TASK_STATE_WORKING"))

    spans = exporter.get_finished_spans()
    entry = next(span for span in spans if span.name == "SendStreamingMessage")
    push = next(span for span in spans if span.name == "SendMessage-pushNotification")
    assert push.kind == SpanKind.CLIENT
    links = push.links or ()
    assert any(link.context.span_id == entry.context.span_id for link in links)
    attrs = push.attributes or {}
    assert attrs[ATTR_TASK_ID] == "T9"
    assert attrs[ATTR_STREAMING_EVENT_KIND] == "status"


async def test_executor_decorator_delegates_cancel_and_attrs() -> None:
    executor = FakeExecutor()
    executor.custom_field = "business-value"
    decorator = A2ATAgentExecutorDecorator(executor)

    assert decorator.custom_field == "business-value"
    await decorator.cancel(SimpleNamespace(task_id="T1"), FakeEventQueue())
    assert len(executor.received_queues) == 1


async def test_push_sender_decorator_wraps_send_notification() -> None:
    sender = FakePushSender()
    decorator = A2ATPushSenderDecorator(sender)

    await decorator.send_notification("T1", make_status_event("T1", "TASK_STATE_WORKING"))

    assert sender.calls == [("T1", sender.calls[0][1])]


async def test_get_task_creates_named_server_span(exporter: InMemorySpanExporter) -> None:
    inner = FakeSimpleHandler(result=SimpleNamespace(id="T8"))
    decorator = A2ATRequestHandlerDecorator(inner)

    result = await decorator.on_get_task(SimpleNamespace(id="T8"), FakeServerCallContext())

    assert result is inner.result
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["GetTask"]
    assert spans[0].kind == SpanKind.SERVER
    assert (spans[0].attributes or {})[ATTR_GEN_AI_OPERATION_NAME] == "GetTask"


async def test_getattr_delegates_to_inner() -> None:
    inner = FakeSimpleHandler()
    inner.custom_field = "business-value"
    decorator = A2ATRequestHandlerDecorator(inner)

    assert decorator.custom_field == "business-value"


async def test_report_negotiation_rounds_records_histogram(metric_reader: InMemoryMetricReader) -> None:
    report_negotiation_rounds("N1", "accept", 3)

    points = _negotiation_points(metric_reader)
    assert len(points) == 1
    point = points[0]
    assert point.count == 1
    assert point.sum == 3
    attrs = dict(point.attributes)
    assert attrs["gen_ai.agent.a2at.negotiation.id"] == "N1"
    assert attrs["outcome"] == "accept"
    assert attrs[ATTR_EXTENSION_NAME] == "Negotiation-T"


async def test_negotiation_metric_disabled_records_nothing(
    metric_reader: InMemoryMetricReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_otel_compat, "_METRIC_ENABLED", False)

    report_negotiation_rounds("N1", "accept", 3)

    assert _metric_names(metric_reader) == set()


async def test_trace_disabled_no_spans_but_metrics_and_logs(
    exporter: InMemorySpanExporter,
    metric_reader: InMemoryMetricReader,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§8.1: A2AT_TRACE_ENABLED=false → zero spans; metrics + logs still recorded."""
    monkeypatch.setattr(_otel_compat, "_TRACE_ENABLED", False)
    inner = FakeSimpleHandler(result=FakeMessage(metadata={}, task_id="T5", context_id="C5"))
    decorator = A2ATRequestHandlerDecorator(inner)
    assert (
        await decorator.on_message_send(make_send_request({}, task_id="T5", context_id="C5"), FakeServerCallContext())
        is inner.result
    )

    events = [make_status_event("T5", "TASK_STATE_WORKING"), make_final_event("T5")]
    executor = FakeExecutor(events=events)
    streaming_inner = FakeStreamingHandler(executor)
    streaming_decorator = A2ATRequestHandlerDecorator(streaming_inner)
    with caplog.at_level(logging.DEBUG, logger="a2at.observability"):
        collected = [
            event
            async for event in streaming_decorator.on_message_send_stream(
                make_send_request({}, task_id="T5", context_id="C5"), FakeServerCallContext()
            )
        ]

    assert collected == events
    assert not exporter.get_finished_spans()
    names = _metric_names(metric_reader)
    assert "a2at.task.request.duration" in names
    points = _histogram_point_attributes(metric_reader, "a2at.task.request.duration")
    assert points
    assert any("task.status_changed" in record.getMessage() for record in caplog.records)


async def test_config_enabled_false_full_pass_through(
    exporter: InMemorySpanExporter, metric_reader: InMemoryMetricReader
) -> None:
    config = A2ATObservabilityConfig(enabled=False)
    inner = FakeSimpleHandler(result=FakeMessage(metadata={}, task_id="T6", context_id="C6"))
    decorator = A2ATRequestHandlerDecorator(inner, config=config)

    result = await decorator.on_message_send(
        make_send_request({}, task_id="T6", context_id="C6"), FakeServerCallContext()
    )

    assert result is inner.result
    assert not exporter.get_finished_spans()
    assert _metric_names(metric_reader) == set()


async def test_decorator_failure_never_breaks_business_flow(
    exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ExplodingTracer:
        def start_span(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("otel exploded")

    monkeypatch.setattr(_otel_compat, "get_tracer", lambda *args, **kwargs: ExplodingTracer())
    inner = FakeSimpleHandler(result=FakeMessage(metadata={}, task_id="T10", context_id="C10"))
    decorator = A2ATRequestHandlerDecorator(inner)

    result = await decorator.on_message_send(
        make_send_request({}, task_id="T10", context_id="C10"), FakeServerCallContext()
    )

    assert result is inner.result
    assert not exporter.get_finished_spans()


async def test_stream_error_sets_error_status_and_propagates(exporter: InMemorySpanExporter) -> None:
    executor = FakeExecutor(events=[make_status_event("T4", "TASK_STATE_WORKING")])
    inner = FakeStreamingHandler(executor, error=RuntimeError("stream boom"))
    decorator = A2ATRequestHandlerDecorator(inner)

    with pytest.raises(RuntimeError, match="stream boom"):
        async for _ in decorator.on_message_send_stream(
            make_send_request({}, task_id="T4", context_id="C4"), FakeServerCallContext()
        ):
            pass

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["SendStreamingMessage"].status.status_code == StatusCode.ERROR


async def test_sync_send_message_no_phantom_event_spans(
    exporter: InMemorySpanExporter, caplog: pytest.LogCaptureFixture
) -> None:
    """Sync on_message_send also routes a queue through the executor (DefaultRequestHandler):
    the wrapped queue must NOT emit SendStreamingMessage-event spans for the sync operation."""
    events = [make_status_event("T1", "TASK_STATE_WORKING"), make_final_event("T1")]
    executor = FakeExecutor(events=events)
    inner = FakeSyncQueueHandler(executor, result=FakeMessage(metadata={}, task_id="T1", context_id="C1"))
    decorator = A2ATRequestHandlerDecorator(inner)
    params = make_send_request({}, task_id="T1", context_id="C1")

    with caplog.at_level(logging.INFO, logger="a2at.observability"):
        result = await decorator.on_message_send(params, FakeServerCallContext())

    assert result is inner.result
    assert [span.name for span in exporter.get_finished_spans()] == ["SendMessage"]
    assert not isinstance(executor.received_queues[0], FakeEventQueue)
    assert any("task.status_changed" in record.getMessage() for record in caplog.records)


async def test_two_concurrent_same_context_id_requests_no_stale_parent(exporter: InMemorySpanExporter) -> None:
    """Concurrent requests sharing a context_id (no client task_id): the context_id registry
    key is overwritten by the newer request; a late executor dispatch must not adopt the
    stale record whose entry span has already ended."""
    shared = "C-shared"
    gate_a = asyncio.Event()
    events_a = [make_status_event("T-a", "TASK_STATE_WORKING"), make_final_event("T-a")]
    events_b = [make_status_event("T-b", "TASK_STATE_WORKING"), make_final_event("T-b")]

    decorator_a = A2ATRequestHandlerDecorator(GatedStreamingHandler(FakeExecutor(events=events_a), gate_a))
    decorator_b = A2ATRequestHandlerDecorator(FakeStreamingHandler(FakeExecutor(events=events_b)))
    params_a = make_send_request({}, task_id="", context_id=shared)
    params_b = make_send_request({}, task_id="", context_id=shared)

    async def collect(decorator: A2ATRequestHandlerDecorator, params: Any) -> list[Any]:
        return [event async for event in decorator.on_message_send_stream(params, FakeServerCallContext())]

    task_a = asyncio.create_task(collect(decorator_a, params_a))
    await asyncio.sleep(0)  # A registers its entry record, then blocks on the gate
    task_b = asyncio.create_task(collect(decorator_b, params_b))
    await asyncio.sleep(0)  # B registers (overwrites the shared key) and completes

    finished = exporter.get_finished_spans()
    assert [span.name for span in finished if span.name == "SendStreamingMessage"] == ["SendStreamingMessage"]
    assert all(
        (span.attributes or {}).get(ATTR_TASK_ID) == "T-b"
        for span in finished
        if span.name == "SendStreamingMessage-event"
    )

    gate_a.set()
    collected_a, collected_b = await asyncio.gather(task_a, task_b)

    assert collected_a == events_a
    assert collected_b == events_b
    spans = exporter.get_finished_spans()
    entries = [span for span in spans if span.name == "SendStreamingMessage"]
    assert len(entries) == 2
    event_spans = [span for span in spans if span.name == "SendStreamingMessage-event"]
    assert all((span.attributes or {}).get(ATTR_TASK_ID) == "T-b" for span in event_spans)
    entry_b = next(span for span in entries if (span.attributes or {}).get(ATTR_A2A_CONTEXT_ID) == shared)
    for span in event_spans:
        assert span.parent is not None
        assert span.parent.span_id == entry_b.context.span_id


async def test_push_dispatch_span_per_fanout_carries_push_info_url(exporter: InMemorySpanExporter) -> None:
    """BasePushNotificationSender shape: one CLIENT span per fanout push_info with
    ``push.notification.url`` taken from push_info (not the dead event .url)."""
    executor = FakeExecutor(events=[make_status_event("T9", "TASK_STATE_WORKING")])
    inner = FakeStreamingHandler(executor)
    sender = FakeDispatchPushSender(["https://hook-two", "https://hook-one"])
    inner._push_sender = sender
    decorator = A2ATRequestHandlerDecorator(inner)
    params = make_send_request({}, task_id="T9", context_id="C9")

    async for _ in decorator.on_message_send_stream(params, FakeServerCallContext()):
        pass
    await inner._push_sender.send_notification("T9", make_status_event("T9", "TASK_STATE_WORKING"))

    spans = exporter.get_finished_spans()
    entry = next(span for span in spans if span.name == "SendStreamingMessage")
    push_spans = [span for span in spans if span.name == "SendMessage-pushNotification"]
    assert len(push_spans) == 2
    urls = sorted((span.attributes or {}).get(ATTR_PUSH_NOTIFICATION_URL) for span in push_spans)
    assert urls == ["https://hook-one", "https://hook-two"]
    for span in push_spans:
        assert span.kind == SpanKind.CLIENT
        assert (span.attributes or {})[ATTR_TASK_ID] == "T9"
        links = span.links or ()
        assert any(link.context.span_id == entry.context.span_id for link in links)
    assert sender.dispatched == [("T9", "https://hook-two"), ("T9", "https://hook-one")]


async def test_push_sender_without_dispatch_notification_still_wraps_abc_surface(
    exporter: InMemorySpanExporter,
) -> None:
    """Senders without ``_dispatch_notification`` keep the ABC-surface wrap (span + LINK)."""
    executor = FakeExecutor(events=[make_status_event("T9", "TASK_STATE_WORKING")])
    inner = FakeStreamingHandler(executor)
    inner._push_sender = FakePushSender()
    decorator = A2ATRequestHandlerDecorator(inner)
    params = make_send_request({}, task_id="T9", context_id="C9")

    async for _ in decorator.on_message_send_stream(params, FakeServerCallContext()):
        pass
    await inner._push_sender.send_notification("T9", make_status_event("T9", "TASK_STATE_WORKING"))

    spans = exporter.get_finished_spans()
    entry = next(span for span in spans if span.name == "SendStreamingMessage")
    push = next(span for span in spans if span.name == "SendMessage-pushNotification")
    assert push.kind == SpanKind.CLIENT
    assert any(link.context.span_id == entry.context.span_id for link in push.links or ())
    assert ATTR_PUSH_NOTIFICATION_URL not in (push.attributes or {})


async def test_wrap_collaborators_idempotent_and_dispatch_not_double_wrapped() -> None:
    inner = FakeStreamingHandler(FakeExecutor())
    A2ATRequestHandlerDecorator(inner)
    assert isinstance(inner.agent_executor, A2ATAgentExecutorDecorator)
    wrapped_executor = inner.agent_executor

    A2ATRequestHandlerDecorator(inner)
    assert inner.agent_executor is wrapped_executor

    inner._push_sender = FakeDispatchPushSender(["https://hook"])
    A2ATRequestHandlerDecorator(inner)
    assert not isinstance(inner._push_sender, A2ATPushSenderDecorator)
    wrapped_dispatch = inner._push_sender._dispatch_notification
    assert getattr(wrapped_dispatch, "_a2at_wrapped", False) is True

    A2ATRequestHandlerDecorator(inner)
    assert inner._push_sender._dispatch_notification is wrapped_dispatch


async def test_metric_disabled_records_no_metrics_but_spans(
    exporter: InMemorySpanExporter,
    metric_reader: InMemoryMetricReader,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§8.1: A2AT_METRIC_ENABLED=false → no metric samples; spans keep working."""
    monkeypatch.setattr(_otel_compat, "_METRIC_ENABLED", False)
    inner = FakeSimpleHandler(result=FakeMessage(metadata={}, task_id="T20", context_id="C20"))
    decorator = A2ATRequestHandlerDecorator(inner)

    result = await decorator.on_message_send(
        make_send_request({}, task_id="T20", context_id="C20"), FakeServerCallContext()
    )

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
    events = [make_status_event("T21", "TASK_STATE_WORKING"), make_final_event("T21")]
    inner = FakeSyncQueueHandler(
        FakeExecutor(events=events), result=FakeMessage(metadata={}, task_id="T21", context_id="C21")
    )
    decorator = A2ATRequestHandlerDecorator(inner)

    with caplog.at_level(logging.DEBUG, logger="a2at.observability"):
        result = await decorator.on_message_send(
            make_send_request({}, task_id="T21", context_id="C21"), FakeServerCallContext()
        )

    assert result is inner.result
    assert [span.name for span in exporter.get_finished_spans()] == ["SendMessage"]
    assert "a2at.task.request.duration" in _metric_names(metric_reader)
    assert not [
        record
        for record in caplog.records
        if "task.status_changed" in record.getMessage() or "task.artifact" in record.getMessage()
    ]


async def test_traceparent_extracted_from_request_context_fallback(exporter: InMemorySpanExporter) -> None:
    """RequestContext-shaped context: traceparent read from ``call_context.state["headers"]``."""
    traceparent, parent_span_context = _make_traceparent()
    message = FakeMessage(metadata={}, task_id="T22", context_id="C22")
    inner = FakeSimpleHandler(result=message)
    decorator = A2ATRequestHandlerDecorator(inner)
    context = FakeRequestContext(message, headers={"traceparent": traceparent})

    result = await decorator.on_message_send(make_send_request({}, task_id="T22", context_id="C22"), context)

    assert result is inner.result
    span = exporter.get_finished_spans()[0]
    assert span.parent is not None
    assert span.parent.span_id == parent_span_context.span_id
    assert span.context.trace_id == parent_span_context.trace_id


async def test_payload_attributes_via_env_switch(
    exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A2AT_EXTRACT_REQUEST/RESPONSE env switches → payload attrs on the SERVER entry span."""
    monkeypatch.setenv("A2AT_EXTRACT_REQUEST", "true")
    monkeypatch.setenv("A2AT_EXTRACT_RESPONSE", "true")
    inner = FakeSimpleHandler(result=FakeMessage(metadata={}, task_id="T23", context_id="C23"))
    decorator = A2ATRequestHandlerDecorator(inner)

    result = await decorator.on_message_send(
        make_send_request({}, task_id="T23", context_id="C23"), FakeServerCallContext()
    )

    assert result is inner.result
    attrs = exporter.get_finished_spans()[-1].attributes or {}
    assert ATTR_A2A_REQUEST in attrs
    assert ATTR_A2A_RESPONSE in attrs


@pytest.mark.parametrize(
    ("method", "span_name"),
    [
        ("on_list_tasks", "ListTasks"),
        ("on_cancel_task", "CancelTask"),
        ("on_create_task_push_notification_config", "CreateTaskPushNotificationConfig"),
        ("on_get_task_push_notification_config", "GetTaskPushNotificationConfig"),
        ("on_list_task_push_notification_configs", "ListTaskPushNotificationConfigs"),
        ("on_delete_task_push_notification_config", "DeleteTaskPushNotificationConfig"),
        ("on_get_extended_agent_card", "GetExtendedAgentCard"),
    ],
)
async def test_simple_methods_create_named_server_spans(
    exporter: InMemorySpanExporter, method: str, span_name: str
) -> None:
    inner = FakeRequestHandler(result=SimpleNamespace(id="T24"))
    decorator = A2ATRequestHandlerDecorator(inner)

    result = await getattr(decorator, method)(SimpleNamespace(id="T24"), FakeServerCallContext())

    assert result is inner.result
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == [span_name]
    assert spans[0].kind == SpanKind.SERVER
    assert (spans[0].attributes or {})[ATTR_GEN_AI_OPERATION_NAME] == span_name


async def test_subscribe_to_task_stream_entry_span(exporter: InMemorySpanExporter) -> None:
    """on_subscribe_to_task: re-attach stream → single SERVER entry span, no per-event spans."""
    inner = FakeRequestHandler(events=[make_status_event("T25", "TASK_STATE_WORKING"), make_final_event("T25")])
    decorator = A2ATRequestHandlerDecorator(inner)
    params = SimpleNamespace(task_id="T25")

    collected = [event async for event in decorator.on_subscribe_to_task(params, FakeServerCallContext())]

    assert collected == inner.events
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["SubscribeToTask"]
    assert spans[0].kind == SpanKind.SERVER
    assert spans[0].status.status_code == StatusCode.OK


async def test_v2_handler_emits_one_time_degradation_warning(caplog: pytest.LogCaptureFixture) -> None:
    """a2a-sdk 1.1.2+ V2 DefaultRequestHandler (``_active_task_registry``): post-construction
    executor/push wraps are silently bypassed — the decorator warns once per instance."""
    inner = FakeSimpleHandler(result=FakeMessage(metadata={}, task_id="T26", context_id="C26"))
    inner._active_task_registry = object()

    with caplog.at_level(logging.WARNING, logger="a2at.observability"):
        decorator = A2ATRequestHandlerDecorator(inner)
        decorator._wrap_collaborators()  # re-wrap within the same instance: warning stays one-time

    warnings = [record for record in caplog.records if "LegacyRequestHandler" in record.getMessage()]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING


async def test_legacy_handler_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    """LegacyRequestHandler-shaped inners (no ``_active_task_registry``) stay silent."""
    inner = FakeSimpleHandler(result=FakeMessage(metadata={}, task_id="T27", context_id="C27"))

    with caplog.at_level(logging.WARNING, logger="a2at.observability"):
        A2ATRequestHandlerDecorator(inner)

    assert not [record for record in caplog.records if "LegacyRequestHandler" in record.getMessage()]
