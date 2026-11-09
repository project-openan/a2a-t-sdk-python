"""ASGI tests of the RAN energy-saving Web UI (no network)."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import socket
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from a2a.server.context import ServerCallContext, UnauthenticatedUser
from a2a.types import Artifact, Part, Task, TaskState, TaskStatus
from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    NegotiationContext,
    NegotiationPerformative,
)
from demo.scenario import (
    FINAL_STATE_TASK_COMPLETED,
    get_scenario,
    resolve_user_request,
)
from demo.web.app import create_app
from integration_helpers import (
    free_tcp_port,
    offline_direct_config,
    running_http_app,
    running_server,
)
from protocol.client_flow import ClientFlowResult
from protocol.client_store import (
    ClientRunRecord,
    NegotiationEventRecord,
    SqliteClientStore,
    TaskSnapshotRecord,
)
from protocol.decision_provider import ManualDecisionProvider
from protocol.sqlite_task_store import SqliteTaskStore
from shared.config import DISCOVERY_NMS, TRANSPORT_WEBHOOK, SampleConfig
from shared.models import NegotiationDecision, NegotiationProposeView
from shared.run_constants import FINAL_STATE_INTENT_NOT_CONFIRMED, PROTOCOL_A2A_T
from shared.trace import PHASES
from shared.webhook_receiver import WebhookPortInUseError, WebhookReceiver

CONTEXT_ID = "ctx-web"

#: Local trace token configured for every app instance of this module (design §4).
TRACE_TOKEN = "test-trace-token"


@pytest.fixture(autouse=True)
def trace_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure the local token the trace ingestion endpoint requires."""
    monkeypatch.setenv("A2AT_SAMPLE_TRACE_TOKEN", TRACE_TOKEN)
    return TRACE_TOKEN


def _propose_view() -> NegotiationProposeView:
    return NegotiationProposeView(
        negotiation_id=CONTEXT_ID,
        kind="information",
        round=1,
        max_rounds=3,
        content="Please provide the following missing items: region.",
        template_uri="Negotiation-T/information-negotiation/propose/v1",
        context_id=CONTEXT_ID,
    )


def _session() -> NegotiationContext:
    return NegotiationContext(
        id=CONTEXT_ID,
        round=1,
        max_rounds=3,
        performative=NegotiationPerformative.PROPOSE,
    )


def _propose_event() -> dict[str, object]:
    return {
        "kind": "message",
        "text": "Negotiation proposal from the RAN energy saving service",
        "context_id": CONTEXT_ID,
        "message_id": "propose-1",
        "metadata": {
            NEGOTIATION_T_EXTENSION_URI: "Please provide the following missing items: region.",
            NEGOTIATION_CONTEXT_METADATA_KEY: {
                "id": CONTEXT_ID,
                "round": 1,
                "maxRounds": 3,
                "performative": "PROPOSE",
            },
        },
    }


def _status_event() -> dict[str, object]:
    return {
        "kind": "status",
        "task_id": "task-1",
        "context_id": CONTEXT_ID,
        "state": "TASK_STATE_COMPLETED",
    }


def _trace_span(
    kind: str,
    *,
    phase: str,
    title: str,
    round_number: int | None = None,
    context_id: str | None = CONTEXT_ID,
    detail: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build one synthetic trace span for the runtime double."""
    return {
        "schemaVersion": 1,
        "runId": "",
        "seq": 0,
        "spanId": "",
        "parentSpanId": None,
        "contextId": context_id,
        "taskId": None,
        "phase": phase,
        "phaseStep": 0,
        "round": round_number,
        "actor": "client",
        "side": "client",
        "kind": kind,
        "stage": None,
        "title": title,
        "purpose": None,
        "status": "ok",
        "startedAt": "2026-10-23T00:00:00.000Z",
        "endedAt": None,
        "durationMs": None,
        "direction": "internal",
        "model": None,
        "tokens": None,
        "error": None,
        "detail": detail or {},
    }


class FakeManualStructurer:
    """Structure one manual answer into a fixed decision for the Web API test."""

    async def structure_answer(
        self,
        propose: NegotiationProposeView,
        session: NegotiationContext,
        answer: str,
    ) -> NegotiationDecision:
        del propose, session
        return NegotiationDecision(
            action="accept",
            rationale=f"structured: {answer}",
            filled_params={"region": "Region A"},
        )


class FakeRuntime:
    """Deterministic runtime double: one proposal, an optional manual wait, then completion."""

    def __init__(
        self,
        *,
        manual: bool = False,
        fail: bool = False,
        intent_gate: bool = False,
    ) -> None:
        self.manual_provider = ManualDecisionProvider(timeout_seconds=5.0) if manual else None
        self.manual_structurer = FakeManualStructurer() if manual else None
        self.recommendation_sink = None
        self.fail = fail
        self.intent_gate = intent_gate
        self.closed = False
        self.run_id: str | None = None
        self.user_request: str | None = None
        self.conversation_context: str | None = None
        self.run_mode = "create"
        self.target_task_id: str | None = None
        self.target_context_id: str | None = None

    def set_recommendation_sink(self, sink: Callable[..., None] | None) -> None:
        """Accept the confirmation recommendation callback."""
        self.recommendation_sink = sink

    async def run(
        self,
        *,
        event_sink: Callable[[dict[str, object]], None] | None = None,
        on_decision_requested: Callable[[NegotiationProposeView, NegotiationContext], None] | None = None,
        on_decision_made: Callable[[NegotiationDecision, NegotiationProposeView, NegotiationContext], None]
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
        run_mode: str = "create",
        target_task_id: str | None = None,
        target_context_id: str | None = None,
    ) -> ClientFlowResult:
        del validate_before_send, enforce_required_fields, require_rate_target
        self.run_id = run_id
        self.user_request = user_request
        self.conversation_context = conversation_context
        self.run_mode = run_mode
        self.target_task_id = target_task_id
        self.target_context_id = target_context_id
        if on_intent_ready is not None and self.intent_gate:
            if not await on_intent_ready("## Operation Type\nCreate"):
                return ClientFlowResult(final_state=FINAL_STATE_INTENT_NOT_CONFIRMED)
        if trace_sink is not None:
            trace_sink(
                _trace_span(
                    "task_prompt_rendered",
                    phase="task_prompt",
                    title="Task-T prompt rendered",
                    detail={"length": 12},
                )
            )
        if event_sink is not None:
            event_sink(_propose_event())
        if trace_sink is not None:
            trace_sink(
                _trace_span(
                    "decision_requested",
                    phase="negotiation",
                    title="Decision requested",
                    round_number=1,
                )
            )
        if on_decision_requested is not None:
            on_decision_requested(_propose_view(), _session())
        decision = NegotiationDecision(action="accept", rationale="The proposal is acceptable.")
        if self.recommendation_sink is not None:
            self.recommendation_sink(decision, _propose_view(), _session())
        if self.manual_provider is not None:
            decision = await self.manual_provider.respond(_propose_view(), _session())
        if on_decision_made is not None:
            on_decision_made(decision, _propose_view(), _session())
        elif trace_sink is not None:
            # Mirrors the production flow: the callback owns the decision row, so only a consumer
            # without a callback sees the synthetic span.
            trace_sink(
                _trace_span(
                    "decision_made",
                    phase="resolution",
                    title="Decision made",
                    round_number=1,
                )
            )
        if event_sink is not None:
            event_sink(_status_event())
        if self.fail:
            raise RuntimeError("boom")
        return ClientFlowResult(
            final_state=FINAL_STATE_TASK_COMPLETED,
            decisions=[decision],
            context_id=CONTEXT_ID,
            task_id="task-1",
            report={"summary": "Plan applied", "estimated_saving_kwh": 12.5},
        )

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def runtimes() -> list[FakeRuntime]:
    """Runtimes built by the injected factory, in start order."""
    return []


@pytest.fixture
def factory(runtimes: list[FakeRuntime]) -> Callable[..., Any]:
    """Runtime factory creating a runtime according to the requested mode."""

    async def build(
        config: SampleConfig,
        scenario: str,
        mode: str,
        transport: str,
        discovery: str,
        *,
        protocol: str = PROTOCOL_A2A_T,
        run_id: str = "",
    ) -> FakeRuntime:
        del config, scenario, transport, discovery, protocol, run_id
        runtime = FakeRuntime(manual=(mode in {"manual", "confirm"}))
        runtimes.append(runtime)
        return runtime

    return build


@pytest.fixture
async def client(
    offline_config: SampleConfig,
    factory: Callable[..., Any],
    tmp_path: Path,
) -> Any:
    """HTTP client bound to the ASGI app."""
    app = create_app(
        offline_config,
        runs_dir=tmp_path,
        runtime_factory=factory,
        finished_grace_seconds=0.0,
        ui_mode="explorer",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def _wait_status(
    client: httpx.AsyncClient,
    run_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Poll the run status until ``predicate`` holds."""
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = await client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        latest = response.json()
        if predicate(latest):
            return latest
        await asyncio.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach the expected state: {latest}")


def _sse_payloads(body: str) -> list[dict[str, Any]]:
    """Parse the SSE data frames of one response body."""
    payloads: list[dict[str, Any]] = []
    for line in body.splitlines():
        if line.startswith("data:"):
            payloads.append(json.loads(line[len("data:") :].strip()))
    return payloads


def _assert_seq_order(payloads: list[dict[str, Any]]) -> None:
    """Every frame carries a strictly increasing ``seq`` starting at 1 (design §3)."""
    seqs = [payload["seq"] for payload in payloads]
    assert seqs == list(range(1, len(payloads) + 1))


async def test_metrics_endpoint_is_served(client: httpx.AsyncClient) -> None:
    response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "a2at_client_runs_total" in response.text


async def test_meta_exposes_the_read_only_process_language(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/meta")

    assert response.status_code == 200
    meta = response.json()
    assert meta["language"] == "en-US"
    assert meta["modelMode"] == "offline"
    assert meta["modelLabel"] == "offline (scripted)"
    assert "information" in meta["scenarios"]
    assert meta["modes"] == ["auto", "manual", "confirm"]
    assert meta["discoveries"] == ["registry", "direct", "nms"]
    assert meta["defaultScenario"] == "night"
    assert meta["defaultMode"] == "auto"
    assert meta["defaultTransport"] == "sse"
    assert meta["defaultDiscovery"] == "registry"
    # The UI's stream grace is served by the backend, never hard-coded client-side. This fixture
    # disables the grace so streaming tests finish immediately.
    assert meta["finishedGraceMs"] == 0


def _nms_config(base: SampleConfig) -> SampleConfig:
    """Return a configuration whose client talks to the vendor NMS."""
    return replace(
        base,
        discovery=DISCOVERY_NMS,
        nms_a2a_base="http://nms.example/a2a/json",
        nms_token_url="http://nms.example/sessions",
        nms_token_user="user",
        nms_token_value="secret",
    )


def _working_task_row(task_id: str = "task-9", context_id: str = "ctx-9") -> dict[str, object]:
    return {
        "taskId": task_id,
        "contextId": context_id,
        "state": "TASK_STATE_WORKING",
        "summary": "Task is executing the energy-saving plan.",
        "region": "Region A",
        "sites": ["Site 1"],
        "target": "{00:00~24:00,10Mbps}",
    }


async def test_nms_webhook_lifespan_binds_the_callback_receiver_before_any_run(
    offline_config: SampleConfig,
    tmp_path: Path,
) -> None:
    """A callback port pinned by a public URL must be claimed at startup, not after approval."""
    port = free_tcp_port()
    config = replace(
        _nms_config(offline_config),
        transport=TRANSPORT_WEBHOOK,
        webhook_host="127.0.0.1",
        webhook_port=port,
        webhook_public_url="",
    )
    app = create_app(config, runs_dir=tmp_path, finished_grace_seconds=0.0)

    async with app.router.lifespan_context(app):
        # Nothing else in this process listens on the port: a successful connect proves the
        # lifespan already bound the process-level receiver.
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            pass


async def test_nms_webhook_lifespan_reports_a_busy_callback_port(
    offline_config: SampleConfig,
    tmp_path: Path,
) -> None:
    """A busy pinned port fails the startup instead of the first run."""
    events: list[dict[str, object]] = []
    holder = WebhookReceiver(token="t", on_event=events.append, host="127.0.0.1", port=0)
    await holder.start()
    try:
        config = replace(
            _nms_config(offline_config),
            transport=TRANSPORT_WEBHOOK,
            webhook_host="127.0.0.1",
            webhook_port=holder.port,
            webhook_public_url=f"http://127.0.0.1:{holder.port}/webhook",
            webhook_token="t",
        )
        app = create_app(config, runs_dir=tmp_path, finished_grace_seconds=0.0)

        with pytest.raises(WebhookPortInUseError):
            async with app.router.lifespan_context(app):
                pytest.fail("the lifespan must not start with a busy callback port")
    finally:
        await holder.stop()


async def test_nms_task_list_is_disabled_outside_nms(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/nms/tasks")

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["tasks"] == []


async def test_nms_task_list_returns_modifiable_tasks(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_list(_config: SampleConfig) -> list[dict[str, object]]:
        return [_working_task_row()]

    monkeypatch.setattr("demo.web.app.list_modifiable_tasks", fake_list)
    app = create_app(_nms_config(offline_config), runs_dir=tmp_path, finished_grace_seconds=0.0)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.get("/api/nms/tasks?state=modifiable")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["states"] == ["TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"]
    assert payload["tasks"][0]["taskId"] == "task-9"


async def test_nms_task_list_keeps_the_working_filter_for_older_callers(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_list(_config: SampleConfig) -> list[dict[str, object]]:
        return [
            _working_task_row(),
            {**_working_task_row(task_id="task-8", context_id="ctx-8"), "state": "TASK_STATE_SUBMITTED"},
        ]

    monkeypatch.setattr("demo.web.app.list_modifiable_tasks", fake_list)
    app = create_app(_nms_config(offline_config), runs_dir=tmp_path, finished_grace_seconds=0.0)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.get("/api/nms/tasks?state=working")

    assert response.status_code == 200
    assert [row["taskId"] for row in response.json()["tasks"]] == ["task-9"]


async def test_nms_task_list_reports_an_nms_failure(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from protocol.nms_client import NmsProtocolError

    async def failing_list(_config: SampleConfig) -> list[dict[str, object]]:
        raise NmsProtocolError("NMS task list returned HTTP 500")

    monkeypatch.setattr("demo.web.app.list_modifiable_tasks", failing_list)
    app = create_app(_nms_config(offline_config), runs_dir=tmp_path, finished_grace_seconds=0.0)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.get("/api/nms/tasks")

    assert response.status_code == 502
    assert response.json()["code"] == "nms_unavailable"


async def test_start_run_requires_both_target_ids(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/runs",
        json={"scenario": "outage", "mode": "auto", "targetTaskId": "task-9"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "target_pair_required"


async def test_start_run_rejects_a_target_that_is_not_modifiable(
    offline_config: SampleConfig,
    factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def completed_task(_config: SampleConfig, task_id: str) -> dict[str, object]:
        del task_id
        return {**_working_task_row(), "state": "TASK_STATE_COMPLETED"}

    monkeypatch.setattr("demo.web.app.read_task", completed_task)
    app = create_app(
        _nms_config(offline_config),
        runs_dir=tmp_path,
        runtime_factory=factory,
        finished_grace_seconds=0.0,
        ui_mode="explorer",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.post(
            "/api/runs",
            json={
                "scenario": "outage",
                "mode": "auto",
                "discovery": "nms",
                "targetTaskId": "task-9",
                "targetContextId": "ctx-9",
            },
        )

    assert response.status_code == 409
    assert response.json()["code"] == "target_not_modifiable"


async def test_start_run_accepts_a_submitted_target(
    offline_config: SampleConfig,
    factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def submitted_task(_config: SampleConfig, task_id: str) -> dict[str, object]:
        del task_id
        return {**_working_task_row(), "state": "TASK_STATE_SUBMITTED"}

    monkeypatch.setattr("demo.web.app.read_task", submitted_task)
    app = create_app(
        _nms_config(offline_config),
        runs_dir=tmp_path,
        runtime_factory=factory,
        finished_grace_seconds=0.0,
        ui_mode="explorer",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.post(
            "/api/runs",
            json={
                "scenario": "outage",
                "mode": "auto",
                "discovery": "nms",
                "targetTaskId": "task-9",
                "targetContextId": "ctx-9",
            },
        )

    assert response.status_code == 202
    assert response.json()["runId"]


async def test_start_run_accepts_a_modify_on_a_selected_task_for_any_scenario(
    offline_config: SampleConfig,
    factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Any A2A-T scenario can be modified: the operator's Modify text is the Task-T input."""
    async def working_task(_config: SampleConfig, task_id: str) -> dict[str, object]:
        del task_id
        return _working_task_row()

    monkeypatch.setattr("demo.web.app.read_task", working_task)
    app = create_app(
        _nms_config(offline_config),
        runs_dir=tmp_path,
        runtime_factory=factory,
        finished_grace_seconds=0.0,
        ui_mode="explorer",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.post(
            "/api/runs",
            json={
                "scenario": "night",
                "mode": "auto",
                "discovery": "nms",
                "targetTaskId": "task-9",
                "targetContextId": "ctx-9",
            },
        )

    assert response.status_code == 202
    assert response.json()["runId"]


async def test_start_run_passes_the_target_to_the_runtime(
    offline_config: SampleConfig,
    factory: Callable[..., Any],
    runtimes: list[FakeRuntime],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def working_task(_config: SampleConfig, task_id: str) -> dict[str, object]:
        del task_id
        return _working_task_row()

    monkeypatch.setattr("demo.web.app.read_task", working_task)
    app = create_app(
        _nms_config(offline_config),
        runs_dir=tmp_path,
        runtime_factory=factory,
        finished_grace_seconds=0.0,
        ui_mode="explorer",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.post(
            "/api/runs",
            json={
                "scenario": "outage",
                "mode": "auto",
                "discovery": "nms",
                "targetTaskId": "task-9",
                "targetContextId": "ctx-9",
            },
        )
        assert response.status_code == 202
        run_id = response.json()["runId"]
        await _wait_status(http_client, run_id, lambda data: data["status"] == "finished")

    assert runtimes[0].run_mode == "modify"
    assert runtimes[0].target_task_id == "task-9"
    assert runtimes[0].target_context_id == "ctx-9"


async def test_client_run_endpoints_return_persisted_runs_and_conversations(
    offline_config: SampleConfig, tmp_path: Path
) -> None:
    db_path = tmp_path / "client.sqlite3"
    store = SqliteClientStore(db_path)
    await store.ensure_conversation(
        conversation_id="conv-1", external_id="ext-1", user_id="user-1", title="energy saving"
    )
    await store.append_turn(conversation_id="conv-1", role="user", content="save energy")
    await store.create_run(
        ClientRunRecord(
            run_id="run-1",
            conversation_id="conv-1",
            scenario="information",
            mode="auto",
            transport="sse",
            discovery="registry",
            user_request="save energy",
            status="finished",
            final_state="task_completed",
        )
    )
    await store.record_negotiation_event(
        NegotiationEventRecord.new(
            run_id="run-1",
            seq=1,
            direction="inbound",
            actor="server",
            kind="information",
            round=1,
            performative="PROPOSE",
            content={"content": "missing region"},
        )
    )
    await store.save_task_snapshot(
        TaskSnapshotRecord(run_id="run-1", task_id="task-1", context_id="ctx-1", state="TASK_STATE_COMPLETED")
    )
    config = replace(offline_config, client_db_path=str(db_path))
    app = create_app(config, runs_dir=tmp_path, client_store=store, finished_grace_seconds=0.0)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        runs = await http_client.get("/api/client/runs")
        assert runs.status_code == 200
        payload = runs.json()
        assert payload["enabled"] is True
        assert payload["runs"][0]["runId"] == "run-1"
        assert payload["runs"][0]["conversationId"] == "conv-1"

        detail = await http_client.get("/api/client/runs/run-1")
        assert detail.status_code == 200
        body = detail.json()
        assert body["status"] == "finished"
        assert body["turns"][0]["content"] == "save energy"
        assert body["negotiationEvents"][0]["performative"] == "PROPOSE"
        assert body["taskSnapshot"]["state"] == "TASK_STATE_COMPLETED"

        conversations = await http_client.get("/api/client/conversations")
        assert conversations.status_code == 200
        assert conversations.json()["conversations"][0]["conversationId"] == "conv-1"

        conversation = await http_client.get("/api/client/conversations/conv-1")
        assert conversation.status_code == 200
        assert conversation.json()["runs"][0]["runId"] == "run-1"

        assert (await http_client.get("/api/client/runs/missing")).status_code == 404


async def test_tasks_endpoint_returns_persisted_task_summaries(offline_config: SampleConfig, tmp_path: Path) -> None:
    db_path = tmp_path / "tasks.sqlite3"
    store = SqliteTaskStore(db_path)
    task = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        artifacts=[
            Artifact(
                artifact_id="artifact-1",
                name="energy saving intent report",
                parts=[Part(text="Plan applied to cell-001.")],
            )
        ],
    )
    await store.save(
        task,
        ServerCallContext(state={}, user=UnauthenticatedUser()),
    )
    config = replace(offline_config, task_db_path=str(db_path))
    app = create_app(config, runs_dir=tmp_path, finished_grace_seconds=0.0)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await http_client.get("/api/tasks")

    assert response.status_code == 200
    document = response.json()
    assert document["enabled"] is True
    assert len(document["tasks"]) == 1
    row = document["tasks"][0]
    assert row["taskId"] == "task-1"
    assert row["contextId"] == "ctx-1"
    assert row["state"] == "TASK_STATE_COMPLETED"
    assert row["summary"] == "Plan applied to cell-001."


async def test_tasks_endpoint_is_disabled_without_a_task_database(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/tasks")

    assert response.status_code == 200
    assert response.json() == {"enabled": False, "tasks": []}


async def test_cli_defaults_are_exposed_and_used_when_the_body_omits_them(
    offline_config: SampleConfig,
    factory: Callable[..., Any],
    tmp_path: Path,
) -> None:
    app = create_app(
        offline_config,
        runs_dir=tmp_path,
        runtime_factory=factory,
        default_scenario="reject",
        default_mode="auto",
        default_transport="webhook",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        meta = (await http_client.get("/api/meta")).json()
        assert meta["defaultScenario"] == "reject"
        assert meta["defaultMode"] == "auto"
        assert meta["defaultTransport"] == "webhook"

        start = await http_client.post("/api/runs", json={})
        assert start.status_code == 202
        run_id = start.json()["runId"]
        status = await _wait_status(http_client, run_id, lambda data: data["status"] == "finished")

    assert status["scenario"] == "reject"
    assert status["mode"] == "auto"
    assert status["transport"] == "webhook"


async def test_intent_mode_is_exposed_and_applied_per_run(
    client: httpx.AsyncClient,
) -> None:
    meta = (await client.get("/api/meta")).json()
    assert meta["defaultIntentMode"] == "raw"
    assert meta["intentModes"] == ["processed", "raw"]

    start = await client.post(
        "/api/runs",
        json={"scenario": "information", "intentMode": "processed"},
    )
    assert start.status_code == 202
    assert start.json()["intentMode"] == "processed"
    run_id = start.json()["runId"]

    status = await _wait_status(client, run_id, lambda data: data["status"] != "running")
    assert status["intentMode"] == "processed"

    invalid = await client.post(
        "/api/runs",
        json={"scenario": "information", "intentMode": "guessed"},
    )
    assert invalid.status_code == 400


async def test_root_serves_the_run_explorer_shell(client: httpx.AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    page = response.text
    assert "RAN energy saving negotiation" in page
    assert "/static/app.css" in page
    assert "/static/js/main.js" in page
    for marker in (
        'id="run-id"',
        'id="status-pill"',
        'id="elapsed"',
        'id="token-total"',
        'id="follow-toggle"',
        'id="context-strip"',
        'id="phase-steps"',
        'id="scenario-select"',
        'id="start-button"',
        'id="analysis"',
        'id="decision-log"',
        'id="timeline-toolbar"',
        'id="timeline"',
        'id="inspector-tabs"',
        'id="inspector-body"',
        'id="connection-state"',
        'id="manual-pinned"',
        'id="shortcuts-dialog"',
    ):
        assert marker in page, marker
    assert 'role="tablist"' in page
    assert 'data-tab="llm-request"' in page
    assert 'data-tab="llm-response"' in page
    assert 'data-tab="tool"' in page
    # Payloads are built as DOM nodes; the shell must not contain an inline script or innerHTML.
    assert "innerHTML" not in page
    assert page.count("<script") == 1


async def test_run_lifecycle_streams_sse_events(client: httpx.AsyncClient, runtimes: list[FakeRuntime]) -> None:
    start = await client.post(
        "/api/runs",
        json={"scenario": "information", "mode": "auto", "transport": "sse"},
    )

    assert start.status_code == 202
    run_id = start.json()["runId"]
    events = await client.get(f"/api/runs/{run_id}/events")
    payloads = _sse_payloads(events.text)
    types = [payload["type"] for payload in payloads]
    assert types[0] == "run_started"
    assert "event" in types
    assert "trace" in types
    # An auto run never exposes a pending manual decision.
    assert "decision_requested" not in types
    assert types[-1] == "finished"
    assert payloads[-1]["finalState"] == FINAL_STATE_TASK_COMPLETED
    assert payloads[-1]["startedAt"], "the Done phase needs a start to render a duration"
    assert [payload["seq"] for payload in payloads] == list(range(1, len(payloads) + 1))

    status = await _wait_status(client, run_id, lambda data: data["status"] == "finished")
    assert status["taskId"] == "task-1"
    assert status["report"]["summary"] == "Plan applied"
    assert status["transcript"]["finalState"] == FINAL_STATE_TASK_COMPLETED
    assert runtimes[0].closed is True


async def test_manual_decision_submission_advances_the_run(
    client: httpx.AsyncClient,
    runtimes: list[FakeRuntime],
) -> None:
    start = await client.post(
        "/api/runs",
        json={"scenario": "information", "mode": "manual", "transport": "sse"},
    )
    assert start.status_code == 202
    run_id = start.json()["runId"]

    pending = await _wait_status(client, run_id, lambda data: data["pendingDecision"] is True)
    assert pending["status"] == "running"

    decision = {
        "action": "accept",
        "rationale": "The region is supplied by the operator.",
        "filled_params": {"region": "Region A"},
    }
    response = await client.post(f"/api/runs/{run_id}/decisions", json=decision)

    assert response.status_code == 200
    finished = await _wait_status(client, run_id, lambda data: data["status"] != "running")
    assert finished["finalState"] == FINAL_STATE_TASK_COMPLETED
    assert finished["transcript"]["decisions"][0]["action"] == "accept"
    assert finished["transcript"]["decisions"][0]["rationale"] == decision["rationale"]
    assert runtimes[0].closed is True


async def test_manual_answer_parse_returns_a_preview_without_submitting(
    client: httpx.AsyncClient,
    runtimes: list[FakeRuntime],
) -> None:
    start = await client.post(
        "/api/runs",
        json={"scenario": "information", "mode": "manual", "transport": "sse"},
    )
    assert start.status_code == 202
    run_id = start.json()["runId"]
    await _wait_status(client, run_id, lambda data: data["pendingDecision"] is True)

    response = await client.post(
        f"/api/runs/{run_id}/decisions/parse",
        json={"text": "The served region is Region A."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision"]["action"] == "accept"
    assert payload["decision"]["filled_params"] == {"region": "Region A"}
    assert payload["missingItems"] == ["region"]
    # Parsing is a preview only: the run is still waiting for the confirmed decision.
    status = await client.get(f"/api/runs/{run_id}")
    assert status.json()["pendingDecision"] is True

    submitted = await client.post(
        f"/api/runs/{run_id}/decisions",
        json=payload["decision"],
    )
    assert submitted.status_code == 200
    finished = await _wait_status(client, run_id, lambda data: data["status"] != "running")
    assert finished["finalState"] == FINAL_STATE_TASK_COMPLETED


async def test_manual_answer_parse_rejects_a_run_without_a_structurer(
    client: httpx.AsyncClient,
    runtimes: list[FakeRuntime],
) -> None:
    start = await client.post(
        "/api/runs",
        json={"scenario": "information", "mode": "manual", "transport": "sse"},
    )
    run_id = start.json()["runId"]
    await _wait_status(client, run_id, lambda data: data["pendingDecision"] is True)
    runtimes[0].manual_structurer = None

    response = await client.post(
        f"/api/runs/{run_id}/decisions/parse",
        json={"text": "Region A"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "manual_parse_unavailable"


async def test_cancel_endpoint_stops_a_running_run(
    client: httpx.AsyncClient,
    runtimes: list[FakeRuntime],
) -> None:
    """The presentation Cancel button stops a long-running task through the API."""
    start = await client.post(
        "/api/runs",
        json={"scenario": "information", "mode": "manual", "transport": "sse"},
    )
    assert start.status_code == 202
    run_id = start.json()["runId"]
    await _wait_status(client, run_id, lambda data: data["pendingDecision"] is True)

    response = await client.post(f"/api/runs/{run_id}/cancel")

    assert response.status_code == 202
    assert response.json() == {"runId": run_id, "status": "cancelled"}
    finished = await _wait_status(client, run_id, lambda data: data["status"] != "running")
    assert finished["status"] == "interrupted"
    assert finished["error"] == "The run was cancelled by the operator."
    assert runtimes[0].closed is True

    again = await client.post(f"/api/runs/{run_id}/cancel")
    assert again.status_code == 409
    assert again.json()["code"] == "run_not_active"


async def test_cancel_unknown_run_is_not_found(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/runs/missing/cancel")
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_run"


async def test_each_decision_produces_exactly_one_timeline_row(
    client: httpx.AsyncClient,
) -> None:
    """The consumer callback owns the decision row; no adjacent ``decision_made`` span remains."""
    start = await client.post(
        "/api/runs",
        json={"scenario": "information", "mode": "auto", "transport": "sse"},
    )
    run_id = start.json()["runId"]
    payloads = _sse_payloads((await client.get(f"/api/runs/{run_id}/events")).text)

    decision_entries = [
        payload["event"]
        for payload in payloads
        if payload["type"] == "event" and payload["event"].get("entryType") == "decision"
    ]
    decision_spans = [
        payload["event"]
        for payload in payloads
        if payload["type"] == "trace" and payload["event"].get("kind") == "decision_made"
    ]
    assert len(decision_entries) == 1
    assert decision_spans == []
    assert decision_entries[0]["action"] == "accept"


async def test_manual_decision_row_is_attributed_to_the_human(
    client: httpx.AsyncClient,
) -> None:
    """A manual decision is the operator's, not the client's."""
    start = await client.post(
        "/api/runs",
        json={"scenario": "information", "mode": "manual", "transport": "sse"},
    )
    run_id = start.json()["runId"]
    await _wait_status(client, run_id, lambda data: data["pendingDecision"] is True)
    response = await client.post(
        f"/api/runs/{run_id}/decisions",
        json={"action": "accept", "rationale": "The operator accepts the proposal."},
    )
    assert response.status_code == 200
    await _wait_status(client, run_id, lambda data: data["status"] != "running")

    payloads = _sse_payloads((await client.get(f"/api/runs/{run_id}/events")).text)
    decisions = [
        payload["event"]
        for payload in payloads
        if payload["type"] == "event" and payload["event"].get("entryType") == "decision"
    ]
    assert len(decisions) == 1
    span = await client.get(f"/api/runs/{run_id}/spans/{decisions[0]['spanId']}")
    assert span.status_code == 200
    assert span.json()["actor"] == "human"


async def test_auto_run_does_not_expose_a_pending_manual_decision(
    client: httpx.AsyncClient,
    runtimes: list[FakeRuntime],
) -> None:
    start = await client.post(
        "/api/runs",
        json={"scenario": "information", "mode": "auto", "transport": "sse"},
    )
    assert start.status_code == 202
    run_id = start.json()["runId"]

    events = await client.get(f"/api/runs/{run_id}/events")
    payloads = _sse_payloads(events.text)
    assert [payload["type"] for payload in payloads if payload["type"] == "decision_requested"] == []

    status = await _wait_status(client, run_id, lambda data: data["status"] == "finished")
    assert status["mode"] == "auto"
    assert status["pendingDecision"] is False
    response = await client.post(
        f"/api/runs/{run_id}/decisions",
        json={"action": "accept", "rationale": "There is no manual decision to answer."},
    )
    assert response.status_code == 409


async def test_manual_run_publishes_a_manual_decision_request(
    client: httpx.AsyncClient,
    runtimes: list[FakeRuntime],
) -> None:
    start = await client.post(
        "/api/runs",
        json={"scenario": "information", "mode": "manual", "transport": "sse"},
    )
    assert start.status_code == 202
    run_id = start.json()["runId"]
    events_task = asyncio.create_task(client.get(f"/api/runs/{run_id}/events"))

    pending = await _wait_status(client, run_id, lambda data: data["pendingDecision"] is True)
    assert pending["mode"] == "manual"

    response = await client.post(
        f"/api/runs/{run_id}/decisions",
        json={"action": "accept", "rationale": "The operator accepts the proposal."},
    )
    assert response.status_code == 200
    events = await asyncio.wait_for(events_task, timeout=5)
    requests = [payload for payload in _sse_payloads(events.text) if payload["type"] == "decision_requested"]
    assert len(requests) == 1
    assert requests[0]["mode"] == "manual"
    # The manual countdown reads the provider budget and the request time from the frame.
    assert requests[0]["timeoutSeconds"] == 5.0
    assert isinstance(requests[0]["requestedAt"], str)
    assert requests[0]["requestedAt"].endswith("Z")
    await _wait_status(client, run_id, lambda data: data["status"] != "running")


async def test_confirm_run_publishes_the_recommendation_with_the_decision(
    client: httpx.AsyncClient,
    runtimes: list[FakeRuntime],
) -> None:
    start = await client.post(
        "/api/runs",
        json={"scenario": "information", "mode": "confirm", "transport": "sse"},
    )
    assert start.status_code == 202
    run_id = start.json()["runId"]
    events_task = asyncio.create_task(client.get(f"/api/runs/{run_id}/events"))

    pending = await _wait_status(client, run_id, lambda data: data["pendingDecision"] is True)
    assert pending["mode"] == "confirm"

    response = await client.post(
        f"/api/runs/{run_id}/decisions",
        json={"action": "accept", "rationale": "The engineer confirmed the recommendation."},
    )
    assert response.status_code == 200
    events = await asyncio.wait_for(events_task, timeout=5)
    requests = [payload for payload in _sse_payloads(events.text) if payload["type"] == "decision_requested"]
    assert len(requests) == 1
    assert requests[0]["mode"] == "confirm"
    assert requests[0]["recommendation"]["action"] == "accept"
    assert requests[0]["proposal"]["kind"] == "information"
    assert requests[0]["timeoutSeconds"] == 5.0
    await _wait_status(client, run_id, lambda data: data["status"] != "running")


async def test_intent_review_gate_blocks_the_task_until_the_engineer_confirms(
    offline_config: SampleConfig,
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(intent_gate=True)

    async def factory(
        config: SampleConfig,
        scenario: str,
        mode: str,
        transport: str,
        discovery: str,
        *,
        protocol: str = PROTOCOL_A2A_T,
        run_id: str = "",
    ) -> FakeRuntime:
        del config, scenario, mode, transport, discovery, protocol, run_id
        return runtime

    app = create_app(
        offline_config,
        runs_dir=tmp_path,
        runtime_factory=factory,
        finished_grace_seconds=0.0,
        ui_mode="presentation",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        start = await client.post(
            "/api/runs",
            json={"scenario": "information", "mode": "confirm", "transport": "sse"},
        )
        assert start.status_code == 202
        run_id = start.json()["runId"]
        events_task = asyncio.create_task(client.get(f"/api/runs/{run_id}/events"))

        pending = await _wait_status(client, run_id, lambda data: data["pendingIntent"] is True)
        assert pending["pendingIntent"] is True
        response = await client.post(
            f"/api/runs/{run_id}/intent",
            json={"approved": True},
        )
        assert response.status_code == 200
        final = await _wait_status(client, run_id, lambda data: data["status"] != "running")
        assert final["finalState"] == "task_completed"
        events = await asyncio.wait_for(events_task, timeout=5)
        frames = [payload for payload in _sse_payloads(events.text) if payload["type"] == "intent_review_requested"]
        assert len(frames) == 1
        assert frames[0]["promptText"].startswith("## Operation Type")


async def test_second_start_is_rejected_while_a_run_is_active(
    client: httpx.AsyncClient,
    runtimes: list[FakeRuntime],
) -> None:
    first = await client.post("/api/runs", json={"scenario": "information", "mode": "manual", "transport": "sse"})
    assert first.status_code == 202
    run_id = first.json()["runId"]
    conversation_id = first.json()["conversationId"]
    await _wait_status(client, run_id, lambda data: data["pendingDecision"] is True)

    second = await client.post(
        "/api/runs",
        json={
            "scenario": "information",
            "mode": "auto",
            "transport": "sse",
            "conversationId": conversation_id,
        },
    )

    assert second.status_code == 409
    await client.post(
        f"/api/runs/{run_id}/decisions",
        json={"action": "accept", "rationale": "ok"},
    )
    await _wait_status(client, run_id, lambda data: data["status"] != "running")


async def test_start_reuses_the_conversation_and_trace_id_of_a_retry(
    client: httpx.AsyncClient,
) -> None:
    """An API caller can still pin the conversation and trace explicitly."""
    trace_id = "0123456789abcdef0123456789abcdef"
    response = await client.post(
        "/api/runs",
        json={
            "scenario": "information",
            "mode": "auto",
            "transport": "sse",
            "conversationId": "conv-retry",
            "traceId": trace_id,
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["conversationId"] == "conv-retry"
    assert payload["traceId"] == trace_id

    snapshot = (await client.get(f"/api/runs/{payload['runId']}")).json()
    assert snapshot["conversationId"] == "conv-retry"
    assert snapshot["traceId"] == trace_id


async def test_start_defaults_the_trace_id_to_the_run_id(client: httpx.AsyncClient) -> None:
    """The first attempt already exposes the id a blocked retry will send to the vendor."""
    response = await client.post(
        "/api/runs",
        json={"scenario": "information", "mode": "auto", "transport": "sse"},
    )

    payload = response.json()
    assert payload["traceId"] == payload["runId"]


async def test_start_keeps_the_chain_across_a_retry(client: httpx.AsyncClient) -> None:
    """A retry shares the chain id and names its parent, but gets its own conversation/trace."""
    first = (
        await client.post(
            "/api/runs",
            json={"scenario": "information", "mode": "auto", "transport": "sse"},
        )
    ).json()
    assert first["chainId"] == first["conversationId"]

    response = await client.post(
        "/api/runs",
        json={
            "scenario": "information",
            "mode": "auto",
            "transport": "sse",
            "chainId": first["chainId"],
            "parentRunId": first["runId"],
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["chainId"] == first["chainId"]
    assert payload["parentRunId"] == first["runId"]
    # The corrected attempt is a new execution and a new conversation.
    assert payload["runId"] != first["runId"]
    assert payload["conversationId"] != first["conversationId"]
    assert payload["traceId"] != first["traceId"]

    snapshot = (await client.get(f"/api/runs/{payload['runId']}")).json()
    assert snapshot["chainId"] == first["chainId"]
    assert snapshot["parentRunId"] == first["runId"]


async def test_start_rejects_an_invalid_trace_id(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/runs",
        json={
            "scenario": "information",
            "mode": "auto",
            "transport": "sse",
            "traceId": "not-a-trace-id",
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_run"


async def test_unknown_run_returns_404(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/runs/missing")).status_code == 404
    assert (await client.get("/api/runs/missing/events")).status_code == 404
    assert (
        await client.post("/api/runs/missing/decisions", json={"action": "accept", "rationale": "ok"})
    ).status_code == 404
    assert (await client.get("/api/runs/missing/transcript.json")).status_code == 404
    assert (await client.get("/api/runs/missing/transcript.md")).status_code == 404


async def test_decision_without_a_pending_request_returns_409(client: httpx.AsyncClient) -> None:
    start = await client.post("/api/runs", json={"scenario": "information", "mode": "auto", "transport": "sse"})
    run_id = start.json()["runId"]
    await _wait_status(client, run_id, lambda data: data["status"] == "finished")

    response = await client.post(
        f"/api/runs/{run_id}/decisions",
        json={"action": "accept", "rationale": "too late"},
    )

    assert response.status_code == 409


async def test_invalid_start_bodies_are_rejected(client: httpx.AsyncClient) -> None:
    bad_scenario = {"scenario": "nope", "mode": "auto", "transport": "sse"}
    bad_mode = {"scenario": "information", "mode": "x", "transport": "sse"}
    bad_transport = {"scenario": "information", "mode": "auto", "transport": "x"}

    assert (await client.post("/api/runs", json=bad_scenario)).status_code == 400
    assert (await client.post("/api/runs", json=bad_mode)).status_code == 400
    assert (await client.post("/api/runs", json=bad_transport)).status_code == 400


async def test_transcript_downloads(client: httpx.AsyncClient) -> None:
    start = await client.post("/api/runs", json={"scenario": "information", "mode": "auto", "transport": "sse"})
    run_id = start.json()["runId"]
    await _wait_status(client, run_id, lambda data: data["status"] == "finished")

    json_response = await client.get(f"/api/runs/{run_id}/transcript.json")
    markdown_response = await client.get(f"/api/runs/{run_id}/transcript.md")

    assert json_response.status_code == 200
    assert "application/json" in json_response.headers["content-type"]
    document = json_response.json()
    assert document["finalState"] == FINAL_STATE_TASK_COMPLETED
    assert document["scenario"] == "information"
    assert [entry["seq"] for entry in document["timeline"]] == sorted(entry["seq"] for entry in document["timeline"])
    assert markdown_response.status_code == 200
    assert "text/markdown" in markdown_response.headers["content-type"]
    assert "RAN energy-saving negotiation transcript" in markdown_response.text
    assert "| # | Seq | Phase | Step |" in markdown_response.text


async def test_meta_exposes_the_default_request_per_scenario(client: httpx.AsyncClient) -> None:
    meta = (await client.get("/api/meta")).json()

    for scenario in meta["scenarios"]:
        assert meta["scenarioRequests"][scenario] == resolve_user_request(get_scenario(scenario), "en-US")


def test_the_profiled_catalog_pre_fills_the_vendor_region() -> None:
    """A real-NMS page must not show the neutral demo region it will never send."""
    from demo.catalog import DemoRunCatalog
    from demo.web.app import _scenario_request

    catalog = DemoRunCatalog(
        region="A2ATestRegion",
        sites=("NRES_XCS_6102937", "LTE_26441_LNR_OCL"),
    )

    request = _scenario_request(catalog, "information", "en-US")

    assert "A2ATestRegion" in request
    assert "Region A" not in request


async def test_custom_user_request_reaches_run_started_and_runtime(
    client: httpx.AsyncClient,
    runtimes: list[FakeRuntime],
) -> None:
    custom = "Create a RAN energy saving task for Region A at 30% saving."
    start = await client.post(
        "/api/runs",
        json={
            "scenario": "information",
            "mode": "auto",
            "transport": "sse",
            "userRequest": custom,
        },
    )
    assert start.status_code == 202
    run_id = start.json()["runId"]
    await _wait_status(client, run_id, lambda data: data["status"] == "finished")

    first = _sse_payloads((await client.get(f"/api/runs/{run_id}/events")).text)[0]
    assert first["userRequest"] == custom
    assert runtimes[-1].user_request == custom


async def test_blank_user_request_falls_back_to_the_scenario_default(
    client: httpx.AsyncClient,
) -> None:
    start = await client.post(
        "/api/runs",
        json={"scenario": "information", "mode": "auto", "transport": "sse", "userRequest": "   "},
    )
    assert start.status_code == 202
    run_id = start.json()["runId"]
    await _wait_status(client, run_id, lambda data: data["status"] == "finished")

    first = _sse_payloads((await client.get(f"/api/runs/{run_id}/events")).text)[0]
    assert first["userRequest"] == resolve_user_request(get_scenario("information"), "en-US")


async def test_non_string_user_request_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/runs",
        json={"scenario": "information", "mode": "auto", "transport": "sse", "userRequest": 42},
    )

    assert response.status_code == 400
    assert "userRequest" in response.json()["error"]


async def test_run_started_is_the_first_replayable_frame(client: httpx.AsyncClient) -> None:
    start = await client.post(
        "/api/runs",
        json={"scenario": "information", "mode": "auto", "transport": "sse", "discovery": "direct"},
    )
    assert start.status_code == 202
    assert start.json()["discovery"] == "direct"
    run_id = start.json()["runId"]
    await _wait_status(client, run_id, lambda data: data["status"] == "finished")

    payloads = _sse_payloads((await client.get(f"/api/runs/{run_id}/events")).text)
    first = payloads[0]
    spec = get_scenario("information")
    assert first["type"] == "run_started"
    assert first["seq"] == 1
    assert first["runId"] == run_id
    assert first["description"] == spec.description
    assert first["userRequest"] == resolve_user_request(spec, "en-US")
    assert first["language"] == "en-US"
    assert first["mode"] == "auto"
    assert first["transport"] == "sse"
    assert first["discovery"] == "direct"
    assert first["modelMode"] == "offline"
    assert first["modelLabel"] == "offline (scripted)"
    assert first["expectedSequence"] == [
        {"actor": step.actor, "performative": step.performative, "round": step.round} for step in spec.expected_sequence
    ]
    assert first["phase"] == "dispatch"
    assert first["spanId"]

    # A reconnect replays the same ordered messages without duplicating seq values.
    replay = _sse_payloads((await client.get(f"/api/runs/{run_id}/events")).text)
    assert replay[0] == first
    assert [payload["seq"] for payload in replay] == list(range(1, len(replay) + 1))


async def test_live_decision_is_recorded_before_the_task_events(client: httpx.AsyncClient) -> None:
    start = await client.post("/api/runs", json={"scenario": "information", "mode": "auto", "transport": "sse"})
    run_id = start.json()["runId"]
    await _wait_status(client, run_id, lambda data: data["status"] == "finished")

    payloads = _sse_payloads((await client.get(f"/api/runs/{run_id}/events")).text)
    entries = [payload["event"] for payload in payloads if payload["type"] == "event"]
    decision_index = next(index for index, entry in enumerate(entries) if entry["eventKind"] == "decision")
    task_index = next(index for index, entry in enumerate(entries) if entry["eventKind"] == "status")
    assert decision_index < task_index
    assert entries[decision_index]["action"] == "accept"
    assert entries[decision_index]["spanId"]


async def test_trace_ingestion_stays_reachable_when_api_auth_is_enabled(
    offline_config: SampleConfig,
    factory: Callable[..., Any],
    tmp_path: Path,
) -> None:
    """The run-scoped trace token works on its own; every other API route keeps the bearer."""
    app = create_app(
        replace(offline_config, auth_token="secret"),
        runs_dir=tmp_path,
        runtime_factory=factory,
        finished_grace_seconds=0.0,
    )
    manager = app.state.run_manager
    run = await manager.start("information", "auto", "sse")
    deadline = time.monotonic() + 5.0
    while not run.finished() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert run.finished()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        accepted = await client.post(
            f"/api/runs/{run.run_id}/trace",
            json=[{"spanId": "server-span-auth", "kind": "tool", "phase": "execution"}],
            headers={"x-a2at-trace-token": TRACE_TOKEN},
        )
        rejected = await client.post(
            f"/api/runs/{run.run_id}/trace",
            json=[{"spanId": "server-span-bad", "kind": "tool", "phase": "execution"}],
            headers={"x-a2at-trace-token": "wrong"},
        )
        other = await client.get("/api/meta")
        metrics = await client.get("/metrics")

    assert accepted.status_code == 202
    assert rejected.status_code == 403
    assert other.status_code == 401
    assert metrics.status_code == 401


async def test_trace_json_span_lookup_and_ingestion(client: httpx.AsyncClient) -> None:
    start = await client.post("/api/runs", json={"scenario": "information", "mode": "auto", "transport": "sse"})
    run_id = start.json()["runId"]
    await _wait_status(client, run_id, lambda data: data["status"] == "finished")

    trace_response = await client.get(f"/api/runs/{run_id}/trace.json")
    assert trace_response.status_code == 200
    document = trace_response.json()
    assert document["runId"] == run_id
    assert document["schemaVersion"] == 1
    assert document["transcript"]["finalState"] == FINAL_STATE_TASK_COMPLETED
    spans = document["spans"]
    assert spans
    assert [span["seq"] for span in spans] == sorted(span["seq"] for span in spans)
    assert {span["phase"] for span in spans} >= {"dispatch", "task_prompt", "done"}

    lookup = await client.get(f"/api/runs/{run_id}/spans/{spans[0]['spanId']}")
    assert lookup.status_code == 200
    assert lookup.json()["spanId"] == spans[0]["spanId"]
    assert (await client.get(f"/api/runs/{run_id}/spans/missing-span")).status_code == 404

    remote = {
        "spanId": "server-span-1",
        "parentSpanId": "client-span",
        "kind": "llm",
        "phase": "execution",
        "phaseStep": 4,
        "round": None,
        "actor": "llm",
        "side": "server",
        "title": "Server LLM call",
        "status": "ok",
        "detail": {"model": "fake-model"},
    }
    accepted = await client.post(
        f"/api/runs/{run_id}/trace",
        json=[remote],
        headers={"x-a2at-trace-token": TRACE_TOKEN},
    )
    assert accepted.status_code == 202
    assert accepted.json() == {"accepted": 1, "runId": run_id}

    stored = (await client.get(f"/api/runs/{run_id}/spans/server-span-1")).json()
    assert stored["runId"] == run_id
    assert stored["seq"] > spans[-1]["seq"]
    assert stored["detail"]["model"] == "fake-model"

    replay = _sse_payloads((await client.get(f"/api/runs/{run_id}/events")).text)
    ingested = [
        payload for payload in replay if payload["type"] == "trace" and payload["event"]["spanId"] == "server-span-1"
    ]
    assert len(ingested) == 1
    assert ingested[0]["seq"] == stored["seq"]

    wrapped = await client.post(
        f"/api/runs/{run_id}/trace",
        json={"spans": [{"spanId": "server-span-2", "kind": "tool", "phase": "execution"}]},
        headers={"x-a2at-trace-token": TRACE_TOKEN},
    )
    assert wrapped.status_code == 202
    assert (await client.get(f"/api/runs/{run_id}/spans/server-span-2")).status_code == 200

    # A retried batch is deduplicated by the original span id: no new row, no new SSE frame.
    repeat = await client.post(
        f"/api/runs/{run_id}/trace",
        json=[remote],
        headers={"x-a2at-trace-token": TRACE_TOKEN},
    )
    assert repeat.status_code == 202
    assert repeat.json() == {"accepted": 0, "runId": run_id, "deduplicated": 1}
    replay_after = _sse_payloads((await client.get(f"/api/runs/{run_id}/events")).text)
    assert (
        len(
            [
                payload
                for payload in replay_after
                if payload["type"] == "trace" and payload["event"]["spanId"] == "server-span-1"
            ]
        )
        == 1
    )


async def test_late_span_batch_is_recoverable_from_trace_json(
    client: httpx.AsyncClient,
) -> None:
    """When the stream grace expires first, the late batch is still in ``trace.json``.

    This is the contract behind the finalize merge: the client re-reads the trace document and
    merges by ``seq``/``spanId`` instead of assuming the SSE stream carried every server span.
    """
    start = await client.post("/api/runs", json={"scenario": "information", "mode": "auto", "transport": "sse"})
    run_id = start.json()["runId"]
    await _wait_status(client, run_id, lambda data: data["status"] == "finished")

    frames = _sse_payloads((await client.get(f"/api/runs/{run_id}/events")).text)
    assert frames[-1]["type"] == "finished"

    late_span = _trace_span("llm", phase="execution", title="Late server LLM call")
    late_span.update({"spanId": "server-late-2", "side": "server", "actor": "llm"})
    posted = await client.post(
        f"/api/runs/{run_id}/trace",
        json=[late_span],
        headers={"x-a2at-trace-token": TRACE_TOKEN},
    )
    assert posted.status_code == 202

    document = (await client.get(f"/api/runs/{run_id}/trace.json")).json()
    late = next(span for span in document["spans"] if span["spanId"] == "server-late-2")
    assert late["seq"] > frames[-1]["seq"]
    assert late["phase"] == "execution"


async def test_events_stream_keeps_forwarding_spans_posted_after_finished(
    offline_config: SampleConfig,
    factory: Callable[..., Any],
    tmp_path: Path,
) -> None:
    """A server trace batch that lands after ``finished`` still reaches the live subscriber."""
    app = create_app(
        offline_config,
        runs_dir=tmp_path,
        runtime_factory=factory,
        finished_grace_seconds=2.0,
    )
    late_span = _trace_span("llm", phase="execution", title="Late server LLM call")
    late_span.update({"spanId": "server-late-1", "side": "server", "actor": "llm"})
    frames: list[dict[str, Any]] = []

    with running_http_app(app) as base_url:
        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as http_client:
            start = await http_client.post(
                "/api/runs",
                json={"scenario": "information", "mode": "auto", "transport": "sse"},
            )
            run_id = start.json()["runId"]
            await _wait_status(http_client, run_id, lambda data: data["status"] == "finished")

            async with http_client.stream("GET", f"/api/runs/{run_id}/events") as response:
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    frame = json.loads(line[len("data:") :].strip())
                    frames.append(frame)
                    if frame.get("type") == "finished":
                        # The real cross-process path: the server process posts its last batch.
                        posted = await http_client.post(
                            f"/api/runs/{run_id}/trace",
                            json=[late_span],
                            headers={"x-a2at-trace-token": TRACE_TOKEN},
                        )
                        assert posted.status_code == 202
                    if frame.get("type") == "trace" and frame["event"]["spanId"] == "server-late-1":
                        break

    types = [frame["type"] for frame in frames]
    assert types[0] == "run_started"
    finished_index = types.index("finished")
    late_index = next(
        index
        for index, frame in enumerate(frames)
        if frame["type"] == "trace" and frame["event"]["spanId"] == "server-late-1"
    )
    assert finished_index < late_index
    assert frames[late_index]["seq"] > frames[finished_index]["seq"]


async def test_trace_ingestion_validates_the_run_and_payload(client: httpx.AsyncClient) -> None:
    start = await client.post("/api/runs", json={"scenario": "information", "mode": "auto", "transport": "sse"})
    run_id = start.json()["runId"]
    await _wait_status(client, run_id, lambda data: data["status"] == "finished")

    assert (await client.post("/api/runs/missing/trace", json=[])).status_code == 404
    trace_headers = {"x-a2at-trace-token": TRACE_TOKEN}
    assert (await client.post(f"/api/runs/{run_id}/trace", json=42, headers=trace_headers)).status_code == 400
    assert (await client.post(f"/api/runs/{run_id}/trace", json=[1, 2], headers=trace_headers)).status_code == 400
    malformed = await client.post(
        f"/api/runs/{run_id}/trace",
        content=b"not-json",
        headers={"content-type": "application/json", "x-a2at-trace-token": TRACE_TOKEN},
    )
    assert malformed.status_code == 400

    assert (
        await client.post(
            f"/api/runs/{run_id}/trace",
            json=[{"spanId": "s", "kind": "llm", "phase": "execution"}],
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/runs/{run_id}/trace",
            json=[{"spanId": "s", "kind": "llm", "phase": "execution"}],
            headers={"x-a2at-trace-token": "wrong"},
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/runs/{run_id}/trace",
            json=[{"spanId": "s", "kind": "llm", "phase": "nope"}],
            headers={"x-a2at-trace-token": TRACE_TOKEN},
        )
    ).status_code == 400
    assert (
        await client.post(
            f"/api/runs/{run_id}/trace",
            json=[{"spanId": "s", "phase": "execution"}],
            headers={"x-a2at-trace-token": TRACE_TOKEN},
        )
    ).status_code == 400
    assert (
        await client.post(
            f"/api/runs/{run_id}/trace",
            json=[{"spanId": "s", "kind": "llm", "phase": "execution", "runId": "other"}],
            headers={"x-a2at-trace-token": TRACE_TOKEN},
        )
    ).status_code == 400


async def test_real_offline_information_run_has_unique_spans_and_all_phases(
    english_language: None,
    tmp_path: Path,
) -> None:
    """The real client flow (not a double) must keep run-wide span ids unique and all 8 phases."""
    del english_language  # fixture side effect: pin the scripted SDK language
    config = offline_direct_config(free_tcp_port())
    with running_server(config):
        app = create_app(config, runs_dir=tmp_path, finished_grace_seconds=0.0)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
            start = await http_client.post(
                "/api/runs",
                json={
                    "scenario": "information",
                    "mode": "auto",
                    "transport": "sse",
                    "discovery": "direct",
                },
            )
            assert start.status_code == 202
            run_id = start.json()["runId"]
            status = await _wait_status(
                http_client,
                run_id,
                lambda data: data["status"] != "running",
                timeout=30.0,
            )
            assert status["status"] == "finished"
            assert status["finalState"] == FINAL_STATE_TASK_COMPLETED

            payloads = _sse_payloads((await http_client.get(f"/api/runs/{run_id}/events")).text)
            run_started = payloads[0]
            assert run_started["type"] == "run_started"
            _assert_seq_order(payloads)
            assert payloads[-1]["type"] == "finished"
            assert payloads[-1]["finalState"] == FINAL_STATE_TASK_COMPLETED

            # The decision is recorded when the provider returns, before the task events it causes.
            event_entries = [payload["event"] for payload in payloads if payload["type"] == "event"]
            decision_index = next(
                index for index, entry in enumerate(event_entries) if entry.get("eventKind") == "decision"
            )
            later_task_index = next(
                index
                for index, entry in enumerate(event_entries)
                if index > decision_index and entry.get("kind") in {"status", "artifact"}
            )
            assert decision_index < later_task_index
            assert event_entries[decision_index]["action"] == "accept"
            assert event_entries[decision_index]["spanId"]

            looked_up = await http_client.get(f"/api/runs/{run_id}/spans/{run_started['spanId']}")
            assert looked_up.status_code == 200
            assert looked_up.json()["kind"] == "run_started"

            document = (await http_client.get(f"/api/runs/{run_id}/trace.json")).json()
            assert document["status"] == "finished"
            assert document["transcript"]["report"] == status["report"]
            assert status["report"]
            spans = document["spans"]
            span_ids = [span["spanId"] for span in spans]
            assert len(span_ids) == len(set(span_ids))
            assert span_ids.count(run_started["spanId"]) == 1
            assert {span["spanId"]: span["kind"] for span in spans}[run_started["spanId"]] == "run_started"

            phases = {span["phase"] for span in spans}
            assert set(PHASES) <= phases

            order: list[str] = []
            for span in spans:
                phase = str(span["phase"])
                if phase not in order:
                    order.append(phase)
            assert order == list(PHASES)

            grouped: dict[str, list[object]] = {}
            for span in spans:
                grouped.setdefault(str(span["phase"]), []).append(span["phaseStep"])
            for steps in grouped.values():
                assert steps == list(range(1, len(steps) + 1))

            # Report is the final artifact plus the COMPLETED status; ADK per-tool artifacts are
            # execution rows, not report rows.
            report_spans = [span for span in spans if span["phase"] == "report"]
            assert len(report_spans) == 2
            report_kinds = sorted(str(span["kind"]) for span in report_spans)
            assert report_kinds == ["artifact", "status"]
            artifact_spans = [span for span in spans if span["kind"] == "artifact"]
            assert any(span["phase"] == "execution" for span in artifact_spans)
            assert all(span["phase"] in {"execution", "report"} for span in artifact_spans)

            # ADK is client-only now. The server still makes A2A-T SDK LLM calls in the gate and
            # runs the LangGraph planner, but it has no ADK model; only the client has ADK LLM
            # spans. The LangGraph executor produces the server planner and tool spans.
            llm_spans = [span for span in spans if span["kind"] == "llm"]
            tool_spans = [span for span in spans if span["kind"] == "tool"]
            assert llm_spans and tool_spans
            server_llm = [span for span in llm_spans if span["side"] == "server"]
            assert server_llm
            assert any(span["model"] == "scripted" for span in server_llm)
            assert any(span["stage"] == "server_planner" for span in server_llm)
            tool_names = {span["title"] for span in tool_spans if span["side"] == "server"}
            assert {
                "get_cell_energy_profile",
                "evaluate_energy_saving_plan",
                "apply_energy_saving_plan",
                "generate_energy_saving_report",
            } <= tool_names
            assert any(span["side"] == "client" for span in llm_spans)
            assert {span["side"] for span in tool_spans} == {"server"}
            for span in llm_spans:
                assert span["stage"]
                assert isinstance(span["durationMs"], (int, float))
                detail = span["detail"]
                assert isinstance(detail, dict)
                assert detail.get("request")
                assert detail.get("response") is not None
                request = detail["request"]
                assert isinstance(request, dict)
                # SDK spans carry the provider; ADK spans carry the model contents.
                assert detail.get("provider") or "contents" in request
            scripted = [span for span in llm_spans if span["model"] == "scripted"]
            assert scripted
            assert all(span["tokens"]["label"] == "n/a (scripted)" for span in scripted)
            for span in tool_spans:
                detail = span["detail"]
                assert isinstance(detail, dict)
                assert detail.get("toolName")
                assert "args" in detail and "result" in detail

            # The real frame stream drives the in-repo JS store harness (skipped without Node).
            node = shutil.which("node")
            if node is not None:
                frames_path = tmp_path / "frames.json"
                frames_path.write_text(json.dumps(payloads, ensure_ascii=False), encoding="utf-8")
                harness = Path(__file__).resolve().parent / "js" / "frames.test.mjs"
                result = subprocess.run(
                    [node, str(harness), str(frames_path)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                assert result.returncode == 0, result.stderr or result.stdout
                assert "real-frame store checks passed" in result.stdout


async def test_real_offline_manual_run_waits_then_records_a_human_decision(
    english_language: None,
    tmp_path: Path,
) -> None:
    """A real manual run blocks on the UI, then the submitted decision becomes a human row."""
    del english_language  # fixture side effect: pin the scripted SDK language
    config = offline_direct_config(free_tcp_port())
    with running_server(config):
        app = create_app(config, runs_dir=tmp_path, finished_grace_seconds=0.0)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
            start = await http_client.post(
                "/api/runs",
                json={"scenario": "information", "mode": "manual", "transport": "sse", "discovery": "direct"},
            )
            assert start.status_code == 202
            run_id = start.json()["runId"]

            pending = await _wait_status(
                http_client,
                run_id,
                lambda data: data["pendingDecision"] is True,
                timeout=30.0,
            )
            assert pending["status"] == "running"
            assert pending["mode"] == "manual"

            submitted = await http_client.post(
                f"/api/runs/{run_id}/decisions",
                json={
                    "action": "accept",
                    "rationale": "The operator accepts the default Task-T parameters.",
                },
            )
            assert submitted.status_code == 200

            finished = await _wait_status(
                http_client,
                run_id,
                lambda data: data["status"] != "running",
                timeout=30.0,
            )
            assert finished["status"] == "finished"
            assert finished["finalState"] == FINAL_STATE_TASK_COMPLETED
            assert finished["transcript"]["decisions"][0]["action"] == "accept"

            payloads = _sse_payloads((await http_client.get(f"/api/runs/{run_id}/events")).text)
            assert payloads[0]["type"] == "run_started"
            _assert_seq_order(payloads)
            requests = [payload for payload in payloads if payload["type"] == "decision_requested"]
            assert len(requests) == 1
            assert requests[0]["mode"] == "manual"
            assert requests[0]["timeoutSeconds"] == 300.0

            spans = (await http_client.get(f"/api/runs/{run_id}/trace.json")).json()["spans"]
            decisions = [span for span in spans if span["kind"] == "accept"]
            assert decisions and decisions[0]["actor"] == "human"


async def test_start_accepts_discovery_and_rejects_unknown_values(client: httpx.AsyncClient) -> None:
    accepted = await client.post(
        "/api/runs",
        json={"scenario": "information", "mode": "auto", "transport": "sse", "discovery": "direct"},
    )
    assert accepted.status_code == 202
    run_id = accepted.json()["runId"]
    status = await _wait_status(client, run_id, lambda data: data["status"] == "finished")
    assert status["discovery"] == "direct"

    rejected = await client.post(
        "/api/runs",
        json={"scenario": "information", "mode": "auto", "transport": "sse", "discovery": "nope"},
    )
    assert rejected.status_code == 400


async def test_start_accepts_the_plain_protocol_and_rejects_unknown_values(
    client: httpx.AsyncClient,
) -> None:
    accepted = await client.post("/api/runs", json={"scenario": "information", "mode": "auto", "protocol": "plain"})
    assert accepted.status_code == 202
    assert accepted.json()["protocol"] == "plain"
    run_id = accepted.json()["runId"]
    status = await _wait_status(client, run_id, lambda data: data["status"] == "finished")
    assert status["protocol"] == "plain"

    rejected = await client.post("/api/runs", json={"scenario": "information", "mode": "auto", "protocol": "nope"})
    assert rejected.status_code == 400


async def test_one_ui_derives_each_request_forms_hop_from_the_protocol(
    offline_config: SampleConfig,
    tmp_path: Path,
) -> None:
    """A2A-T extension and standard A2A share one agent but keep their own discovery/transport."""
    calls: list[tuple[str, str, str]] = []

    async def build(
        config: SampleConfig,
        scenario: str,
        mode: str,
        transport: str,
        discovery: str,
        *,
        protocol: str = PROTOCOL_A2A_T,
        run_id: str = "",
    ) -> FakeRuntime:
        del config, scenario, mode, run_id
        calls.append((protocol, transport, discovery))
        return FakeRuntime()

    app = create_app(
        offline_config,
        runs_dir=tmp_path,
        runtime_factory=build,
        protocol=PROTOCOL_A2A_T,
        protocols=(PROTOCOL_A2A_T, "pure"),
        nms_enabled=False,
        mie_url="http://127.0.0.1:8002",
        finished_grace_seconds=0.0,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        meta = (await http_client.get("/api/meta")).json()
        assert meta["protocols"] == ["a2a-t", "pure"]
        assert meta["defaultProtocol"] == "a2a-t"
        assert meta["scenariosByProtocol"]["pure"] == ["night", "information", "target"]
        assert meta["protocolDefaults"]["a2a-t"] == {
            "discovery": "registry",
            "transport": "sse",
        }
        assert meta["protocolDefaults"]["pure"] == {
            "discovery": "direct",
            "transport": "sse",
        }
        assert meta["mieEnabled"] is True


        extension = await http_client.post("/api/runs", json={"scenario": "night", "mode": "auto", "protocol": "a2a-t"})
        assert extension.status_code == 202
        await _wait_status(http_client, extension.json()["runId"], lambda data: data["status"] == "finished")

        standard = await http_client.post(
            "/api/runs", json={"scenario": "information", "mode": "auto", "protocol": "pure"}
        )
        assert standard.status_code == 202
        await _wait_status(http_client, standard.json()["runId"], lambda data: data["status"] == "finished")

    assert calls == [
        ("a2a-t", "sse", "registry"),
        ("pure", "sse", "direct"),
    ]


async def test_nms_simulator_keeps_the_sse_transport_in_the_meta_defaults(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """`--nms-sim` serves SSE only; the page must not default A2A-T to webhook."""
    app = create_app(
        replace(offline_config, discovery=DISCOVERY_NMS, transport="sse"),
        runs_dir=tmp_path,
        runtime_factory=lambda *args, **kwargs: FakeRuntime(),  # type: ignore[arg-type,return-value]
        protocol=PROTOCOL_A2A_T,
        protocols=(PROTOCOL_A2A_T,),
        nms_enabled=True,
        default_transport="sse",
        finished_grace_seconds=0.0,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        meta = (await http_client.get("/api/meta")).json()

    assert meta["protocolDefaults"]["a2a-t"] == {
        "discovery": "nms",
        "transport": "sse",
    }


async def test_wire_endpoint_returns_the_captured_exchange(client: httpx.AsyncClient, tmp_path: Path) -> None:
    """The inspector renders the request from the wire log, never from a reconstructed one."""
    run_dir = tmp_path / "run-wire"
    run_dir.mkdir()
    record = {
        "dir": "out",
        "method": "POST",
        "url": "https://nms.test:31127/a2a/json/message:send",
        "headers": {"Accept": "application/json", "Authorization": "***"},
        "body": {"message": {"parts": [{"text": "create ran energy saving task"}]}},
    }
    (run_dir / "wire.jsonl").write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    response = await client.get("/api/runs/run-wire/wire")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["records"] == [record]


async def test_wire_endpoint_reports_no_capture_instead_of_inventing_one(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/runs/unknown-run/wire")

    assert response.status_code == 200
    assert response.json() == {"runId": "unknown-run", "records": [], "enabled": False}


async def test_trace_ingestion_warns_for_an_unknown_run(
    client: httpx.AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stale OSS Agent posts to a run this UI instance does not own; ui.log must say so."""
    with caplog.at_level(logging.WARNING, logger="ran_energy_saving.web"):
        response = await client.post(
            "/api/runs/missing/trace",
            json=[],
            headers={"x-a2at-trace-token": TRACE_TOKEN},
        )

    assert response.status_code == 404
    assert "unknown run missing" in caplog.text


class _FakeCancelNmsClient:
    """NMS double for the Running-tasks picker; no network."""

    tasks: list[dict[str, object]] = []
    cancel_result: bool = True
    cancel_error: Exception | None = None
    cancelled: list[str] = []

    def __init__(self, config: object) -> None:
        del config

    async def list_tasks(self) -> dict[str, object]:
        return {"tasks": list(type(self).tasks)}

    async def cancel_task(self, task_id: str) -> bool:
        if type(self).cancel_error is not None:
            raise type(self).cancel_error  # type: ignore[misc]
        type(self).cancelled.append(task_id)
        return type(self).cancel_result

    async def aclose(self) -> None:
        return None


def _nms_config(config: SampleConfig) -> SampleConfig:
    return replace(
        config,
        discovery=DISCOVERY_NMS,
        nms_a2a_base="http://nms.example/a2a/json",
        nms_token_url="http://nms.example/sessions",
        nms_token_user="user",
        nms_token_value="secret",
    )


def _nms_app(
    config: SampleConfig,
    tmp_path: Path,
    factory: Callable[..., Any],
) -> tuple[Any, Any]:
    app = create_app(
        _nms_config(config),
        runs_dir=tmp_path,
        runtime_factory=factory,
        finished_grace_seconds=0.0,
        ui_mode="explorer",
    )
    return app, app.state.run_manager


@pytest.fixture(autouse=True)
def _fake_cancel_client(monkeypatch: pytest.MonkeyPatch) -> type[_FakeCancelNmsClient]:
    from demo.web import nms_tasks as nms_tasks_module

    monkeypatch.setattr(nms_tasks_module, "NmsClient", _FakeCancelNmsClient)
    _FakeCancelNmsClient.tasks = []
    _FakeCancelNmsClient.cancel_result = True
    _FakeCancelNmsClient.cancel_error = None
    _FakeCancelNmsClient.cancelled = []
    return _FakeCancelNmsClient


async def test_nms_task_list_active_state_includes_input_required(
    offline_config: SampleConfig,
    factory: Callable[..., Any],
    tmp_path: Path,
    _fake_cancel_client: type[_FakeCancelNmsClient],
) -> None:
    _fake_cancel_client.tasks = [
        {"id": "task-working", "contextId": "c1", "status": {"state": "TASK_STATE_WORKING"}},
        {"id": "task-waiting", "contextId": "c2", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}},
        {"id": "task-done", "contextId": "c3", "status": {"state": "TASK_STATE_COMPLETED"}},
    ]
    app, _ = _nms_app(offline_config, tmp_path, factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        active = await client.get("/api/nms/tasks?state=active")
        modifiable = await client.get("/api/nms/tasks?state=modifiable")

    assert [row["taskId"] for row in active.json()["tasks"]] == ["task-working", "task-waiting"]
    assert [row["taskId"] for row in modifiable.json()["tasks"]] == ["task-working"]


async def test_cancel_nms_task_endpoint_stops_the_matching_local_run(
    offline_config: SampleConfig,
    factory: Callable[..., Any],
    tmp_path: Path,
    _fake_cancel_client: type[_FakeCancelNmsClient],
) -> None:
    app, manager = _nms_app(offline_config, tmp_path, factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        start = await client.post(
            "/api/runs",
            json={"scenario": "information", "mode": "manual", "transport": "sse"},
        )
        run_id = start.json()["runId"]
        await _wait_status(client, run_id, lambda data: data["pendingDecision"] is True)
        run = manager.get(run_id)
        assert run is not None
        run.remote_task_id = "task-9"

        response = await client.post("/api/nms/tasks/task-9/cancel")
        finished = await _wait_status(client, run_id, lambda data: data["status"] != "running")

    assert response.status_code == 200
    assert response.json() == {
        "taskId": "task-9",
        "cancelled": True,
        "supported": True,
        "stoppedRuns": [run_id],
    }
    assert _fake_cancel_client.cancelled == ["task-9"]
    assert finished["status"] == "interrupted"


async def test_cancel_nms_task_endpoint_reports_the_known_503(
    offline_config: SampleConfig,
    factory: Callable[..., Any],
    tmp_path: Path,
    _fake_cancel_client: type[_FakeCancelNmsClient],
) -> None:
    _fake_cancel_client.cancel_result = False
    app, _ = _nms_app(offline_config, tmp_path, factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/nms/tasks/task-9/cancel")

    assert response.status_code == 200
    assert response.json() == {
        "taskId": "task-9",
        "cancelled": False,
        "supported": False,
        "reason": "nms_cancel_unsupported",
        "stoppedRuns": [],
    }


async def test_cancel_nms_task_endpoint_reports_a_missing_task(
    offline_config: SampleConfig,
    factory: Callable[..., Any],
    tmp_path: Path,
    _fake_cancel_client: type[_FakeCancelNmsClient],
) -> None:
    from protocol.nms_client import NmsProtocolError

    _fake_cancel_client.cancel_error = NmsProtocolError("NMS cancel returned HTTP 404: gone")
    app, _ = _nms_app(offline_config, tmp_path, factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/nms/tasks/task-9/cancel")

    assert response.status_code == 404
    assert response.json()["code"] == "task_missing"


async def test_cancel_nms_task_endpoint_requires_nms_discovery(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/api/nms/tasks/task-9/cancel")

    assert response.status_code == 400
    assert response.json()["code"] == "nms_discovery_required"
