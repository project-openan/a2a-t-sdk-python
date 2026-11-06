"""Tests of the client application service (runs, conversations, persistence, memory)."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from a2a.types import Artifact, Task, TaskState, TaskStatus
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from demo.catalog import DemoRunCatalog
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from protocol.client_flow import ClientFlowResult
from protocol.client_service import RunConflictError, RunManager
from protocol.client_store import (
    ClientRunRecord,
    SqliteClientStore,
    read_client_run_detail,
    read_conversation_detail,
)
from protocol.decision_provider import CLIENT_APP_NAME, CLIENT_USER_ID
from shared.config import SampleConfig
from shared.metrics import render_prometheus, reset_metrics
from shared.models import NegotiationDecision, NegotiationProposeView
from shared.run_constants import (
    FINAL_STATE_INCOMPLETE,
    FINAL_STATE_TASK_COMPLETED,
    PROTOCOL_A2A_T,
    PROTOCOL_PURE,
)


class FakeRuntime:
    """Deterministic runtime double with an optional wait and failure."""

    def __init__(
        self,
        *,
        wait: asyncio.Event | None = None,
        error: Exception | None = None,
    ) -> None:
        self.manual_provider = None
        self.discovered = SimpleNamespace(url="http://server", agent_card=SimpleNamespace(name="ran"))
        self.wait = wait
        self.error = error
        self.conversation_context: str | None = None
        self.user_request: str | None = None
        self.closed = False
        self.entered = asyncio.Event()

    async def run(
        self,
        *,
        event_sink: Callable[[dict[str, object]], None] | None = None,
        on_decision_requested: Callable[[NegotiationProposeView, NegotiationContext], None] | None = None,
        on_decision_made: Callable[[NegotiationDecision, NegotiationProposeView, NegotiationContext], None]
        | None = None,
        on_intent_ready: Callable[[str], Awaitable[bool]] | None = None,
        trace_sink: Callable[[dict[str, object]], None] | None = None,
        run_id: str | None = None,
        user_request: str | None = None,
        conversation_context: str | None = None,
        validate_before_send: bool = False,
        run_mode: str = "create",
        target_task_id: str | None = None,
        target_context_id: str | None = None,
    ) -> ClientFlowResult:
        del on_intent_ready, trace_sink, run_id, validate_before_send
        del run_mode, target_task_id, target_context_id
        self.conversation_context = conversation_context
        self.user_request = user_request
        self.entered.set()
        if self.wait is not None:
            await self.wait.wait()
        if self.error is not None:
            raise self.error
        if event_sink is not None:
            event_sink(
                {
                    "kind": "status",
                    "task_id": "task-1",
                    "context_id": "ctx-1",
                    "state": "TASK_STATE_WORKING",
                    "text": "working",
                }
            )
        propose = NegotiationProposeView(
            negotiation_id="neg-1",
            kind="information",
            round=1,
            max_rounds=3,
            content="missing region",
            template_uri="Negotiation-T/information-negotiation/propose/v1",
            context_id="ctx-1",
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
            context_id="ctx-1",
            task_id="task-1",
            report={"summary": "The plan is applied."},
        )

    async def aclose(self) -> None:
        self.closed = True


def _manager(
    tmp_path: Path,
    config: SampleConfig,
    *,
    wait: asyncio.Event | None = None,
    error: Exception | None = None,
    memory_turns: int = 6,
) -> tuple[RunManager, SqliteClientStore, list[FakeRuntime]]:
    store = SqliteClientStore(tmp_path / "client.sqlite3")
    runtimes: list[FakeRuntime] = []

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
        runtime = FakeRuntime(wait=wait, error=error)
        runtimes.append(runtime)
        return runtime

    manager = RunManager(
        config=config,
        runtime_factory=factory,
        catalog=DemoRunCatalog(),
        runs_dir=tmp_path / "runs",
        store=store,
        memory_turns=memory_turns,
    )
    return manager, store, runtimes


async def _finish(manager: RunManager, run_id: str) -> None:
    run = manager.get(run_id)
    assert run is not None and run.task is not None
    await run.task


async def test_run_persists_lifecycle_negotiation_and_task_snapshot(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    manager, store, runtimes = _manager(tmp_path, offline_config)
    run = await manager.start("information", "auto", "sse", user_request="please save energy")
    await _finish(manager, run.run_id)

    assert run.status == "finished"
    # The finished run keeps its record, not its runtime: the provider, its ADK runner and the
    # decision sessions are released so a long-lived process does not accumulate them.
    assert run.runtime is None
    assert runtimes[0].closed is True
    stored = await store.get_run(run.run_id)
    assert stored is not None
    assert stored.status == "finished"
    assert stored.final_state == "task_completed"
    assert stored.server_task_id == "task-1"
    assert stored.server_context_id == "ctx-1"
    assert stored.transcript_json is not None

    turns = await store.list_turns(stored.conversation_id or "")
    assert [turn.role for turn in turns] == ["user", "assistant"]
    assert turns[1].content == "The plan is applied."

    events = await store.list_negotiation_events(run.run_id)
    assert [event.direction for event in events] == ["inbound", "outbound"]
    assert events[0].performative == "PROPOSE"
    assert events[1].performative == "ACCEPT"
    assert events[1].decision_action == "accept"

    snapshot = await store.get_task_snapshot(run.run_id)
    assert snapshot is not None
    assert snapshot.task_id == "task-1"
    assert snapshot.state == "TASK_STATE_WORKING"


async def test_run_applies_the_decision_session_retention_window(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    db_path = tmp_path / "client.sqlite3"
    service = SqliteSessionService(str(db_path))
    await service.create_session(app_name=CLIENT_APP_NAME, user_id=CLIENT_USER_ID, session_id="ran-client-stale")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE sessions SET update_time = ? WHERE id = ?",
            (time.time() - 8 * 24 * 60 * 60, "ran-client-stale"),
        )
        connection.commit()

    manager, _, _ = _manager(
        tmp_path,
        replace(
            offline_config,
            client_db_path=str(db_path),
            adk_session_retention_days=7,
        ),
    )
    run = await manager.start("information", "auto", "sse")
    await _finish(manager, run.run_id)

    remaining = await service.list_sessions(app_name=CLIENT_APP_NAME)
    assert remaining.sessions == []


async def test_conversation_memory_is_injected_into_the_next_run(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    manager, store, runtimes = _manager(tmp_path, offline_config, memory_turns=4)
    first = await manager.start("information", "auto", "sse", user_request="first request", conversation_id="conv-1")
    await _finish(manager, first.run_id)

    second = await manager.start("information", "auto", "sse", user_request="second request", conversation_id="conv-1")
    await _finish(manager, second.run_id)

    assert runtimes[1].conversation_context is not None
    assert "user: first request" in runtimes[1].conversation_context
    assert "assistant: The plan is applied." in runtimes[1].conversation_context
    assert "second request" not in runtimes[1].conversation_context

    conversation = read_conversation_detail(store.path, "conv-1")
    assert conversation is not None
    turns = conversation["turns"]
    runs = conversation["runs"]
    assert isinstance(turns, list) and len(turns) == 4
    assert isinstance(runs, list) and len(runs) == 2


async def test_idempotency_key_reuses_the_existing_run(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    manager, store, runtimes = _manager(tmp_path, offline_config)
    first = await manager.start(
        "information",
        "auto",
        "sse",
        user_request="request",
        conversation_id="conv-1",
        idempotency_key="key-1",
    )
    await _finish(manager, first.run_id)
    turn_count = len(await store.list_turns("conv-1"))

    reused = await manager.start(
        "information",
        "auto",
        "sse",
        user_request="request",
        conversation_id="conv-1",
        idempotency_key="key-1",
    )
    assert reused.run_id == first.run_id
    assert reused.task is None
    assert len(runtimes) == 1
    assert len(await store.list_turns("conv-1")) == turn_count


async def test_one_active_run_per_conversation_but_conversations_are_independent(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    wait = asyncio.Event()
    manager, _, _ = _manager(tmp_path, offline_config, wait=wait)
    first = await manager.start("information", "auto", "sse", user_request="first", conversation_id="conv-1")
    while True:
        first_run = manager.get(first.run_id)
        if first_run is not None and first_run.runtime is not None:
            break
        await asyncio.sleep(0.01)

    with pytest.raises(RunConflictError):
        await manager.start("information", "auto", "sse", user_request="second", conversation_id="conv-1")

    other = await manager.start("information", "auto", "sse", user_request="other", conversation_id="conv-2")
    assert other.run_id != first.run_id

    wait.set()
    await _finish(manager, first.run_id)
    await _finish(manager, other.run_id)


async def test_failed_run_is_persisted(tmp_path: Path, offline_config: SampleConfig) -> None:
    manager, store, _ = _manager(tmp_path, offline_config, error=RuntimeError("boom"))
    run = await manager.start("information", "auto", "sse", user_request="fail")
    await _finish(manager, run.run_id)

    assert run.status == "failed"
    stored = await store.get_run(run.run_id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error is not None and "boom" in stored.error
    turns = await store.list_turns(stored.conversation_id or "")
    assert turns[-1].role == "assistant"
    assert "Run failed" in turns[-1].content


async def test_shutdown_marks_running_run_interrupted(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    wait = asyncio.Event()
    manager, store, _ = _manager(tmp_path, offline_config, wait=wait)
    run = await manager.start("information", "auto", "sse", user_request="slow")
    while run.runtime is None:
        await asyncio.sleep(0.01)

    await manager.shutdown()
    await asyncio.sleep(0.05)

    stored = await store.get_run(run.run_id)
    assert stored is not None
    assert stored.status == "interrupted"


async def test_processed_run_records_the_built_in_raw_request(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    from demo.intent import build_scenario_intent

    manager, _, _ = _manager(tmp_path, replace(offline_config, intent_mode="processed"))
    run = await manager.start("information", "auto", "sse")
    await _finish(manager, run.run_id)

    expected = build_scenario_intent("information", offline_config.language)
    assert run.intent_mode == "processed"
    assert run.raw_request == expected.raw_request


async def test_cancel_asks_the_remote_side_and_stops_the_run(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    wait = asyncio.Event()
    manager, store, runtimes = _manager(tmp_path, offline_config, wait=wait)
    run = await manager.start("information", "auto", "sse", user_request="slow")
    while not runtimes or not runtimes[0].entered.is_set():
        await asyncio.sleep(0.01)

    canceled: list[str] = []

    async def cancel_remote(task_id: str) -> None:
        canceled.append(task_id)

    runtime = run.runtime
    assert runtime is not None
    runtime.transport = SimpleNamespace(cancel=cancel_remote)  # type: ignore[attr-defined]
    run.remote_task_id = "task-1"

    assert await manager.cancel(run.run_id) is True

    assert canceled == ["task-1"]
    assert run.status == "interrupted"
    assert run.error == "The run was cancelled by the operator."
    assert run.runtime is None
    stored = await store.get_run(run.run_id)
    assert stored is not None
    assert stored.status == "interrupted"
    assert stored.error == "The run was cancelled by the operator."
    assert await manager.cancel(run.run_id) is False


async def test_a_finished_run_records_metrics(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    reset_metrics()
    manager, _, _ = _manager(tmp_path, offline_config)
    run = await manager.start("information", "auto", "sse", user_request="measure")
    await _finish(manager, run.run_id)

    text = render_prometheus()
    assert 'a2at_client_runs_total{status="finished",final_state="task_completed"} 1' in text
    assert "a2at_client_runs_in_flight 0" in text
    assert "a2at_client_run_duration_seconds_count 1" in text
    assert 'a2at_negotiation_rounds_total{role="server",round="1"} 1' in text
    assert 'a2at_negotiation_rounds_total{role="client",round="1"} 1' in text


async def test_second_manager_cannot_start_an_active_conversation(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """The database claim blocks a second process even when its local map is empty."""
    wait = asyncio.Event()
    first, store, _ = _manager(tmp_path, offline_config, wait=wait)
    second, _, _ = _manager(tmp_path, offline_config)
    run = await first.start("information", "auto", "sse", conversation_id="conv-shared")
    while run.runtime is None:
        await asyncio.sleep(0.01)

    with pytest.raises(RunConflictError, match="already has an active run"):
        await second.start("information", "auto", "sse", conversation_id="conv-shared")

    rows = await store.list_runs(limit=10, conversation_id="conv-shared")
    assert [row.run_id for row in rows] == [run.run_id]

    wait.set()
    await _finish(first, run.run_id)


async def test_a_dead_owner_run_is_reclaimed(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """A row left by a dead process does not block the next start."""
    manager, store, _ = _manager(tmp_path, offline_config)
    await store.ensure_conversation(conversation_id="conv-stale")
    await store.create_run(
        ClientRunRecord(
            run_id="dead-run",
            conversation_id="conv-stale",
            scenario="information",
            mode="auto",
            transport="sse",
            discovery="registry",
            user_request="previous",
            owner_pid=2**31 - 1,
        )
    )

    run = await manager.start("information", "auto", "sse", conversation_id="conv-stale")
    await _finish(manager, run.run_id)

    stale = await store.get_run("dead-run")
    assert stale is not None and stale.status == "interrupted"


async def test_mark_interrupted_sweeps_previous_runs(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    manager, store, _ = _manager(tmp_path, offline_config)
    await store.ensure_conversation(conversation_id="conv-1")
    await store.create_run(
        ClientRunRecord(
            run_id="previous",
            conversation_id="conv-1",
            scenario="information",
            mode="auto",
            transport="sse",
            discovery="registry",
            user_request="previous",
        )
    )
    assert await manager.mark_interrupted() == 1
    stored = await store.get_run("previous")
    assert stored is not None and stored.status == "interrupted"


async def test_read_detail_exposes_persisted_paths(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    manager, store, _ = _manager(tmp_path, offline_config)
    run = await manager.start("information", "auto", "sse", user_request="detail")
    await _finish(manager, run.run_id)

    detail = read_client_run_detail(store.path, run.run_id)
    assert detail is not None
    assert detail["status"] == "finished"
    assert detail["transcriptJson"] is not None
    assert Path(str(detail["transcriptJson"])).is_file()


async def test_run_publishes_langfuse_trace_state(
    tmp_path: Path,
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run frames carry the Langfuse trace id at start and the URL at finish."""

    class _Exporter:
        def __call__(self, span: dict[str, object]) -> None:
            del span

        def finish(self, *, result: object | None = None, error: str | None = None) -> None:
            del result, error

        def snapshot(self, *, resolve_url: bool = False) -> dict[str, object]:
            return {
                "enabled": True,
                "traceId": "trace-1",
                "url": "https://langfuse.example/trace-1" if resolve_url else None,
                "status": "exported" if resolve_url else "exporting",
                "warning": None,
            }

    monkeypatch.setattr(
        "protocol.client_service.run_manager.create_langfuse_exporter",
        lambda config, metadata, **_: _Exporter(),
    )
    manager, _store, _runtimes = _manager(tmp_path, offline_config)

    run = await manager.start("information", "auto", "sse")
    started = next(message for message in run.messages if message.get("type") == "run_started")
    assert started["langfuse"]["traceId"] == "trace-1"

    await _finish(manager, run.run_id)

    finished = next(message for message in run.messages if message.get("type") == "finished")
    assert finished["langfuse"]["url"] == "https://langfuse.example/trace-1"
    assert run.langfuse["status"] == "exported"
    assert run.snapshot()["langfuse"]["traceId"] == "trace-1"


class _PollingA2AClient:
    """Return a working Task first, then a completed one with a report."""

    def __init__(self) -> None:
        self.calls = 0

    async def get_task(self, request: object, *, context: object = None) -> object:
        del context
        task_id = str(getattr(request, "id", "task-1"))
        self.calls += 1
        task = _task_with_state(task_id, TaskState.TASK_STATE_WORKING)
        if self.calls >= 2:
            task = _task_with_state(task_id, TaskState.TASK_STATE_COMPLETED)
            artifact = Artifact(artifact_id="artifact-1", name="energy saving report")
            artifact.parts.add(text="The plan is applied.")
            task.artifacts.append(artifact)
        return task


def _task_with_state(task_id: str, state: TaskState) -> Task:
    """Build one remote Task with a status."""
    return Task(id=task_id, context_id="ctx-1", status=TaskStatus(state=state))


class _LongRunningRuntime(FakeRuntime):
    """Runtime double that leaves a remote Task executing after the client flow returns."""

    def __init__(self) -> None:
        super().__init__()
        self.a2a_client = _PollingA2AClient()
        self.nms_client = None

    async def run(self, **kwargs: object) -> ClientFlowResult:
        del kwargs
        self.entered.set()
        return ClientFlowResult(
            final_state=FINAL_STATE_INCOMPLETE,
            task_id="task-1",
            context_id="ctx-1",
        )


async def test_a_long_running_run_stays_active_until_the_remote_task_finishes(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """A remote Task that outlives the request stream must not end the engineer run."""
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
    ) -> _LongRunningRuntime:
        del resolved_config, scenario, mode, transport, discovery, protocol, run_id
        return _LongRunningRuntime()

    manager = RunManager(
        config=replace(offline_config, task_poll_interval_seconds=0.01),
        runtime_factory=factory,
        catalog=DemoRunCatalog(),
        runs_dir=tmp_path / "runs",
        store=store,
    )
    run = await manager.start(
        "information",
        "auto",
        "sse",
        protocol=PROTOCOL_PURE,
    )
    await _finish(manager, run.run_id)

    assert run.status == "finished"
    assert run.result is not None
    assert run.result.final_state == FINAL_STATE_TASK_COMPLETED
    assert run.result.report is not None
    assert run.remote_task_id == "task-1"
    stored = await store.get_run(run.run_id)
    assert stored is not None
    assert stored.status == "finished"
    assert stored.final_state == FINAL_STATE_TASK_COMPLETED
    assert stored.server_task_id == "task-1"
