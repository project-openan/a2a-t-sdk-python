"""A2ATClientTransportDecorator tests: span naming, links/parent, stash, metrics, degradation."""

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
    ATTR_A2A_REQUEST,
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
    NEGOTIATION_EXTENSION,
    NEGOTIATION_SUFFIX,
    is_negotiation_message,
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


async def test_sync_negotiation_log_fires_when_trace_off(caplog: pytest.LogCaptureFixture) -> None:
    """§8.1/§4.2：sync 路径 trace-off 时协商日志仍发（entry span 缺席不吞日志）。"""
    import logging

    config = A2ATObservabilityConfig(trace_enabled=False)
    result = FakeMessage(
        {
            _NEGOTIATION_T_URI: "final accept",
            "negotiationContext": {"id": "N9", "round": 3, "maxRounds": 5, "performative": "ACCEPT"},
        },
        task_id="T26",
        context_id="C26",
    )
    inner = FakeSyncTransport(result=result)
    decorator = A2ATClientTransportDecorator(inner, config=config)

    with caplog.at_level(logging.DEBUG, logger="a2at.observability"):
        await decorator.send_message(make_send_request({}, task_id="T26", context_id="C26"), context=FakeContext())

    assert any("negotiation.message" in record.getMessage() for record in caplog.records)


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
    task_points = _histogram_point_attributes(metric_reader, "a2at.task.request.duration")
    assert task_points
    assert all(p.get("a2at.span.side") == "client" for p in task_points)
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
        # D7: 同 trace、无父子、LINK 关联入口
        assert span.context.trace_id == entry.context.trace_id
        assert span.parent is None or span.parent.span_id != entry.context.span_id
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


async def test_streaming_continues_after_negotiation_message_until_terminal(
    exporter: InMemorySpanExporter,
) -> None:
    """a2a-java 对齐（2026-09-30 addendum D5）：流内 Message 不终止消费。

    协商 Message 之后的 completed 事件继续投递给客户端；流仅由终态事件
    （final status / 终态 Task 快照）结束——对齐 a2a-java
    ``AbstractSSEEventListener.shouldAutoClose``。
    """
    metadata = {
        _NEGOTIATION_T_URI: "counter-offer",
        "negotiationContext": {"id": "N5", "round": 2, "maxRounds": 5, "performative": "PROPOSE"},
    }
    message = FakeMessage(metadata, task_id="T18", context_id="C18")
    completed = make_final_event("T18")
    inner = FakeStreamingTransport([message, completed])
    decorator = A2ATClientTransportDecorator(inner)
    request = make_send_request({}, task_id="T18", context_id="C18")

    collected = [event async for event in decorator.send_message_streaming(request, context=FakeContext())]

    assert collected == [message, completed]
    spans = exporter.get_finished_spans()
    negotiations = [span for span in spans if span.name.endswith("-negotiation")]
    assert len(negotiations) == 1
    assert negotiations[0].parent is not None
    event_spans = [span for span in spans if span.name == "SendStreamingMessage-event"]
    assert len(event_spans) == 1
    assert (event_spans[0].attributes or {})[ATTR_TASK_STATUS] == "completed"


async def test_entry_span_context_restored_when_detach_fails(
    exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生产实测（多轮协商）：a2a-sdk 跨上下文消费 SSE 使 OTel ``detach`` 失败且被吞
    （"Token was created in a different Context"），上一轮 entry span 泄漏为 current，
    下一轮 entry 串进同一条 trace。客户端 entry span 恒为 trace 根（显式
    INVALID_SPAN_CONTEXT 隔离），ambient 污染（无论泄漏还是编排根遗留）都不影响；
    跨请求关联走属性（negotiation.id，spec 6.3 模式 A）。
    """
    from opentelemetry.trace import use_span

    first = A2ATClientTransportDecorator(FakeStreamingTransport([FakeMessage({}, task_id="T21", context_id="C21")]))
    _ = [
        event
        async for event in first.send_message_streaming(
            make_send_request({}, task_id="T21", context_id="C21"), context=FakeContext()
        )
    ]
    first_entry = next(span for span in exporter.get_finished_spans() if span.name == "SendStreamingMessage")

    import opentelemetry.context as otel_context

    def flaky_detach(token: Any) -> None:
        # 只让第一处 detach（本 SDK cm 的 finally）失败，模拟生产泄漏路径；
        # 测试自身 use_span 的退出 detach 放行。
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("Token was created in a different Context")
        real_detach(token)

    real_detach = otel_context.detach
    calls = {"n": 0}
    monkeypatch.setattr(otel_context, "detach", flaky_detach)

    # 模拟最恶劣环境：detach 必失败 + ambient 残留上一轮 entry span
    with use_span(first_entry, end_on_exit=False):
        second = A2ATClientTransportDecorator(
            FakeStreamingTransport([FakeMessage({}, task_id="T22", context_id="C22")])
        )
        _ = [
            event
            async for event in second.send_message_streaming(
                make_send_request({}, task_id="T22", context_id="C22"), context=FakeContext()
            )
        ]

    second_entry = next(
        span
        for span in exporter.get_finished_spans()
        if span.name == "SendStreamingMessage" and span.context.span_id != first_entry.context.span_id
    )
    assert second_entry.context.trace_id != first_entry.context.trace_id
    assert second_entry.parent is None


class AbortTrackingTransport:
    """FakeStreamingTransport that records whether its stream generator was closed."""

    def __init__(self, events: list[Any]) -> None:
        self.events = events
        self.closed = False

    async def send_message_streaming(self, request: Any, *, context: Any = None) -> Any:
        try:
            for event in self.events:
                yield event
        finally:
            self.closed = True

    async def send_message(self, request: Any, *, context: Any = None) -> Any:
        return self.events[-1] if self.events else None

    def close(self) -> None:
        pass


async def test_consumer_abort_closes_inner_stream(exporter: InMemorySpanExporter) -> None:
    """消费者中途放弃流（break + aclose）时，内层流必须被确定性关闭——
    不能等 asyncgen GC 兜底（长程异步任务的资源纪律）。"""
    inner = AbortTrackingTransport([make_status_event("T27", "TASK_STATE_WORKING"), make_final_event("T27")])
    decorator = A2ATClientTransportDecorator(inner)
    request = make_send_request({}, task_id="T27", context_id="C27")

    gen = decorator.send_message_streaming(request, context=FakeContext())
    received = 0
    async for _event in gen:
        received += 1
        if received == 1:
            break
    assert received == 1
    assert not inner.closed  # 消费者 break 本身不会立即关闭内层流
    await gen.aclose()
    assert inner.closed  # aclose 后内层流被确定性关闭（aclosing）
    # M1: consumer abort（GeneratorExit）不是成功路径——entry span 必须已结束
    # 且状态保持 UNSET（不得记为 OK 污染成功率统计）。
    entry = next(span for span in exporter.get_finished_spans() if span.name == "SendStreamingMessage")
    assert entry.status.status_code == StatusCode.UNSET


async def test_cancelled_send_message_marks_span_error(exporter: InMemorySpanExporter) -> None:
    """M1: asyncio.CancelledError 是 BaseException（不继承 Exception）——被取消的请求
    不得记为 OK。entry span 必须标 ERROR + "cancelled"，异常照常向上传播。"""
    inner = FakeSyncTransport(error=asyncio.CancelledError())
    decorator = A2ATClientTransportDecorator(inner)
    request = make_send_request({}, task_id="T28", context_id="C28")

    with pytest.raises(asyncio.CancelledError):
        await decorator.send_message(request, context=FakeContext())

    entry = next(span for span in exporter.get_finished_spans() if span.name == "SendMessage")
    assert entry.status.status_code == StatusCode.ERROR
    assert entry.status.description == "cancelled"


async def test_entry_span_child_of_caller_traceparent(exporter: InMemorySpanExporter) -> None:
    """显式传播（spec 6.3 模式 B）：调用方在 service_parameters 预置 traceparent →
    entry span 成为该 span 的子 span（整个业务流程一条 trace，多轮协商 + 任务执行）；
    未预置 → entry 恒为 ROOT（模式 A，防 a2a-sdk 跨上下文 detach 泄漏串链）。
    """
    from opentelemetry import trace as otel_trace

    from a2a_t.observability.propagation import inject_traceparent

    tracer = otel_trace.get_tracer("test")
    root_span = tracer.start_span("business-root")

    headers: dict[str, str] = {}
    inject_traceparent(headers, context=otel_trace.set_span_in_context(root_span))
    context = FakeContext()
    context.service_parameters.update(headers)

    decorator = A2ATClientTransportDecorator(FakeStreamingTransport([make_final_event("T23")]))
    _ = [
        event
        async for event in decorator.send_message_streaming(
            make_send_request({}, task_id="T23", context_id="C23"), context=context
        )
    ]
    root_span.end()

    entry = next(span for span in exporter.get_finished_spans() if span.name == "SendStreamingMessage")
    assert entry.parent is not None
    assert entry.parent.span_id == root_span.get_span_context().span_id
    assert entry.context.trace_id == root_span.get_span_context().trace_id


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


async def test_metric_config_disabled_records_no_metrics_but_spans(
    exporter: InMemorySpanExporter, metric_reader: InMemoryMetricReader
) -> None:
    """M2: 实例级 A2ATObservabilityConfig(metric_enabled=False) 与全局 env 开关同等生效
    （对齐 trace/log 的双重检查——旧行为只查全局开关，实例开关形同虚设）；spans 照常。"""
    config = A2ATObservabilityConfig(metric_enabled=False)
    inner = FakeSyncTransport(result=_message_response())
    decorator = A2ATClientTransportDecorator(inner, config=config)

    result = await decorator.send_message(make_send_request({}, task_id="T23", context_id="C23"), context=FakeContext())

    assert result is inner.result
    assert [span.name for span in exporter.get_finished_spans()] == ["SendMessage"]
    assert _metric_names(metric_reader) == set()


async def test_span_payload_attribute_redacts_before_truncation(exporter: InMemorySpanExporter) -> None:
    """M3: span 属性通道遵循“先脱敏后截断”——跨截断边界的 secret 不得以部分明文
    进入 span 属性（旧实现先截断：secret 被切成两半后 redactor 完整模式无法命中）。"""
    secret = "sk-abcdef0123456789"

    class SecretRequest:
        def __str__(self) -> str:
            return "x" * 18 + secret + "y" * 10

    config = A2ATObservabilityConfig(
        extract_request=True,
        payload_log_max_length=30,
        payload_redactor=lambda s: s.replace(secret, "***"),
    )
    inner = FakeSyncTransport(result=_message_response())
    decorator = A2ATClientTransportDecorator(inner, config=config)

    await decorator.send_message(SecretRequest(), context=FakeContext())

    entry = next(span for span in exporter.get_finished_spans() if span.name == "SendMessage")
    attr = (entry.attributes or {})[ATTR_A2A_REQUEST]
    assert "sk-" not in attr
    assert attr.endswith("[truncated]")


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


def test_is_negotiation_message_detects_extension_uri() -> None:
    assert is_negotiation_message(FakeMessage({_NEGOTIATION_T_URI: "offer"})) is True


def test_is_negotiation_message_rejects_plain_metadata() -> None:
    assert is_negotiation_message(FakeMessage({_TASK_T_URI: "regular"})) is False
    assert is_negotiation_message(FakeMessage({})) is False


def test_negotiation_constants_values() -> None:
    assert NEGOTIATION_EXTENSION == "Negotiation-T"
    assert NEGOTIATION_SUFFIX == "-negotiation"
