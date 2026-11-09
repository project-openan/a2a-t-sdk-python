"""Tests of the client agent's inbound A2A gateway."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext, UnauthenticatedUser
from a2a.server.events import Event as A2AEvent
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.base_push_notification_sender import BasePushNotificationSender
from a2a.types import (
    Message,
    Role,
    SendMessageRequest,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import InvalidParamsError
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from demo.catalog import DemoRunCatalog
from google.protobuf.json_format import MessageToDict
from protocol.client_flow import ClientFlowResult
from protocol.client_gateway import ClientGatewayExecutor, build_gateway_app, build_gateway_service
from protocol.client_service import RunManager
from protocol.client_store import SqliteClientStore
from protocol.gateway_push import validate_push_url
from protocol.sqlite_push_config_store import SqlitePushNotificationConfigStore
from protocol.sqlite_task_store import SqliteTaskStore, read_task_summaries
from shared.config import SampleConfig
from shared.models import NegotiationDecision, NegotiationProposeView
from shared.run_constants import PROTOCOL_A2A_T

CONTEXT_ID = "ctx-gateway"


class RecordingEventQueue(EventQueue):
    """Collect events enqueued by an executor."""

    def __init__(self) -> None:
        self.events: list[A2AEvent] = []

    async def enqueue_event(self, event: A2AEvent) -> None:
        self.events.append(event)


class FakeRuntime:
    """One proposal, one accept, one completed report."""

    def __init__(self, *, wait: asyncio.Event | None = None) -> None:
        self.manual_provider = None
        self.discovered = SimpleNamespace(url="http://server", agent_card=SimpleNamespace(name="ran"))
        self.wait = wait
        self.closed = False
        self.user_request: str | None = None
        self.conversation_context: str | None = None

    async def run(
        self,
        *,
        event_sink: Callable[[dict[str, object]], None] | None = None,
        on_decision_requested: Callable[[NegotiationProposeView, NegotiationContext], None]
        | None = None,
        on_decision_made: Callable[
            [NegotiationDecision, NegotiationProposeView, NegotiationContext], None
        ]
        | None = None,
        on_intent_ready: Callable[[str], Awaitable[bool]] | None = None,
        trace_sink: Callable[[dict[str, object]], None] | None = None,
        activity_sink: Callable[[dict[str, object]], None] | None = None,
        run_id: str | None = None,
        user_request: str | None = None,
        conversation_context: str | None = None,
        validate_before_send: bool = False,
        enforce_required_fields: bool = False,
        require_rate_target: bool = True,
        site_level_rate_optional: bool = False,
        run_mode: str = "create",
        target_task_id: str | None = None,
        target_context_id: str | None = None,
        target_server_task_id: str | None = None,
        target_server_context_id: str | None = None,
    ) -> ClientFlowResult:
        del on_intent_ready, trace_sink, activity_sink, run_id, validate_before_send, enforce_required_fields
        del require_rate_target, site_level_rate_optional, run_mode, target_task_id, target_context_id
        del target_server_task_id, target_server_context_id
        self.user_request = user_request
        self.conversation_context = conversation_context
        if self.wait is not None:
            await self.wait.wait()
        if event_sink is not None:
            event_sink(
                {
                    "kind": "status",
                    "task_id": "outbound-task",
                    "context_id": "outbound-ctx",
                    "state": "TASK_STATE_WORKING",
                }
            )
            event_sink(
                {
                    "kind": "artifact",
                    "task_id": "outbound-task",
                    "context_id": "outbound-ctx",
                    "name": "tool result",
                    "metadata": {"toolName": "apply_energy_saving_plan"},
                }
            )
            event_sink(
                {
                    "kind": "artifact",
                    "task_id": "outbound-task",
                    "context_id": "outbound-ctx",
                    "name": "energy saving intent report",
                }
            )
        propose = NegotiationProposeView(
            negotiation_id="neg-1",
            kind="information",
            round=1,
            max_rounds=3,
            content="missing region",
            context_id="outbound-ctx",
        )
        session = NegotiationContext(
            id="neg-1",
            round=1,
            max_rounds=3,
            performative=NegotiationPerformative.PROPOSE,
        )
        if on_decision_requested is not None:
            on_decision_requested(propose, session)
        decision = NegotiationDecision(action="accept", rationale="accepted")
        if on_decision_made is not None:
            on_decision_made(decision, propose, session)
        return ClientFlowResult(
            final_state="task_completed",
            decisions=[decision],
            context_id="outbound-ctx",
            task_id="outbound-task",
            report={"summary": "The plan is applied.", "cell_id": "cell-001"},
        )

    async def aclose(self) -> None:
        self.closed = True


def _context(text: str = "Save energy in Region A.", context_id: str = CONTEXT_ID) -> RequestContext:
    message = Message(message_id="message-1", role=Role.ROLE_USER, context_id=context_id)
    message.parts.add(text=text)
    request = SendMessageRequest(message=message)
    call_context = ServerCallContext(state={}, user=UnauthenticatedUser())
    return RequestContext(call_context=call_context, request=request)


def _manager(
    tmp_path: Path,
    config: SampleConfig,
    *,
    wait: asyncio.Event | None = None,
) -> tuple[RunManager, SqliteClientStore]:
    store = SqliteClientStore(tmp_path / "client.sqlite3")

    async def factory(
        resolved_config: SampleConfig,
        scenario: str,
        mode: str,
        transport: str,
        discovery: str,
        *,
        protocol: str = PROTOCOL_A2A_T,
        run_id: str = "",
    ) -> FakeRuntime:
        del resolved_config, scenario, mode, transport, discovery, protocol, run_id
        return FakeRuntime(wait=wait)

    manager = RunManager(
        config=config,
        runtime_factory=factory,
        catalog=DemoRunCatalog(),
        runs_dir=tmp_path / "runs",
        store=store,
    )
    return manager, store


async def test_gateway_maps_one_message_to_one_run_and_returns_report(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    manager, store = _manager(tmp_path, offline_config)
    executor = ClientGatewayExecutor(service=manager, config=offline_config)
    queue = RecordingEventQueue()

    await executor.execute(_context(), queue)

    kinds = [type(event).__name__ for event in queue.events]
    assert "Task" in kinds
    artifacts = [
        event
        for event in queue.events
        if isinstance(event, TaskArtifactUpdateEvent)
        and event.artifact.name == "energy saving intent report"
    ]
    assert artifacts
    report = MessageToDict(artifacts[0].artifact.parts[0].data)
    assert report["summary"] == "The plan is applied."
    assert any(
        isinstance(event, TaskStatusUpdateEvent)
        and event.status.state == TaskState.TASK_STATE_COMPLETED
        for event in queue.events
    )

    metadata = MessageToDict(artifacts[0].artifact.metadata)
    run = await store.get_run(str(metadata["clientRunId"]))
    assert run is not None
    assert run.conversation_id == f"a2a-{CONTEXT_ID}"
    assert run.status == "finished"
    assert run.final_state == "task_completed"
    assert run.source_kind == "gateway"
    assert run.source["channel"] == "a2a-gateway"
    assert run.source["contextId"] == CONTEXT_ID


async def test_gateway_rejects_a_second_active_run_of_the_same_conversation(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    wait = asyncio.Event()
    manager, _ = _manager(tmp_path, offline_config, wait=wait)
    executor = ClientGatewayExecutor(service=manager, config=offline_config)
    first_queue = RecordingEventQueue()
    first = asyncio.create_task(executor.execute(_context(), first_queue))
    while not executor.runs_by_task:
        await asyncio.sleep(0.01)

    second_queue = RecordingEventQueue()
    await executor.execute(_context(), second_queue)
    assert any(
        isinstance(event, TaskStatusUpdateEvent)
        and event.status.state == TaskState.TASK_STATE_FAILED
        for event in second_queue.events
    )

    wait.set()
    await first


async def test_gateway_cancel_cancels_the_client_run(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    wait = asyncio.Event()
    manager, store = _manager(tmp_path, offline_config, wait=wait)
    executor = ClientGatewayExecutor(service=manager, config=offline_config)
    queue = RecordingEventQueue()
    context = _context()
    task = asyncio.create_task(executor.execute(context, queue))
    while not executor.runs_by_task:
        await asyncio.sleep(0.01)
    run = next(iter(executor.runs_by_task.values()))
    while await store.get_run(run.run_id) is None:
        await asyncio.sleep(0.01)

    await executor.cancel(context, queue)
    await asyncio.sleep(0.05)

    stored = await store.get_run(run.run_id)
    assert stored is not None
    assert stored.status == "interrupted"
    await asyncio.gather(task, return_exceptions=True)


async def test_gateway_app_advertises_a_plain_a2a_agent(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    manager, _ = _manager(tmp_path, offline_config)
    app = build_gateway_app(config=offline_config, service=manager, host="127.0.0.1", port=8001)
    card = app.state.agent_card
    assert card.name == "ran-energy-saving-client-gateway"
    assert card.capabilities.streaming is True
    assert card.capabilities.push_notifications is True
    assert [interface.url for interface in card.supported_interfaces] == ["http://127.0.0.1:8001"]
    assert app.state.client_service is manager


async def test_gateway_persists_push_registrations_when_a_database_is_configured(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    manager, _ = _manager(tmp_path, offline_config)
    database = tmp_path / "gateway.sqlite3"
    config = replace(offline_config, gateway_task_db_path=str(database))
    app = build_gateway_app(config=config, service=manager)

    async with app.router.lifespan_context(app):
        store = app.state.push_config_store
        assert isinstance(store.inner, SqlitePushNotificationConfigStore)
        assert store.inner.path == database
        assert app.state.push_sender is not None


def test_build_gateway_service_uses_the_client_database(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    config = replace(offline_config, client_db_path=str(tmp_path / "client.sqlite3"))
    service = build_gateway_service(config=config, env_path=None)
    assert isinstance(service.store, SqliteClientStore)
    assert service.store.path == tmp_path / "client.sqlite3"


async def test_gateway_uses_the_injected_persistent_task_store(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """The gateway handler is wired to the SQLite store instead of the in-memory one."""
    manager, _ = _manager(tmp_path, offline_config)
    database = tmp_path / "gateway-tasks.sqlite3"

    app = build_gateway_app(
        config=offline_config,
        service=manager,
        task_store=SqliteTaskStore(database),
    )

    assert isinstance(app.state.gateway_task_store, SqliteTaskStore)
    assert app.state.gateway_task_store.path == database
    assert read_task_summaries(database) == []


async def test_gateway_rejects_requests_without_the_token(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """A configured token guards the gateway A2A routes."""
    manager, _ = _manager(tmp_path, offline_config)
    config = replace(offline_config, auth_token="secret")
    app = build_gateway_app(config=config, service=manager)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.get("/tasks")
        authorized = await client.get("/tasks", headers={"Authorization": "Bearer secret"})
        health = await client.get("/health")
        metrics = await client.get("/metrics")

    assert unauthorized.status_code == 401
    assert authorized.status_code != 401
    assert health.status_code == 200
    assert metrics.status_code == 401


def _is_phase_status(event: A2AEvent, phase: str) -> bool:
    """Return whether an event is a progress status of one phase."""
    return (
        isinstance(event, TaskStatusUpdateEvent)
        and event.status.state == TaskState.TASK_STATE_WORKING
        and event.status.HasField("message")
        and event.status.message.HasField("metadata")
        and MessageToDict(event.status.message.metadata).get("phase") == phase
    )


async def test_gateway_publishes_progress_before_the_final_report(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """The inbound Task shows negotiation and execution milestones, then the report."""
    manager, _ = _manager(tmp_path, offline_config)
    executor = ClientGatewayExecutor(service=manager, config=offline_config)
    queue = RecordingEventQueue()

    await executor.execute(_context(), queue)

    phases = {
        str(MessageToDict(event.status.message.metadata)["phase"])
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent)
        and event.status.state == TaskState.TASK_STATE_WORKING
        and event.status.HasField("message")
        and event.status.message.HasField("metadata")
    }
    assert {"dispatch", "negotiation", "execution", "report"} <= phases

    progress_indexes = [
        index for index, event in enumerate(queue.events) if _is_phase_status(event, "negotiation")
    ]
    completed_index = next(
        index
        for index, event in enumerate(queue.events)
        if isinstance(event, TaskStatusUpdateEvent)
        and event.status.state == TaskState.TASK_STATE_COMPLETED
    )
    assert progress_indexes
    assert max(progress_indexes) < completed_index


async def test_gateway_pushes_progress_and_the_report_to_a_webhook(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """An inline taskPushNotificationConfig receives every milestone plus the final artifact."""
    manager, _ = _manager(tmp_path, offline_config)
    posted: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        posted.append(request)
        return httpx.Response(200)

    push_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    push_store = SqlitePushNotificationConfigStore(tmp_path / "gateway-push.sqlite3")
    sender = BasePushNotificationSender(push_client, push_store)
    app = build_gateway_app(
        config=offline_config,
        service=manager,
        push_config_store=push_store,
        push_sender=sender,
    )
    message = Message(message_id="message-1", role=Role.ROLE_USER, context_id=CONTEXT_ID)
    message.parts.add(text="Save energy in Region A.")
    request = SendMessageRequest(message=message)
    request.configuration.task_push_notification_config.url = "https://caller.example/hook"
    request.configuration.task_push_notification_config.token = "hook-token"
    body = MessageToDict(request)

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/message:send",
                json=body,
                headers={"A2A-Version": "1.0"},
            )
    finally:
        await push_client.aclose()

    assert response.status_code == 200
    assert posted
    assert all(item.headers.get("x-a2a-notification-token") == "hook-token" for item in posted)
    payloads = [json.loads(item.content) for item in posted]
    states = [
        str(payload["statusUpdate"]["status"]["state"])
        for payload in payloads
        if "statusUpdate" in payload
    ]
    assert "TASK_STATE_WORKING" in states
    assert "TASK_STATE_COMPLETED" in states
    artifact_names = [
        str(payload["artifactUpdate"]["artifact"]["name"])
        for payload in payloads
        if "artifactUpdate" in payload
    ]
    assert "energy saving intent report" in artifact_names

    task_id = str(response.json()["task"]["id"])
    stored = await push_store.get_info_for_dispatch(task_id)
    assert [config.url for config in stored] == ["https://caller.example/hook"]


async def test_gateway_streams_progress_before_the_report(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """message:stream carries the same milestones to a streaming caller."""
    manager, _ = _manager(tmp_path, offline_config)
    app = build_gateway_app(config=offline_config, service=manager)
    message = Message(message_id="message-1", role=Role.ROLE_USER, context_id=CONTEXT_ID)
    message.parts.add(text="Save energy in Region A.")
    body = MessageToDict(SendMessageRequest(message=message))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/message:stream",
            json=body,
            headers={"A2A-Version": "1.0"},
        )

    assert response.status_code == 200
    payloads = [
        json.loads(line[len("data:") :].strip())
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    phases = [
        payload["statusUpdate"]["status"]["message"]["metadata"]["phase"]
        for payload in payloads
        if "statusUpdate" in payload
        and "message" in payload["statusUpdate"]["status"]
        and "metadata" in payload["statusUpdate"]["status"]["message"]
    ]
    assert {"dispatch", "negotiation", "execution", "report"} <= set(phases)
    assert any(
        payload.get("statusUpdate", {}).get("status", {}).get("state")
        == "TASK_STATE_COMPLETED"
        for payload in payloads
    )
    assert any("artifactUpdate" in payload for payload in payloads)


async def test_a_failing_webhook_does_not_fail_the_run(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """Push delivery is best-effort: a 500 from the caller still yields the completed report."""
    manager, _ = _manager(tmp_path, offline_config)

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500)

    push_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    push_store = SqlitePushNotificationConfigStore(tmp_path / "gateway-push.sqlite3")
    sender = BasePushNotificationSender(push_client, push_store)
    app = build_gateway_app(
        config=offline_config,
        service=manager,
        push_config_store=push_store,
        push_sender=sender,
    )
    message = Message(message_id="message-1", role=Role.ROLE_USER, context_id=CONTEXT_ID)
    message.parts.add(text="Save energy in Region A.")
    request = SendMessageRequest(message=message)
    request.configuration.task_push_notification_config.url = "https://caller.example/hook"
    body = MessageToDict(request)

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/message:send",
                json=body,
                headers={"A2A-Version": "1.0"},
            )
    finally:
        await push_client.aclose()

    assert response.status_code == 200
    assert response.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_push_url_validation_rejects_unsafe_destinations() -> None:
    validate_push_url("https://caller.example/hook")
    validate_push_url("http://127.0.0.1:9000/hook")

    with pytest.raises(InvalidParamsError, match="absolute http"):
        validate_push_url("ftp://caller.example/hook")
    with pytest.raises(InvalidParamsError, match="credentials"):
        validate_push_url("https://user:secret@caller.example/hook")
    with pytest.raises(InvalidParamsError, match="loopback"):
        validate_push_url("http://caller.example/hook")
