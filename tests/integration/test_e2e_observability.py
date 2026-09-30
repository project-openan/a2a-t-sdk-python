"""End-to-end observability verification for the v3.0 decorator architecture (spec §10.2, assertions 1-10).

In-process wiring: httpx ASGITransport -> Starlette (a2a-python REST routes) ->
A2ATRequestHandlerDecorator(LegacyRequestHandler(...)) on the server and a real
a2a ClientFactory client with register_client_factory(factory) on the client —
zero business observability code. No ASGI middleware: the server entry span
extracts the traceparent from ``ServerCallContext.state["headers"]`` (spec
appendix #5), which the client transport decorator injected into
``context.service_parameters``.

Requires a2a-sdk integration deps (marker ``a2a``; opt-in via A2AT_TEST_A2A=1).
Verified against a2a-sdk 1.1.2 structural reality (differs from the unit-test
stubs):

- LegacyRequestHandler instead of DefaultRequestHandlerV2: the V2 handler's
  ActiveTaskRegistry captures the raw agent_executor/push_sender references at
  construction, so post-construction structural wrapping (the executor queue
  wrap and the push ``_dispatch_notification`` wrap) would be bypassed;
  LegacyRequestHandler resolves ``self.agent_executor`` / ``self._push_sender``
  per call, which is what the decorator replaces.
- Streaming transport/handler methods are async generators: a2a's trace_class
  applies its sync wrapper, so a2a's own protocol spans end at generator
  creation. Because ASGITransport runs the server app inside the client entry
  span activation, ambient propagation coincides with the traceparent path —
  both route through the same client entry span.
- TaskStatusUpdateEvent has no ``final`` field; the terminal state itself is
  final.

Assertion coverage (spec §10.2 v3 table):

1/2/3/8 -> test_e2e_trace_continuity_event_spans_and_metrics
4       -> test_e2e_stream_error_span
5       -> test_e2e_negotiation_span
6       -> test_e2e_sync_message_response_attributes_no_extra_spans
7       -> test_e2e_push_notification_span
9       -> test_e2e_payload_attributes_and_log_trace_context
10      -> test_e2e_master_switch_off
wiring  -> test_register_client_factory_decorates_real_factory_transport
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from opentelemetry.trace import SpanKind, StatusCode

pytestmark = [
    pytest.mark.a2a,
    pytest.mark.skipif(not os.environ.get("A2AT_TEST_A2A"), reason="set A2AT_TEST_A2A=1 (requires integration deps)"),
]

_EXTENSION_BASE = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/"
_TASK_T_URI = f"{_EXTENSION_BASE}Task-T"
_NEG_T_URI = f"{_EXTENSION_BASE}Negotiation-T"

_A2AT_SPAN_NAMES = frozenset(
    {
        "SendMessage",
        "SendStreamingMessage",
        "SendStreamingMessage-event",
        "SendStreamingMessage-error",
        "SendStreamingMessage-negotiation",
        "SendMessage-pushNotification",
    }
)


def _agent_card(push: bool) -> Any:
    from a2a.types.a2a_pb2 import AgentCard, AgentInterface

    card = AgentCard(
        name="obs-server",
        description="observability e2e server",
        version="1.0",
        supported_interfaces=[
            AgentInterface(protocol_binding="HTTP+JSON", protocol_version="1.0", url="http://testserver")
        ],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[],
    )
    card.capabilities.streaming = True
    if push:
        card.capabilities.push_notifications = True
    return card


def _build_app(executor_kind: str, *, config: Any = None, push: bool = False) -> tuple[Any, Any]:
    """Starlette app: A2ATRequestHandlerDecorator(LegacyRequestHandler(...)) + a2a REST routes."""
    import httpx
    from a2a.server.agent_execution import AgentExecutor
    from a2a.server.request_handlers.default_request_handler import LegacyRequestHandler
    from a2a.server.routes import create_agent_card_routes, create_rest_routes
    from a2a.server.tasks.base_push_notification_sender import BasePushNotificationSender
    from a2a.server.tasks.inmemory_push_notification_config_store import InMemoryPushNotificationConfigStore
    from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
    from a2a.types.a2a_pb2 import (
        Artifact,
        Message,
        Role,
        TaskArtifactUpdateEvent,
        TaskState,
        TaskStatus,
        TaskStatusUpdateEvent,
    )
    from starlette.applications import Starlette

    from a2a_t.observability import A2ATRequestHandlerDecorator

    class FlowExecutor(AgentExecutor):
        async def execute(self, context: Any, event_queue: Any) -> None:
            task_id = context.task_id
            context_id = context.context_id
            for state in (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING):
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(task_id=task_id, context_id=context_id, status=TaskStatus(state=state))
                )
            artifact = Artifact(artifact_id="A1", name="e2e.artifact")
            artifact.parts.add().text = "result-data"
            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(task_id=task_id, context_id=context_id, artifact=artifact)
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id, context_id=context_id, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)
                )
            )

        async def cancel(self, context: Any, event_queue: Any) -> None: ...

    class MessageExecutor(AgentExecutor):
        async def execute(self, context: Any, event_queue: Any) -> None:
            message = Message(message_id="m-reply", role=Role.ROLE_AGENT, context_id=context.context_id or "")
            message.parts.add().text = "reply-data"
            message.metadata[_TASK_T_URI] = "reply"
            await event_queue.enqueue_event(message)

        async def cancel(self, context: Any, event_queue: Any) -> None: ...

    class NegotiationExecutor(AgentExecutor):
        async def execute(self, context: Any, event_queue: Any) -> None:
            message = Message(message_id="m-neg", role=Role.ROLE_AGENT, context_id=context.context_id or "")
            message.parts.add().text = "counter-offer"
            message.metadata[_NEG_T_URI] = "negotiation"
            message.metadata["negotiationContext"] = {
                "id": "N-e2e",
                "round": 3,
                "maxRounds": 5,
                "performative": "ACCEPT",
            }
            await event_queue.enqueue_event(message)

        async def cancel(self, context: Any, event_queue: Any) -> None: ...

    class FailingExecutor(AgentExecutor):
        async def execute(self, context: Any, event_queue: Any) -> None:
            raise RuntimeError("e2e boom")

        async def cancel(self, context: Any, event_queue: Any) -> None: ...

    executors = {
        "flow": FlowExecutor(),
        "message": MessageExecutor(),
        "negotiation": NegotiationExecutor(),
        "fail": FailingExecutor(),
    }
    agent_card = _agent_card(push=push)

    handler_kwargs: dict[str, Any] = {}
    if push:
        push_store = InMemoryPushNotificationConfigStore()
        push_http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
        )
        handler_kwargs["push_config_store"] = push_store
        handler_kwargs["push_sender"] = BasePushNotificationSender(push_http, push_store)

    handler = LegacyRequestHandler(
        agent_executor=executors[executor_kind],
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
        **handler_kwargs,
    )
    decorated = A2ATRequestHandlerDecorator(handler, config=config) if config is not None else A2ATRequestHandlerDecorator(handler)
    app = Starlette(routes=[*create_agent_card_routes(agent_card), *create_rest_routes(decorated)])
    return app, agent_card


def _make_client(app: Any, card: Any, *, config: Any = None, streaming: bool = True) -> tuple[Any, Any]:
    import httpx
    from a2a.client.client import ClientConfig
    from a2a.client.client_factory import ClientFactory
    from a2a.utils.constants import TransportProtocol

    from a2a_t.observability import register_client_factory

    httpx_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    factory = ClientFactory(
        ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
            use_client_preference=True,
            streaming=streaming,
        )
    )
    register_client_factory(factory, config=config)
    return factory.create(card), httpx_client


def _build_request(metadata: Mapping[str, str], context_id: str, *, push_url: str | None = None) -> Any:
    from a2a.types.a2a_pb2 import Role, SendMessageRequest

    request = SendMessageRequest()
    request.message.message_id = "m-e2e"
    request.message.role = Role.ROLE_USER
    request.message.context_id = context_id
    request.message.parts.add().text = "e2e-prompt"
    for key, value in metadata.items():
        request.message.metadata[key] = value
    if push_url is not None:
        request.configuration.task_push_notification_config.id = "push-e2e"
        request.configuration.task_push_notification_config.url = push_url
    return request


async def _send_streaming(client: Any, metadata: Mapping[str, str], *, context_id: str, push_url: str | None = None) -> list[Any]:
    from a2a.client.client import ClientCallContext

    request = _build_request(metadata, context_id, push_url=push_url)
    context = ClientCallContext(service_parameters={"A2A-Extensions": ",".join(metadata) or _TASK_T_URI})
    events: list[Any] = []
    async for response in client.send_message(request, context=context):
        events.append(response)
    return events


async def _send_sync(client: Any, metadata: Mapping[str, str], *, context_id: str) -> list[Any]:
    from a2a.client.client import ClientCallContext

    request = _build_request(metadata, context_id)
    context = ClientCallContext(service_parameters={"A2A-Extensions": ",".join(metadata) or _TASK_T_URI})
    events: list[Any] = []
    async for response in client.send_message(request, context=context):
        events.append(response)
    return events


@pytest.fixture(autouse=True)
def _reset_otel_globals() -> Iterator[None]:
    """Same OTel isolation as tests/observability/conftest.py (this dir has no conftest)."""
    import opentelemetry.metrics._internal as metrics_internal
    import opentelemetry.trace as trace_api

    trace_api._TRACER_PROVIDER = None  # type: ignore[assignment]
    metrics_internal._METER_PROVIDER = None  # type: ignore[assignment]
    for lock in (
        getattr(trace_api, "_TRACER_PROVIDER_SET_ONCE", None),
        getattr(metrics_internal, "_METER_PROVIDER_SET_ONCE", None),
    ):
        if lock is not None:
            for attr in ("_set", "_done"):
                if hasattr(lock, attr):
                    setattr(lock, attr, False)  # type: ignore[attr-defined]
    from a2a_t.observability import negotiation_metrics
    from a2a_t.observability import setup as obs_setup
    from a2a_t.observability.client import transport_decorator
    from a2a_t.observability.llm_stash import clear_llm_usage
    from a2a_t.observability.server import handler_decorator

    transport_decorator._METRIC_INSTRUMENTS.clear()
    handler_decorator._METRIC_INSTRUMENTS.clear()
    negotiation_metrics._METRIC_INSTRUMENTS.clear()
    handler_decorator._TASK_SPAN_REGISTRY.clear()
    clear_llm_usage()
    obs_setup._otel_configured = False
    yield
    handler_decorator._TASK_SPAN_REGISTRY.clear()


@pytest.fixture()
def otel_setup() -> Iterator[tuple[Any, Any]]:
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    trace.set_tracer_provider(TracerProvider())
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    yield exporter, reader


def _a2at_spans(spans: list[Any]) -> list[Any]:
    return [span for span in spans if span.name in _A2AT_SPAN_NAMES]


_SIDE_KINDS = {"client": SpanKind.CLIENT, "server": SpanKind.SERVER}


def _find(spans: list[Any], name: str, *, side: str | None = None) -> Any:
    kind = _SIDE_KINDS.get(side) if side else None
    for span in spans:
        if span.name != name:
            continue
        if kind is not None and span.kind is not kind:
            continue
        return span
    raise AssertionError(f"span {name!r} (side={side!r}) not found in {[s.name for s in spans]}")


def _metric_points(reader: Any, name: str) -> list[Any]:
    data = reader.get_metrics_data()
    if data is None:
        return []
    return [
        point
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


def _metric_names(reader: Any) -> set[str]:
    data = reader.get_metrics_data()
    if data is None:
        return set()
    return {metric.name for rm in data.resource_metrics for sm in rm.scope_metrics for metric in sm.metrics}


def _parse_record(record: Any) -> dict[str, Any]:
    try:
        data = json.loads(record.getMessage())
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def _finalize_abandoned_generators() -> None:
    """Deterministically close async generators abandoned mid-iteration.

    a2a's BaseClient._process_stream returns early on a Message response,
    abandoning the transport decorator's stream generator (and the client entry
    span end inside its finally) to the asyncgen finalizer. A collect + loop
    turns schedule and run that finalization within the test.
    """
    for _ in range(3):
        gc.collect()
        for _ in range(3):
            await asyncio.sleep(0)


async def test_e2e_trace_continuity_event_spans_and_metrics(otel_setup: tuple[Any, Any]) -> None:
    """Assertions 1/2/3/8: entry spans + double-write attrs, traceparent continuity, per-event link/parent, L1/L2/L3 metrics."""
    from a2a_t.observability.llm_stash import LLMUsageStash, set_llm_usage

    exporter, reader = otel_setup
    app, card = _build_app("flow")
    client, httpx_client = _make_client(app, card)
    set_llm_usage(LLMUsageStash(input_tokens=120, output_tokens=30, request_model="gpt-e2e"))
    try:
        events = await _send_streaming(client, {_TASK_T_URI: "prompt-text"}, context_id="C-e2e")
    finally:
        await httpx_client.aclose()
    assert len(events) == 4

    spans = _a2at_spans(exporter.get_finished_spans())
    client_entry = _find(spans, "SendStreamingMessage", side="client")
    server_entry = _find(spans, "SendStreamingMessage", side="server")

    # (1) client entry span exists with double-written attributes (gen_ai.* + gen_ai.agent.a2a.*)
    assert client_entry.kind is SpanKind.CLIENT
    attrs = client_entry.attributes or {}
    assert attrs["gen_ai.operation.name"] == "SendStreamingMessage"
    assert attrs["gen_ai.agent.a2a.operation.name"] == "SendStreamingMessage"
    assert attrs["gen_ai.conversation.id"] == "C-e2e"
    assert attrs["gen_ai.agent.a2a.context_id"] == "C-e2e"
    assert attrs["gen_ai.agent.a2at.extension.name"] == "Task-T"
    assert attrs["gen_ai.agent.a2a.role"] == "ROLE_USER"
    assert attrs["gen_ai.agent.a2a.parts.number"] == 1
    assert client_entry.status.status_code == StatusCode.OK

    # (2) the server SendStreamingMessage span is a child of the client entry span (traceparent continuity)
    assert server_entry.kind is SpanKind.SERVER
    assert server_entry.context.trace_id == client_entry.context.trace_id
    assert server_entry.parent is not None
    assert server_entry.parent.span_id == client_entry.context.span_id
    server_attrs = server_entry.attributes or {}
    assert server_attrs["gen_ai.operation.name"] == "SendStreamingMessage"
    assert server_attrs["gen_ai.agent.a2a.operation.name"] == "SendStreamingMessage"
    assert server_attrs["gen_ai.agent.a2at.extension.name"] == "Task-T"
    assert server_attrs["gen_ai.agent.a2at.task.status"] == "completed"
    assert server_attrs["gen_ai.agent.a2at.task.id"]
    assert server_entry.status.status_code == StatusCode.OK

    # (3) per-event spans on both ends: client LINK to the client entry, server PARENT of the server entry
    client_events = [
        span
        for span in spans
        if span.name == "SendStreamingMessage-event" and span.kind is SpanKind.CLIENT
    ]
    server_events = [
        span
        for span in spans
        if span.name == "SendStreamingMessage-event" and span.kind is SpanKind.INTERNAL
    ]
    assert len(client_events) == 4
    assert len(server_events) == 4
    assert all(span.kind is SpanKind.CLIENT for span in client_events)
    assert all(
        span.links and link.context.span_id == client_entry.context.span_id for span in client_events for link in span.links
    )
    assert all(span.kind is SpanKind.INTERNAL for span in server_events)
    assert all(span.parent is not None and span.parent.span_id == server_entry.context.span_id for span in server_events)
    assert {(span.attributes or {}).get("gen_ai.agent.a2at.streaming.event.kind") for span in server_events} == {
        "status",
        "artifact",
        "completed",
    }

    # (8) metrics: L1 gen_ai histogram (client) + L3 a2at histogram with client AND server side attribution
    reader.collect()
    task_points = _metric_points(reader, "a2at.task.request.duration")
    assert task_points
    gen_ai_points = _metric_points(reader, "gen_ai.client.operation.duration")
    assert gen_ai_points
    for point in gen_ai_points:
        assert (point.attributes or {}).get("gen_ai.operation.name") == "SendStreamingMessage"

    # (8) L2: token usage from the LLM stash landed on the client entry span
    assert attrs["gen_ai.usage.input_tokens"] == 120
    assert attrs["gen_ai.usage.output_tokens"] == 30
    assert attrs["gen_ai.request.model"] == "gpt-e2e"


async def test_e2e_stream_error_span(otel_setup: tuple[Any, Any]) -> None:
    """Assertion 4: stream error span SendStreamingMessage-error LINKed to the entry span."""
    exporter, _reader = otel_setup
    app, card = _build_app("fail")
    client, httpx_client = _make_client(app, card)
    try:
        with pytest.raises(Exception):  # noqa: B017, PT011 - a2a maps the failure to A2AClientError
            await _send_streaming(client, {_TASK_T_URI: "prompt"}, context_id="C-err")
    finally:
        await httpx_client.aclose()

    spans = _a2at_spans(exporter.get_finished_spans())
    client_entry = _find(spans, "SendStreamingMessage", side="client")
    server_entry = _find(spans, "SendStreamingMessage", side="server")
    error_span = _find(spans, "SendStreamingMessage-error")

    assert error_span.kind is SpanKind.CLIENT
    assert (error_span.attributes or {})["error.type"] == "A2AClientError"
    assert error_span.links
    assert all(link.context.span_id == client_entry.context.span_id for link in error_span.links)
    assert client_entry.status.status_code == StatusCode.ERROR
    assert server_entry.status.status_code == StatusCode.ERROR


async def test_e2e_negotiation_span(otel_setup: tuple[Any, Any]) -> None:
    """Assertion 5: SendStreamingMessage-negotiation span PARENT of the entry span + negotiation.* attrs."""
    exporter, _reader = otel_setup
    app, card = _build_app("negotiation")
    client, httpx_client = _make_client(app, card)
    try:
        events = await _send_streaming(client, {_NEG_T_URI: "offer"}, context_id="C-neg")
    finally:
        await httpx_client.aclose()
    assert events
    assert events[-1].HasField("message")
    await _finalize_abandoned_generators()

    spans = _a2at_spans(exporter.get_finished_spans())
    client_entry = _find(spans, "SendStreamingMessage", side="client")
    negotiation = _find(spans, "SendStreamingMessage-negotiation")

    assert negotiation.kind is SpanKind.CLIENT
    assert negotiation.parent is not None
    assert negotiation.parent.span_id == client_entry.context.span_id
    assert negotiation.context.trace_id == client_entry.context.trace_id
    attrs = negotiation.attributes or {}
    assert attrs["gen_ai.agent.a2at.extension.name"] == "Negotiation-T"
    assert attrs["gen_ai.agent.a2at.negotiation.id"] == "N-e2e"
    assert attrs["gen_ai.agent.a2at.negotiation.round"] == 3
    assert attrs["gen_ai.agent.a2at.negotiation.max_rounds"] == 5
    assert attrs["gen_ai.agent.a2at.negotiation.performative"] == "ACCEPT"
    assert attrs["gen_ai.agent.a2at.negotiation.total_rounds"] == 3


async def test_e2e_sync_message_response_attributes_no_extra_spans(otel_setup: tuple[Any, Any]) -> None:
    """Assertion 6 (rule 3): non-negotiation Message response enriches the entry span; no new spans."""
    exporter, _reader = otel_setup
    app, card = _build_app("message")
    client, httpx_client = _make_client(app, card, streaming=False)
    try:
        events = await _send_sync(client, {}, context_id="C-sync")
    finally:
        await httpx_client.aclose()
    assert len(events) == 1
    assert events[0].HasField("message")

    spans = _a2at_spans(exporter.get_finished_spans())
    client_entry = _find(spans, "SendMessage", side="client")
    server_entry = _find(spans, "SendMessage", side="server")

    assert client_entry.kind is SpanKind.CLIENT
    assert (client_entry.attributes or {})["gen_ai.operation.name"] == "SendMessage"
    assert (client_entry.attributes or {})["gen_ai.agent.a2a.operation.name"] == "SendMessage"
    assert (client_entry.attributes or {})["gen_ai.agent.a2at.extension.name"] == "Task-T"
    assert server_entry.kind is SpanKind.SERVER
    assert (server_entry.attributes or {})["gen_ai.agent.a2at.extension.name"] == "Task-T"

    # rule 3: the message response created no negotiation/event/error/push spans anywhere
    assert [span.name for span in spans] == ["SendMessage", "SendMessage"]


async def test_e2e_push_notification_span(otel_setup: tuple[Any, Any]) -> None:
    """Assertion 7: SendMessage-pushNotification span LINKed to the server entry span."""
    exporter, _reader = otel_setup
    app, card = _build_app("flow", push=True)
    client, httpx_client = _make_client(app, card)
    try:
        events = await _send_streaming(
            client, {_TASK_T_URI: "prompt"}, context_id="C-push", push_url="http://push-receiver.test/hook"
        )
    finally:
        await httpx_client.aclose()
    assert len(events) == 4

    spans = _a2at_spans(exporter.get_finished_spans())
    server_entry = _find(spans, "SendStreamingMessage", side="server")
    push_spans = [span for span in spans if span.name == "SendMessage-pushNotification"]
    assert push_spans

    for span in push_spans:
        attrs = span.attributes or {}
        assert span.kind is SpanKind.CLIENT
        assert attrs["gen_ai.agent.a2at.push.notification.url"] == "http://push-receiver.test/hook"
        assert attrs["gen_ai.agent.a2at.task.id"]
        assert any(link.context.span_id == server_entry.context.span_id for link in span.links)


async def test_e2e_payload_attributes_and_log_trace_context(
    otel_setup: tuple[Any, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """Assertion 9: dual-track payload attrs (switch on) + auto logs carrying the shared trace_id."""
    from a2a_t.observability.config import A2ATObservabilityConfig

    config = A2ATObservabilityConfig(extract_request=True, extract_response=True, payload_log_enabled=True)
    exporter, _reader = otel_setup
    app, card = _build_app("flow", config=config)
    client, httpx_client = _make_client(app, card, config=config)
    with caplog.at_level(logging.DEBUG, logger="a2at.observability"):
        try:
            events = await _send_streaming(client, {_TASK_T_URI: "prompt-text"}, context_id="C-log")
        finally:
            await httpx_client.aclose()
    assert len(events) == 4

    spans = _a2at_spans(exporter.get_finished_spans())
    client_entry = _find(spans, "SendStreamingMessage", side="client")
    server_entry = _find(spans, "SendStreamingMessage", side="server")

    # payload attributes on both ends (channel 1)
    client_request_payload = (client_entry.attributes or {})["gen_ai.agent.a2a.request"]
    client_response_payload = (client_entry.attributes or {})["gen_ai.agent.a2a.response"]
    server_request_payload = (server_entry.attributes or {})["gen_ai.agent.a2a.request"]
    server_response_payload = (server_entry.attributes or {})["gen_ai.agent.a2a.response"]
    assert "e2e-prompt" in client_request_payload
    assert client_response_payload
    assert "e2e-prompt" in server_request_payload
    assert "TASK_STATE_COMPLETED" in server_response_payload

    # auto logs carry the trace_id of the one shared trace (channel 2)
    records = [_parse_record(record) for record in caplog.records if record.name == "a2at.observability"]
    assert records
    expected_trace = f"{client_entry.context.trace_id:032x}"
    with_trace = [data for data in records if data.get("trace_id") == expected_trace]
    assert with_trace
    assert all(data.get("span_id") for data in with_trace)
    assert {"task.request", "task.status_changed", "task.artifact"} <= {data["event"] for data in with_trace}


async def test_e2e_master_switch_off(
    otel_setup: tuple[Any, Any], caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Assertion 10: master switch off -> zero A2A-T span/metric/log output, business flow intact."""
    from a2a_t.observability import _otel_compat

    monkeypatch.setattr(_otel_compat, "_ENABLED", False)
    exporter, reader = otel_setup
    app, card = _build_app("flow")
    client, httpx_client = _make_client(app, card)
    with caplog.at_level(logging.DEBUG, logger="a2at.observability"):
        try:
            events = await _send_streaming(client, {_TASK_T_URI: "prompt"}, context_id="C-off")
        finally:
            await httpx_client.aclose()
    assert len(events) == 4

    spans = exporter.get_finished_spans()
    assert spans
    assert not _a2at_spans(spans)
    assert not [span for span in spans if span.attributes and "gen_ai.agent.a2at.extension.name" in span.attributes]

    reader.collect()
    assert not _metric_names(reader) & {"a2at.task.request.duration", "gen_ai.client.operation.duration"}

    assert not [record for record in caplog.records if record.name == "a2at.observability"]


async def test_register_client_factory_decorates_real_factory_transport() -> None:
    """register_client_factory wiring against the real a2a-sdk ClientFactory (Task 9)."""
    import httpx
    from a2a.client.client import ClientConfig
    from a2a.client.client_factory import ClientFactory
    from a2a.client.transports.rest import RestTransport
    from a2a.utils.constants import TransportProtocol

    from a2a_t.observability.client.factory import register_client_factory
    from a2a_t.observability.client.transport_decorator import A2ATClientTransportDecorator

    httpx_client = httpx.AsyncClient()
    factory = ClientFactory(
        ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
            use_client_preference=True,
        )
    )
    try:
        register_client_factory(factory)
        client = factory.create(_agent_card(push=False))

        assert isinstance(client._transport, A2ATClientTransportDecorator)
        assert isinstance(client._transport._inner, RestTransport)

        register_client_factory(factory)
        client2 = factory.create(_agent_card(push=False))

        assert isinstance(client2._transport, A2ATClientTransportDecorator)
        assert not isinstance(client2._transport._inner, A2ATClientTransportDecorator)
        assert isinstance(client2._transport._inner, RestTransport)
    finally:
        await httpx_client.aclose()
