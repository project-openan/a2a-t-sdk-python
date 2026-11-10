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
from a2a.types import Artifact, Message, Role, Task, TaskState, TaskStatus
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from demo.catalog import DemoRunCatalog
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from protocol.client_flow import ClientFlowResult
from protocol.client_service import RunConflictError, RunManager
from protocol.client_service.run_manager import HANDED_OVER
from protocol.client_store import (
    ClientRunRecord,
    SqliteClientStore,
    read_client_run_detail,
    read_conversation_detail,
)
from protocol.decision_provider import CLIENT_APP_NAME, CLIENT_USER_ID, DecisionTimeoutError
from shared.config import SampleConfig
from shared.metrics import render_prometheus, reset_metrics
from shared.models import NegotiationDecision, NegotiationProposeView
from shared.run_constants import (
    FINAL_STATE_INCOMPLETE,
    FINAL_STATE_TASK_COMPLETED,
    FINAL_STATE_TASK_FAILED,
    PROTOCOL_A2A_T,
    PROTOCOL_PURE,
)
from shared.trace_propagation import current_downstream_trace_id, current_trace_id


class FakeRuntime:
    """Deterministic runtime double with an optional wait and failure."""

    def __init__(
        self,
        *,
        wait: asyncio.Event | None = None,
        error: Exception | None = None,
        hold_decision: asyncio.Event | None = None,
        expire_windows: int = 0,
        skip_decision: bool = False,
    ) -> None:
        self.manual_provider = None
        self.discovered = SimpleNamespace(url="http://server", agent_card=SimpleNamespace(name="ran"))
        self.wait = wait
        self.error = error
        self.hold_decision = hold_decision
        self.expire_windows = expire_windows
        self.skip_decision = skip_decision
        self.wait_sink: Callable[[DecisionTimeoutError], None] | None = None
        self.conversation_context: str | None = None
        self.user_request: str | None = None
        self.closed = False
        self.entered = asyncio.Event()
        self.decision_entered = asyncio.Event()
        self.trace_id = ""
        self.downstream_trace_id = ""

    def set_decision_wait_sink(self, sink: Callable[[DecisionTimeoutError], None] | None) -> bool:
        """Accept the manager's expired-window observer."""
        self.wait_sink = sink
        return True

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
        # Observed from inside the run task: a binding placed outside ``create_task`` is gone here.
        self.trace_id = current_trace_id()
        self.downstream_trace_id = current_downstream_trace_id()
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
        self.decision_entered.set()
        for window in range(self.expire_windows):
            if self.wait_sink is not None:
                self.wait_sink(
                    DecisionTimeoutError(
                        round_number=session.round,
                        window_seconds=0.01,
                        expired_windows=window + 1,
                    )
                )
        if self.hold_decision is not None:
            await self.hold_decision.wait()
        decision = NegotiationDecision(action="accept", rationale="accepted")
        if not self.skip_decision and on_decision_made is not None:
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
    hold_decision: asyncio.Event | None = None,
    expire_windows: int = 0,
    skip_decision: bool = False,
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
        runtime = FakeRuntime(
            wait=wait,
            error=error,
            hold_decision=hold_decision,
            expire_windows=expire_windows,
            skip_decision=skip_decision,
        )
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
    assert {event.protocol for event in events} == {"a2a-t"}

    snapshot = await store.get_task_snapshot(run.run_id)
    assert snapshot is not None
    assert snapshot.task_id == "task-1"
    assert snapshot.state == "TASK_STATE_WORKING"


async def test_a_pure_run_tags_its_negotiation_events(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """A cross-run report can separate the two protocols without joining ``runs``."""
    manager, store, _runtimes = _manager(tmp_path, offline_config)
    run = await manager.start(
        "information",
        "auto",
        "sse",
        user_request="please save energy",
        protocol=PROTOCOL_PURE,
    )
    await _finish(manager, run.run_id)

    events = await store.list_negotiation_events(run.run_id)

    assert [event.direction for event in events] == ["inbound", "outbound"]
    assert {event.protocol for event in events} == {"pure"}


async def test_a_waiting_decision_keeps_the_run_running(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    hold = asyncio.Event()
    manager, _store, runtimes = _manager(
        tmp_path,
        offline_config,
        hold_decision=hold,
        expire_windows=1,
    )
    run = await manager.start("information", "manual", "sse", user_request="please save energy")
    try:
        for _ in range(100):
            if runtimes:
                break
            await asyncio.sleep(0.01)
        await asyncio.wait_for(runtimes[0].decision_entered.wait(), timeout=1)
        await asyncio.sleep(0)

        assert run.status == "running"
        assert run.pending_decision is True
        assert run.activity is not None
        assert run.activity["kind"] == "decision_wait"
        assert run.activity["detail"]["expiredWindows"] == 1
        assert any(event.get("kind") == "decision_wait_expired" for event in run.transcript.events)
    finally:
        hold.set()
    await _finish(manager, run.run_id)

    assert run.status == "finished"
    assert run.pending_decision is False
    assert run.activity is None


async def test_a_pure_waiting_decision_keeps_the_run_running(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """The pure hop has the same observation-window rule as A2A-T: expiry never ends the run."""
    hold = asyncio.Event()
    manager, _store, runtimes = _manager(
        tmp_path,
        offline_config,
        hold_decision=hold,
        expire_windows=1,
    )
    run = await manager.start(
        "information",
        "manual",
        "sse",
        user_request="please save energy",
        protocol=PROTOCOL_PURE,
    )
    try:
        for _ in range(100):
            if runtimes:
                break
            await asyncio.sleep(0.01)
        await asyncio.wait_for(runtimes[0].decision_entered.wait(), timeout=1)
        await asyncio.sleep(0)

        assert run.status == "running"
        assert run.pending_decision is True
        # The pure confirmation answer now accepts the same expired-window observer.
        assert runtimes[0].wait_sink is not None
        assert any(event.get("kind") == "decision_wait_expired" for event in run.transcript.events)
    finally:
        hold.set()
    await _finish(manager, run.run_id)

    assert run.status == "finished"


async def test_confirm_opens_the_decision_surface_before_the_recommendation(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    hold = asyncio.Event()
    manager, _store, runtimes = _manager(tmp_path, offline_config, hold_decision=hold)
    run = await manager.start("information", "confirm", "sse", user_request="please save energy")
    try:
        for _ in range(100):
            if runtimes:
                break
            await asyncio.sleep(0.01)
        await asyncio.wait_for(runtimes[0].decision_entered.wait(), timeout=1)
        await asyncio.sleep(0)

        assert run.pending_decision is True
        pending = [frame for frame in run.messages if frame.get("type") == "decision_pending"]
        assert len(pending) == 1
        assert pending[0]["round"] == 1
        assert isinstance(pending[0]["missingItems"], list)
        assert "recommendationTimeoutSeconds" in pending[0]
        assert not any(frame.get("type") == "decision_requested" for frame in run.messages)
    finally:
        hold.set()
    await _finish(manager, run.run_id)

    assert run.status == "finished"


async def test_a_flow_that_returns_while_waiting_fails_instead_of_finishing(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    manager, _store, _runtimes = _manager(tmp_path, offline_config, skip_decision=True)
    run = await manager.start("information", "manual", "sse", user_request="please save energy")
    await _finish(manager, run.run_id)

    assert run.status == "failed"
    assert "still pending" in (run.error or "")


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
        trace_id="0123456789abcdef0123456789abcdef",
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
    assert reused.trace_id == "0123456789abcdef0123456789abcdef"
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


async def test_processed_run_records_the_example_request(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    from demo.catalog import DemoRunCatalog

    manager, _, _ = _manager(tmp_path, replace(offline_config, intent_mode="processed"))
    run = await manager.start("information", "auto", "sse")
    await _finish(manager, run.run_id)

    expected = DemoRunCatalog().spec("information", language=offline_config.language).user_request
    assert run.intent_mode == "processed"
    assert run.raw_request == expected


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


async def test_a_modify_run_stops_the_previous_run_of_the_same_task(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """A Modify continues the same Task, so the run that was tracking it must stop.

    The remote Task is untouched: the new run owns it now. Stopping first releases the previous
    run's callback subscription, so pushes arriving before the new run binds stay buffered for it.
    """
    hold = asyncio.Event()
    manager, _, runtimes = _manager(tmp_path, offline_config, hold_decision=hold)
    first = await manager.start("information", "auto", "sse", user_request="create")
    while first.runtime is None:
        await asyncio.sleep(0.01)
    await runtimes[0].decision_entered.wait()
    first.remote_task_id = "task-1"

    second = await manager.start(
        "information",
        "auto",
        "sse",
        user_request="modify",
        target_task_id="task-1",
        target_context_id="ctx-1",
    )

    assert first.finished()
    assert first.status == "interrupted"
    assert first.error == HANDED_OVER
    assert second.finished() is False
    hold.set()
    await _finish(manager, second.run_id)


async def test_a_pure_modify_stops_the_run_tracking_the_outer_task(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """The A2A peer's Task is the continuation target; its previous run must hand it over."""
    hold = asyncio.Event()
    manager, _, runtimes = _manager(tmp_path, offline_config, hold_decision=hold)
    first = await manager.start(
        "information", "auto", "sse", user_request="create", protocol="pure"
    )
    while first.runtime is None:
        await asyncio.sleep(0.01)
    await runtimes[0].decision_entered.wait()
    # A pure run observes the OSS Agent's outer Task, not the vendor Task in the picker.
    first.remote_task_id = "outer-1"

    second = await manager.start(
        "information",
        "auto",
        "sse",
        user_request="modify",
        protocol="pure",
        target_task_id="task-1",
        target_context_id="ctx-1",
        target_server_task_id="outer-1",
        target_server_context_id="outer-ctx",
    )

    assert first.finished()
    assert first.error == HANDED_OVER
    hold.set()
    await _finish(manager, second.run_id)


async def test_a_modify_waits_until_the_target_task_has_reached_submitted(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """A Task that no run has observed yet has no server-side identity to continue.

    Handing over at that moment would race the create that is still in flight; the operator has to
    wait until the NMS accepts the Task (SUBMITTED) before modifying it.
    """
    hold = asyncio.Event()
    manager, _, runtimes = _manager(tmp_path, offline_config, hold_decision=hold)
    first = await manager.start("information", "auto", "sse", user_request="create")
    while first.runtime is None:
        await asyncio.sleep(0.01)
    await runtimes[0].decision_entered.wait()
    first.target_task_id = "task-1"
    first.remote_task_id = None
    first.latest_task_state = None

    with pytest.raises(RunConflictError, match="SUBMITTED"):
        await manager.start(
            "information",
            "auto",
            "sse",
            user_request="modify",
            target_task_id="task-1",
            target_context_id="ctx-1",
        )

    assert first.finished() is False
    hold.set()
    await _finish(manager, first.run_id)


async def test_a_modify_run_leaves_an_unrelated_run_alone(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """Only the run that owns the continued Task stops; an unrelated one keeps running."""
    hold = asyncio.Event()
    manager, _, runtimes = _manager(tmp_path, offline_config, hold_decision=hold)
    other = await manager.start("information", "auto", "sse", user_request="other")
    while other.runtime is None:
        await asyncio.sleep(0.01)
    await runtimes[0].decision_entered.wait()
    other.remote_task_id = "other-task"

    modify = await manager.start(
        "information",
        "auto",
        "sse",
        user_request="modify",
        target_task_id="task-1",
        target_context_id="ctx-1",
    )

    assert other.finished() is False
    hold.set()
    await other.task  # type: ignore[union-attr]
    await modify.task  # type: ignore[union-attr]


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


async def test_start_keeps_the_chain_but_not_the_conversation(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """A retry shares the correction chain while getting its own conversation and run."""
    manager, store, _ = _manager(tmp_path, offline_config)
    run = await manager.start(
        "information",
        "auto",
        "sse",
        conversation_id="conv-retry",
        chain_id="chain-1",
        parent_run_id="blocked-run",
    )
    assert run.chain_id == "chain-1"
    assert run.parent_run_id == "blocked-run"
    snapshot = run.snapshot()
    assert snapshot["chainId"] == "chain-1"
    assert snapshot["parentRunId"] == "blocked-run"

    await _finish(manager, run.run_id)

    stored = await store.get_run(run.run_id)
    assert stored is not None
    assert stored.chain_id == "chain-1"
    assert stored.parent_run_id == "blocked-run"
    assert stored.conversation_id == "conv-retry"


async def test_a_presentation_attempt_owns_the_chain_trace(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """The blocked preflight and its corrected retry resolve to the same trace id."""
    manager, store, _ = _manager(tmp_path, offline_config)
    first = await manager.start(
        "information",
        "auto",
        "sse",
        source={"kind": "presentation"},
    )
    workflow = await store.get_workflow_root(first.chain_id)
    assert workflow is not None
    assert workflow.trace_id == first.trace_id

    retry = await manager.start(
        "information",
        "auto",
        "sse",
        source={"kind": "presentation"},
        chain_id=first.chain_id,
        parent_run_id=first.run_id,
        trace_id=first.trace_id,
    )
    assert retry.trace_id == first.trace_id
    assert retry.run_id != first.run_id
    assert retry.conversation_id != first.conversation_id
    assert retry.workflow_root_span_id == ""

    await _finish(manager, first.run_id)
    await _finish(manager, retry.run_id)


async def test_a_caller_cannot_move_a_chain_to_another_trace(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """A mismatched explicit trace id is a conflict, not a silent second trace."""
    from protocol.client_store import WorkflowTraceConflictError

    manager, _, _ = _manager(tmp_path, offline_config)
    first = await manager.start(
        "information",
        "auto",
        "sse",
        source={"kind": "presentation"},
    )

    with pytest.raises(WorkflowTraceConflictError):
        await manager.start(
            "information",
            "auto",
            "sse",
            source={"kind": "presentation"},
            chain_id=first.chain_id,
            trace_id="a" * 32,
        )
    await _finish(manager, first.run_id)


async def test_start_defaults_the_chain_to_the_conversation(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """A run without a retry belongs to a chain of its own, named by its conversation."""
    manager, store, _ = _manager(tmp_path, offline_config)
    run = await manager.start("information", "auto", "sse", conversation_id="conv-1")

    assert run.chain_id == "conv-1"
    assert run.parent_run_id == ""

    await _finish(manager, run.run_id)

    stored = await store.get_run(run.run_id)
    assert stored is not None and stored.chain_id == "conv-1"


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
    # The vendor-facing trace id is known at start, so the page can show it immediately.
    assert started["traceId"] == run.trace_id

    # The URL is resolved in the background: the page links to the live trace during the run.
    for _ in range(200):
        if any(message.get("type") == "langfuse" for message in run.messages):
            break
        await asyncio.sleep(0.01)
    early = next(message for message in run.messages if message.get("type") == "langfuse")
    assert early["runId"] == run.run_id
    assert early["langfuse"]["url"] == "https://langfuse.example/trace-1"

    await _finish(manager, run.run_id)

    finished = next(message for message in run.messages if message.get("type") == "finished")
    assert finished["langfuse"]["url"] == "https://langfuse.example/trace-1"
    assert run.langfuse["status"] == "exported"
    assert run.snapshot()["langfuse"]["traceId"] == "trace-1"


async def test_the_run_task_binds_the_langfuse_trace(
    tmp_path: Path,
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The binding lives inside the run task, so the run itself can observe it."""

    class _Exporter:
        trace_id = "e" * 32
        root_span_id = "1" * 16

        def __call__(self, span: dict[str, object]) -> None:
            del span

        def finish(self, *, result: object | None = None, error: str | None = None) -> None:
            del result, error

        def snapshot(self, *, resolve_url: bool = False) -> dict[str, object]:
            del resolve_url
            return {
                "enabled": True,
                "traceId": self.trace_id,
                "url": None,
                "status": "exporting",
                "warning": None,
            }

    monkeypatch.setattr(
        "protocol.client_service.run_manager.create_langfuse_exporter",
        lambda config, metadata, **_: _Exporter(),
    )
    manager, _store, runtimes = _manager(tmp_path, offline_config)

    run = await manager.start("information", "auto", "sse", trace_id="f" * 32)
    await _finish(manager, run.run_id)

    # Observed inside the run: the Langfuse trace id comes from the exporter and the vendor-facing
    # trace id is the run's own (the caller-supplied one), not the generated run uuid.
    assert runtimes[0].trace_id == "e" * 32
    assert runtimes[0].downstream_trace_id == "f" * 32


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


class _SwitchableA2AClient:
    """A remote Task that stays WORKING until the test switches it to COMPLETED."""

    def __init__(self) -> None:
        self.state = TaskState.TASK_STATE_WORKING
        self.calls = 0

    async def get_task(self, request: object, *, context: object = None) -> object:
        del context
        self.calls += 1
        return _task_with_state(str(getattr(request, "id", "task-1")), self.state)


class _SwitchableRuntime(_LongRunningRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.a2a_client = _SwitchableA2AClient()


class _RepeatedStateA2AClient:
    """Return one unchanged WORKING state for several polls, then complete."""

    def __init__(self, stable_polls: int) -> None:
        self.stable_polls = stable_polls
        self.calls = 0

    async def get_task(self, request: object, *, context: object = None) -> object:
        del context
        self.calls += 1
        state = TaskState.TASK_STATE_WORKING if self.calls <= self.stable_polls else TaskState.TASK_STATE_COMPLETED
        return _task_with_state(str(getattr(request, "id", "task-1")), state)


class _RepeatedStateRuntime(_LongRunningRuntime):
    def __init__(self, stable_polls: int) -> None:
        super().__init__()
        self.a2a_client = _RepeatedStateA2AClient(stable_polls)


async def test_long_task_publishes_wait_state_on_every_poll_even_when_nothing_changes(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """An unchanged SUBMITTED/WORKING result is still proof of life for the page."""
    runtime = _RepeatedStateRuntime(stable_polls=3)

    async def factory(
        resolved_config: SampleConfig,
        scenario: str,
        mode: str,
        transport: str,
        discovery: str,
        *,
        protocol: str = PROTOCOL_A2A_T,
        run_id: str = "",
    ) -> _RepeatedStateRuntime:
        del resolved_config, scenario, mode, transport, discovery, protocol, run_id
        return runtime

    manager = RunManager(
        config=replace(
            offline_config,
            task_poll_interval_seconds=0.01,
            nms_state_poll_interval=0.01,
        ),
        runtime_factory=factory,
        catalog=DemoRunCatalog(),
        runs_dir=tmp_path / "runs",
    )
    run = await manager.start(
        "information",
        "auto",
        "sse",
        protocol=PROTOCOL_PURE,
        source={"kind": "presentation"},
    )
    _, queue = run.subscribe()

    first: dict[str, object] | None = None
    second: dict[str, object] | None = None
    deadline = asyncio.get_running_loop().time() + 3.0
    while asyncio.get_running_loop().time() < deadline and second is None:
        try:
            frame = queue.get_nowait()
        except asyncio.QueueEmpty:
            await asyncio.sleep(0.01)
            continue
        if frame.get("type") != "activity":
            continue
        activity = frame.get("activity")
        detail = activity.get("detail") if isinstance(activity, dict) else None
        if not isinstance(detail, dict) or not detail.get("state"):
            # The handoff frame announces tracking before the first poll names a state.
            continue
        if first is None:
            first = dict(detail)
        elif int(detail.get("pollCount") or 0) > int(first.get("pollCount") or 0):
            second = dict(detail)

    await _finish(manager, run.run_id)

    assert first is not None and second is not None
    # The state did not change between the two frames; only the poll counter advanced.
    assert first.get("state") == second.get("state") == "TASK_STATE_WORKING"
    assert int(second["pollCount"]) > int(first["pollCount"])
    # Wait states are live-only: they must not grow the replay buffer of a days-long run.
    assert all(message.get("type") != "activity" for message in run.messages)
    assert run.activity is None


async def test_a_presentation_run_keeps_tracking_without_a_viewer(
    tmp_path: Path,
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed page switches to the background cadence; the run continues until the Task ends."""
    import protocol.client_service.run_manager as run_manager_module

    monkeypatch.setattr(run_manager_module, "_DETACHED_TRACKING_GRACE_SECONDS", 0.05)
    runtime = _SwitchableRuntime()

    async def factory(
        resolved_config: SampleConfig,
        scenario: str,
        mode: str,
        transport: str,
        discovery: str,
        *,
        protocol: str = PROTOCOL_A2A_T,
        run_id: str = "",
    ) -> _SwitchableRuntime:
        del resolved_config, scenario, mode, transport, discovery, protocol, run_id
        return runtime

    manager = RunManager(
        config=replace(
            offline_config,
            task_poll_interval_seconds=0.01,
            nms_state_poll_interval=0.01,
        ),
        runtime_factory=factory,
        catalog=DemoRunCatalog(),
        runs_dir=tmp_path / "runs",
    )
    run = await manager.start(
        "information",
        "auto",
        "sse",
        protocol=PROTOCOL_PURE,
        source={"kind": "presentation"},
    )

    deadline = asyncio.get_running_loop().time() + 3.0
    while not run.detached and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)

    assert run.detached is True
    assert run.status == "running"
    assert run.result is not None
    assert run.result.final_state == FINAL_STATE_INCOMPLETE

    runtime.a2a_client.state = TaskState.TASK_STATE_COMPLETED
    await _finish(manager, run.run_id)

    assert run.status == "finished"
    assert run.result is not None
    assert run.result.final_state == FINAL_STATE_TASK_COMPLETED
    # Polling observes the remote Task; it must never put a fabricated status row on the timeline.
    assert run.transcript is not None
    assert not any(
        "The remote task is" in str(entry.get("summary") or "") for entry in run.transcript.events
    )
    polls = [span for span in run.traces if span.get("kind") == "task_poll"]
    assert polls
    assert all(
        (span["actor"], span["side"], span["direction"]) == ("client", "client", "poll")
        for span in polls
    )


class _PollRuntime:
    """Runtime double holding only the client used to poll a recovered Task."""

    def __init__(self, client: _SwitchableA2AClient) -> None:
        self.a2a_client = client
        self.nms_client = None
        self.manual_provider = None
        self.closed = False

    async def aclose(self) -> None:
        """Record the release."""
        self.closed = True


async def test_a_restart_recovers_a_run_that_still_has_a_remote_task(
    tmp_path: Path,
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persisted running row with ``server_task_id`` is rehydrated, not interrupted."""
    store = SqliteClientStore(tmp_path / "client.sqlite3")
    await store.ensure_conversation(conversation_id="conv-1")
    await store.create_run(
        ClientRunRecord(
            run_id="run-1",
            conversation_id="conv-1",
            scenario="information",
            mode="auto",
            transport="sse",
            discovery="direct",
            user_request="request",
            server_task_id="task-1",
            server_context_id="ctx-1",
            owner_pid=2**31 - 1,
            source={"kind": "presentation"},
            chain_id="chain-1",
            parent_run_id="blocked-1",
            trace_id="0123456789abcdef0123456789abcdef",
        )
    )
    await store.ensure_conversation(conversation_id="conv-2")
    await store.create_run(
        ClientRunRecord(
            run_id="lost",
            conversation_id="conv-2",
            scenario="information",
            mode="auto",
            transport="sse",
            discovery="direct",
            user_request="request",
            owner_pid=2**31 - 1,
            source={"kind": "presentation"},
        )
    )
    await store.ensure_conversation(conversation_id="conv-gateway")
    await store.create_run(
        ClientRunRecord(
            run_id="gateway-1",
            conversation_id="conv-gateway",
            scenario="information",
            mode="auto",
            transport="sse",
            discovery="direct",
            user_request="request",
            server_task_id="task-gateway",
            owner_pid=2**31 - 1,
            source={"kind": "gateway"},
        )
    )
    client = _SwitchableA2AClient()
    poll_runtime = _PollRuntime(client)

    async def fake_build_poll_runtime(
        config: SampleConfig,
        *,
        discovery: str,
    ) -> _PollRuntime:
        del config, discovery
        return poll_runtime

    monkeypatch.setattr(
        "protocol.client_service.run_manager.build_poll_runtime",
        fake_build_poll_runtime,
    )
    manager = RunManager(
        config=replace(
            offline_config,
            task_poll_interval_seconds=0.01,
            nms_state_poll_interval=0.01,
        ),
        runtime_factory=lambda *args, **kwargs: None,  # type: ignore[arg-type,return-value]
        catalog=DemoRunCatalog(),
        runs_dir=tmp_path / "runs",
        store=store,
    )

    assert await manager.recover_runs() == 1

    run = manager.get("run-1")
    assert run is not None
    assert run.status == "running"
    assert run.detached is True
    assert run.runtime is poll_runtime
    assert run.snapshot()["finalState"] is None
    # The recovered run keeps the correction chain and the vendor trace it was started with.
    assert run.chain_id == "chain-1"
    assert run.parent_run_id == "blocked-1"
    assert run.trace_id == "0123456789abcdef0123456789abcdef"
    assert manager.get("gateway-1") is None
    lost = await store.get_run("lost")
    assert lost is not None and lost.status == "interrupted"

    client.state = TaskState.TASK_STATE_COMPLETED
    await _finish(manager, "run-1")

    assert run.status == "finished"
    assert run.result is not None
    assert run.result.final_state == FINAL_STATE_TASK_COMPLETED
    assert poll_runtime.closed is True
    stored = await store.get_run("run-1")
    assert stored is not None
    assert stored.status == "finished"
    assert stored.server_task_id == "task-1"


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


async def test_cancel_without_remote_skips_the_transport(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """The Running-tasks picker already cancelled the NMS Task; do not cancel it twice."""
    wait = asyncio.Event()
    manager, _, runtimes = _manager(tmp_path, offline_config, wait=wait)
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

    assert await manager.cancel(run.run_id, remote=False) is True

    assert canceled == []
    assert run.status == "interrupted"


async def test_active_runs_for_task_matches_the_nms_task(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    wait = asyncio.Event()
    manager, _, runtimes = _manager(tmp_path, offline_config, wait=wait)
    run = await manager.start("information", "auto", "sse", user_request="slow")
    while not runtimes or not runtimes[0].entered.is_set():
        await asyncio.sleep(0.01)
    run.remote_task_id = "nms-1"

    assert manager.active_runs_for_task("nms-1") == [run]
    assert manager.active_runs_for_task("other") == []

    run.target_task_id = "nms-2"
    assert manager.active_runs_for_task("nms-2") == [run]


def test_a_polled_task_extracts_its_report_without_synthesizing_events() -> None:
    """A GetTask snapshot yields the report only; it is not a wire status/artifact update."""
    from protocol.client_service.run_manager import _polled_task_from_events

    polled = _polled_task_from_events(
        state_name="TASK_STATE_COMPLETED",
        context_id="ctx-1",
        artifacts=[
            {
                "kind": "artifact",
                "task_id": "task-1",
                "context_id": "ctx-1",
                "name": "energy saving intent report",
                "text": "done",
            }
        ],
    )

    assert polled.state_name == "TASK_STATE_COMPLETED"
    assert polled.report == {
        "name": "energy saving intent report",
        "text": "done",
        "data": None,
    }


def test_recommendation_frame_reports_an_unavailable_agent() -> None:
    """A failed recommendation still opens the page's manual decision controls."""
    from protocol.client_service.run_manager import _recommendation_frame

    frame = _recommendation_frame(
        mode="confirm",
        propose=NegotiationProposeView(
            negotiation_id="neg-1",
            kind="target",
            round=1,
            max_rounds=3,
            content="the target overlaps",
            performative="PROPOSE",
        ),
        session=NegotiationContext(
            id="neg-1",
            round=1,
            max_rounds=3,
            performative=NegotiationPerformative.PROPOSE,
        ),
        recommendation=None,
        recommendation_error="DecisionProviderError: no parseable decision",
        timeout_seconds=300.0,
        requested_at="2026-11-08T03:44:09.538Z",
    )

    assert frame["recommendationUnavailable"] is True
    assert frame["recommendationError"] == "DecisionProviderError: no parseable decision"
    assert "recommendation" not in frame


def test_recommendation_frame_carries_the_agent_decision() -> None:
    """A successful recommendation keeps the frame shape the page already renders."""
    from protocol.client_service.run_manager import _recommendation_frame

    decision = NegotiationDecision(
        action="counter",
        rationale="overlap",
        counter_proposal="keep 17:30~20:00",
    )
    frame = _recommendation_frame(
        mode="confirm",
        propose=NegotiationProposeView(
            negotiation_id="neg-1",
            kind="target",
            round=1,
            max_rounds=3,
            content="the target overlaps",
            performative="PROPOSE",
        ),
        session=NegotiationContext(
            id="neg-1",
            round=1,
            max_rounds=3,
            performative=NegotiationPerformative.PROPOSE,
        ),
        recommendation=decision,
        recommendation_error=None,
        timeout_seconds=300.0,
        requested_at="2026-11-08T03:44:09.538Z",
    )

    assert frame["recommendation"] == decision.model_dump()
    assert "recommendationUnavailable" not in frame


def test_recommendation_frame_lists_the_items_an_information_proposal_asks_for() -> None:
    """The page needs the asked items to label the card and pick a matching example."""
    from protocol.client_service.run_manager import _recommendation_frame

    frame = _recommendation_frame(
        mode="confirm",
        propose=NegotiationProposeView(
            negotiation_id="neg-1",
            kind="information",
            round=1,
            max_rounds=3,
            content=("Energy saving region is not recognized. Please provide the correct region name."),
            performative="PROPOSE",
        ),
        session=NegotiationContext(
            id="neg-1",
            round=1,
            max_rounds=3,
            performative=NegotiationPerformative.PROPOSE,
        ),
        recommendation=None,
        recommendation_error=None,
        timeout_seconds=300.0,
        requested_at="2026-11-08T03:44:09.538Z",
    )

    assert frame["missingItems"] == ["Region"]


def test_recommendation_frame_reports_no_missing_items_for_a_target_proposal() -> None:
    """A target/feasibility proposal carries an empty list, never a guess."""
    from protocol.client_service.run_manager import _recommendation_frame

    frame = _recommendation_frame(
        mode="confirm",
        propose=NegotiationProposeView(
            negotiation_id="neg-1",
            kind="target",
            round=1,
            max_rounds=3,
            content="the target overlaps",
            performative="PROPOSE",
        ),
        session=NegotiationContext(
            id="neg-1",
            round=1,
            max_rounds=3,
            performative=NegotiationPerformative.PROPOSE,
        ),
        recommendation=None,
        recommendation_error=None,
        timeout_seconds=300.0,
        requested_at="2026-11-08T03:44:09.538Z",
    )

    assert frame["missingItems"] == []


def test_a_polled_task_keeps_the_status_message_text_and_metadata() -> None:
    """A delayed question is in the Task status message; polling must not discard it."""
    from protocol.client_service.run_manager import _polled_task_from_a2a

    status = TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED)
    message = Message(message_id="message-1", role=Role.ROLE_AGENT)
    message.parts.add(text="Energy saving region is not recognized.")
    message.metadata.update({"pure": {"round": 2, "recommendation": {"action": "accept"}}})
    status.message.CopyFrom(message)
    task = Task(id="outer-1", context_id="ctx-1", status=status)

    polled = _polled_task_from_a2a(task, "outer-1")

    assert polled.text == "Energy saving region is not recognized."
    assert polled.metadata == {"pure": {"round": 2.0, "recommendation": {"action": "accept"}}}


async def test_a_polled_input_required_is_answered_through_the_pure_decision(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """The page's pure decision card must also open for a question observed by polling."""
    from types import SimpleNamespace

    from protocol.client_service.run_manager import _PolledTask
    from protocol.client_service.run_state import RunState

    manager, _store, _runtimes = _manager(tmp_path, offline_config)
    run = RunState(
        run_id="run-1",
        scenario="night",
        mode="confirm",
        transport="sse",
        protocol=PROTOCOL_PURE,
    )
    events: list[dict[str, object]] = []
    sent: list[object] = []

    async def pure_answer(event: dict[str, object]) -> str:
        events.append(dict(event))
        return "Region A"

    class _Transport:
        async def send(self, request: object, call_context: object) -> list[object]:
            del call_context
            sent.append(request)
            return []

    run.runtime = SimpleNamespace(pure_answer=pure_answer, transport=_Transport())
    polled = _PolledTask(
        state_name="TASK_STATE_INPUT_REQUIRED",
        context_id="ctx-1",
        reports=[],
        report=None,
        text="Energy saving region is not recognized.",
        metadata={"pure": {"round": 2, "recommendation": {"action": "accept"}}},
    )

    await manager._answer_remote_question(run, "outer-1", polled)

    assert events[0]["text"] == "Energy saving region is not recognized."
    assert events[0]["metadata"]["pure"]["round"] == 2
    request = sent[0]
    assert request.message.parts[0].text == "Region A"
    assert request.message.task_id == "outer-1"
    assert request.message.context_id == "ctx-1"


async def test_the_long_task_watcher_answers_a_polled_question(
    tmp_path: Path,
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A question that arrives after the initial stream closed must still reach the operator."""
    from types import SimpleNamespace

    from protocol.client_service.run_manager import _PolledTask
    from protocol.client_service.run_state import RunState

    config = replace(offline_config, nms_state_poll_interval=0.01, task_poll_interval_seconds=0.01)
    manager, _store, _runtimes = _manager(tmp_path, config)
    run = RunState(
        run_id="run-1",
        scenario="night",
        mode="confirm",
        transport="sse",
        protocol=PROTOCOL_PURE,
    )
    run.runtime = SimpleNamespace()
    answered: list[tuple[str, str]] = []

    async def fake_answer(run_arg: RunState, task_id: str, polled: object) -> None:
        del run_arg
        answered.append((task_id, polled.text))

    async def fake_poll(run_arg: RunState, task_id: str) -> object:
        del run_arg, task_id
        return polls.pop(0)

    monkeypatch.setattr(manager, "_answer_remote_question", fake_answer)
    monkeypatch.setattr(manager, "_poll_remote_task", fake_poll)
    polls = [
        _PolledTask("TASK_STATE_WORKING", "ctx-1", [], None),
        _PolledTask("TASK_STATE_INPUT_REQUIRED", "ctx-1", [], None, text="Which region?"),
        _PolledTask("TASK_STATE_COMPLETED", "ctx-1", [], None),
    ]
    result = ClientFlowResult(
        final_state=FINAL_STATE_INCOMPLETE,
        task_id="outer-1",
        context_id="ctx-1",
    )

    finished = await asyncio.wait_for(manager._watch_long_task(run, result), timeout=3.0)

    assert answered == [("outer-1", "Which region?")]
    assert finished.final_state == FINAL_STATE_TASK_COMPLETED


async def test_a_polled_question_accepts_a_sync_pure_answer(
    tmp_path: Path,
    offline_config: SampleConfig,
) -> None:
    """Auto/scripted modes use a plain callable, not the confirmation coroutine."""
    from types import SimpleNamespace

    from protocol.client_service.run_manager import _PolledTask
    from protocol.client_service.run_state import RunState

    manager, _store, _runtimes = _manager(tmp_path, offline_config)
    run = RunState(
        run_id="run-1",
        scenario="night",
        mode="auto",
        transport="sse",
        protocol=PROTOCOL_PURE,
    )
    sent: list[object] = []

    class _Transport:
        async def send(self, request: object, call_context: object) -> list[object]:
            del call_context
            sent.append(request)
            return []

    run.runtime = SimpleNamespace(pure_answer=lambda event: "Region A", transport=_Transport())
    polled = _PolledTask(
        state_name="TASK_STATE_INPUT_REQUIRED",
        context_id="ctx-1",
        reports=[],
        report=None,
        text="Which region?",
    )

    await manager._answer_remote_question(run, "outer-1", polled)

    assert sent[0].message.parts[0].text == "Region A"


def test_polled_completed_with_a_refusal_text_is_a_failed_run() -> None:
    """A polling watcher applies the same COMPLETED-with-refusal rule as the streamed flow."""
    from protocol.client_service.run_manager import _final_state_for_task_state

    assert (
        _final_state_for_task_state(
            "TASK_STATE_COMPLETED",
            'The region name "TestRegion" is not recognized.',
        )
        == FINAL_STATE_TASK_FAILED
    )
    assert _final_state_for_task_state("TASK_STATE_COMPLETED", "Task completed.") == FINAL_STATE_TASK_COMPLETED
    assert (
        _final_state_for_task_state(
            "TASK_STATE_COMPLETED",
            "The region is not recognized.",
            has_report=True,
        )
        == FINAL_STATE_TASK_COMPLETED
    )


def test_a_failed_pure_run_says_why_the_downstream_refused(tmp_path: Path) -> None:
    """The page transcript must carry the vendor's reason, not only the generic final state."""
    from protocol.client_service.run_manager import _run_summary
    from protocol.client_service.run_state import RunState
    from protocol.transcript import NegotiationTranscript

    run = RunState(run_id="run-1", scenario="night", mode="confirm", transport="sse")
    run.protocol = PROTOCOL_PURE
    run.transcript = NegotiationTranscript(
        scenario="night",
        language="en-US",
        model_mode="live",
        mode="confirm",
        transport="sse",
        runs_dir=tmp_path,
    )
    run.result = ClientFlowResult(final_state=FINAL_STATE_TASK_FAILED)
    run.record_event(
        {
            "kind": "status",
            "task_id": "nms-task-1",
            "context_id": "nms-ctx-1",
            "state": "TASK_STATE_COMPLETED",
            "text": 'The region names "Region A" and "Lianqiu Lake" are not recognized.',
        }
    )

    assert _run_summary(run) == ('The region names "Region A" and "Lianqiu Lake" are not recognized.')


def test_the_question_turn_names_the_downstream() -> None:
    """The relayed vendor question is part of the conversation, not Explorer-only detail."""
    from protocol.client_service.run_manager import _question_turn
    from protocol.client_service.run_state import RunState
    from shared.config import DISCOVERY_NMS

    run = RunState(run_id="run-1", scenario="night", mode="confirm", transport="sse")
    run.protocol = PROTOCOL_PURE
    run.discovery = DISCOVERY_NMS
    run.proposals = [
        NegotiationProposeView(
            negotiation_id="neg-1",
            kind="information",
            round=1,
            max_rounds=5,
            content="Region not recognized. Please provide a valid region name.",
            performative="PROPOSE",
        )
    ]

    assert _question_turn(run) == (
        "The RAN side asked: Region not recognized. Please provide a valid region name."
    )
