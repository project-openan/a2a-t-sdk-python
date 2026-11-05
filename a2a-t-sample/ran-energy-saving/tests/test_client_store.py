"""Tests of the client SQLite store (conversations, runs, negotiation audit)."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path

import pytest
from protocol.client_store import (
    ClientRunRecord,
    NegotiationEventRecord,
    RunAlreadyActiveError,
    SqliteClientStore,
    TaskSnapshotRecord,
    now_iso,
    read_client_run_detail,
    read_client_run_summaries,
    read_conversation_detail,
    read_conversation_summaries,
)


async def test_conversation_create_update_and_turn_sequence(tmp_path: Path) -> None:
    store = SqliteClientStore(tmp_path / "client.sqlite3")
    created = await store.ensure_conversation(
        conversation_id="conv-1",
        external_id="ext-1",
        user_id="user-1",
        title="A long conversation title",
    )
    assert created is not None
    assert created.external_id == "ext-1"
    assert created.user_id == "user-1"

    first = await store.append_turn(conversation_id="conv-1", role="user", content="first")
    second = await store.append_turn(
        conversation_id="conv-1", role="assistant", content="second", run_id="run-1"
    )
    assert first is not None and first.seq == 1
    assert second is not None and second.seq == 2

    turns = await store.list_turns("conv-1")
    assert [turn.content for turn in turns] == ["first", "second"]

    recent = await store.list_recent_turns("conv-1", limit=1)
    assert [turn.content for turn in recent] == ["second"]

    updated = await store.ensure_conversation(
        conversation_id="conv-1",
        external_id="ext-2",
        user_id="user-2",
        title="",
    )
    assert updated is not None
    assert updated.external_id == "ext-2"
    assert updated.user_id == "user-2"
    # An empty title never overwrites the existing one.
    assert updated.title == "A long conversation title"


async def test_concurrent_turn_appends_keep_unique_sequence(tmp_path: Path) -> None:
    store = SqliteClientStore(tmp_path / "client.sqlite3")
    await store.ensure_conversation(conversation_id="conv-1")
    await asyncio.gather(
        *(
            store.append_turn(conversation_id="conv-1", role="user", content=f"turn-{index}")
            for index in range(20)
        )
    )
    turns = await store.list_turns("conv-1")
    assert [turn.seq for turn in turns] == list(range(1, 21))


async def test_run_idempotency_update_and_listing(tmp_path: Path) -> None:
    store = SqliteClientStore(tmp_path / "client.sqlite3")
    await store.ensure_conversation(conversation_id="conv-1")
    run = ClientRunRecord(
        run_id="run-1",
        conversation_id="conv-1",
        scenario="information",
        mode="auto",
        transport="sse",
        discovery="registry",
        protocol="pure",
        user_request="please save energy",
        idempotency_key="key-1",
    )
    await store.create_run(run)

    created = await store.get_run("run-1")
    assert created is not None and created.protocol == "pure"
    found = await store.find_run_by_idempotency("conv-1", "key-1")
    assert found is not None and found.run_id == "run-1"
    assert await store.find_run_by_idempotency("conv-1", "") is None

    await store.update_run(
        "run-1",
        status="finished",
        final_state="task_completed",
        server_task_id="task-1",
        transcript_json="/tmp/run-1.json",
    )
    updated = await store.get_run("run-1")
    assert updated is not None
    assert updated.status == "finished"
    assert updated.server_task_id == "task-1"
    assert updated.transcript_json == "/tmp/run-1.json"

    with pytest.raises(ValueError):
        await store.update_run("run-1", unknown_field="value")

    await store.ensure_conversation(conversation_id="conv-2")
    await store.create_run(
        ClientRunRecord(
            run_id="run-2",
            conversation_id="conv-2",
            scenario="target",
            mode="auto",
            transport="sse",
            discovery="registry",
            user_request="second",
        )
    )
    runs = await store.list_runs()
    assert [item.run_id for item in runs] == ["run-1", "run-2"] or [
        item.run_id for item in runs
    ] == ["run-2", "run-1"]
    scoped = await store.list_runs(conversation_id="conv-2")
    assert [item.run_id for item in scoped] == ["run-2"]


async def test_negotiation_events_and_task_snapshot(tmp_path: Path) -> None:
    store = SqliteClientStore(tmp_path / "client.sqlite3")
    inbound = NegotiationEventRecord.new(
        run_id="run-1",
        seq=1,
        direction="inbound",
        actor="server",
        negotiation_id="neg-1",
        kind="information",
        round=1,
        performative="PROPOSE",
        content={"content": "missing region"},
    )
    outbound = NegotiationEventRecord.new(
        run_id="run-1",
        seq=2,
        direction="outbound",
        actor="client",
        negotiation_id="neg-1",
        kind="information",
        round=1,
        performative="ACCEPT",
        content={"action": "accept"},
        decision_action="accept",
        rationale="filled",
    )
    await store.record_negotiation_event(inbound)
    await store.record_negotiation_event(outbound)
    events = await store.list_negotiation_events("run-1")
    assert [event.seq for event in events] == [1, 2]
    assert events[1].decision_action == "accept"

    await store.save_task_snapshot(
        TaskSnapshotRecord(
            run_id="run-1",
            task_id="task-1",
            context_id="ctx-1",
            state="TASK_STATE_WORKING",
            payload={"kind": "status"},
        )
    )
    await store.save_task_snapshot(
        TaskSnapshotRecord(
            run_id="run-1",
            task_id="task-1",
            context_id="ctx-1",
            state="TASK_STATE_COMPLETED",
            payload={"kind": "status", "state": "TASK_STATE_COMPLETED"},
        )
    )
    snapshot = await store.get_task_snapshot("run-1")
    assert snapshot is not None
    assert snapshot.state == "TASK_STATE_COMPLETED"
    assert snapshot.payload["state"] == "TASK_STATE_COMPLETED"


async def test_claim_run_rejects_a_second_active_run(tmp_path: Path) -> None:
    store = SqliteClientStore(tmp_path / "client.sqlite3")
    first = ClientRunRecord(
        run_id="run-1",
        conversation_id="conv-1",
        scenario="information",
        mode="auto",
        transport="sse",
        discovery="registry",
        user_request="first",
    )
    second = ClientRunRecord(
        run_id="run-2",
        conversation_id="conv-1",
        scenario="information",
        mode="auto",
        transport="sse",
        discovery="registry",
        user_request="second",
    )
    await store.claim_run(first)

    with pytest.raises(RunAlreadyActiveError) as conflict:
        await store.claim_run(second)

    assert conflict.value.run_id == "run-1"
    assert await store.get_run("run-2") is None


def test_v2_migration_dedupes_active_runs(tmp_path: Path) -> None:
    """A legacy database with duplicate running rows upgrades to the unique index."""
    from protocol.client_store.store import _MIGRATIONS
    from shared.sqlite import apply_migrations, connect, schema_version

    path = tmp_path / "client.sqlite3"
    stamp = now_iso()
    with connect(path) as connection:
        assert apply_migrations(connection, _MIGRATIONS[:1]) == 1
        for run_id in ("older", "newer"):
            connection.execute(
                """
                INSERT INTO runs
                    (run_id, conversation_id, scenario, mode, transport, discovery,
                     user_request, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "conv-1",
                    "information",
                    "auto",
                    "sse",
                    "registry",
                    "request",
                    "running",
                    stamp,
                    stamp,
                ),
            )

    # Constructing the store applies migration 2.
    SqliteClientStore(path)
    with connect(path) as connection:
        assert schema_version(connection) == 4
        statuses = {
            str(run_id): str(status)
            for run_id, status in connection.execute(
                "SELECT run_id, status FROM runs"
            ).fetchall()
        }
        protocols = {
            str(protocol)
            for (protocol,) in connection.execute("SELECT protocol FROM runs").fetchall()
        }
    assert statuses == {"older": "interrupted", "newer": "running"}
    # Rows written before the switch existed were A2A-T; the migration keeps that history.
    assert protocols == {"a2a-t"}

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        with connect(path) as connection:
            connection.execute(
                """
                INSERT INTO runs
                    (run_id, conversation_id, scenario, mode, transport, discovery,
                     user_request, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "third",
                    "conv-1",
                    "information",
                    "auto",
                    "sse",
                    "registry",
                    "request",
                    "running",
                    stamp,
                    stamp,
                ),
            )


async def test_mark_running_interrupted(tmp_path: Path) -> None:
    store = SqliteClientStore(tmp_path / "client.sqlite3")
    await store.ensure_conversation(conversation_id="conv-1")
    await store.create_run(
        ClientRunRecord(
            run_id="run-1",
            conversation_id="conv-1",
            scenario="information",
            mode="auto",
            transport="sse",
            discovery="registry",
            user_request="request",
        )
    )
    changed = await store.mark_running_interrupted()
    assert changed == 1
    run = await store.get_run("run-1")
    assert run is not None and run.status == "interrupted"
    assert await store.mark_running_interrupted() == 0


async def test_mark_running_interrupted_keeps_live_process_runs(tmp_path: Path) -> None:
    store = SqliteClientStore(tmp_path / "client.sqlite3")
    for run_id, owner_pid in (("live", os.getpid()), ("dead", 2**31 - 1)):
        await store.ensure_conversation(conversation_id=f"conv-{run_id}")
        await store.create_run(
            ClientRunRecord(
                run_id=run_id,
                conversation_id=f"conv-{run_id}",
                scenario="information",
                mode="auto",
                transport="sse",
                discovery="registry",
                user_request="request",
                owner_pid=owner_pid,
            )
        )
    assert await store.mark_running_interrupted() == 1
    live = await store.get_run("live")
    dead = await store.get_run("dead")
    assert live is not None and live.status == "running"
    assert dead is not None and dead.status == "interrupted"


async def test_read_only_helpers_and_missing_database(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite3"
    assert read_client_run_summaries(missing) == []
    assert read_conversation_summaries(missing) == []
    assert read_client_run_detail(missing, "run-1") is None
    assert read_conversation_detail(missing, "conv-1") is None
    assert not missing.exists()

    path = tmp_path / "client.sqlite3"
    store = SqliteClientStore(path)
    await store.ensure_conversation(conversation_id="conv-1", title="conversation")
    await store.append_turn(conversation_id="conv-1", role="user", content="hello")
    await store.create_run(
        ClientRunRecord(
            run_id="run-1",
            conversation_id="conv-1",
            scenario="information",
            mode="auto",
            transport="sse",
            discovery="registry",
            user_request="hello",
            status="finished",
            final_state="task_completed",
            transcript_json=str(tmp_path / "run-1.json"),
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
            content={"content": "proposal"},
        )
    )
    await store.save_task_snapshot(
        TaskSnapshotRecord(run_id="run-1", task_id="task-1", state="TASK_STATE_COMPLETED")
    )

    summaries = read_client_run_summaries(path)
    assert summaries[0]["runId"] == "run-1"
    assert summaries[0]["turnCount"] == 1
    detail = read_client_run_detail(path, "run-1")
    assert detail is not None
    detail_turns = detail["turns"]
    detail_events = detail["negotiationEvents"]
    detail_snapshot = detail["taskSnapshot"]
    assert isinstance(detail_turns, list) and isinstance(detail_turns[0], dict)
    assert isinstance(detail_events, list) and isinstance(detail_events[0], dict)
    assert isinstance(detail_snapshot, dict)
    assert detail_turns[0]["content"] == "hello"
    assert detail_events[0]["performative"] == "PROPOSE"
    assert detail_snapshot["state"] == "TASK_STATE_COMPLETED"

    conversations = read_conversation_summaries(path)
    assert conversations[0]["conversationId"] == "conv-1"
    assert conversations[0]["runCount"] == 1
    conversation = read_conversation_detail(path, "conv-1")
    assert conversation is not None
    conversation_turns = conversation["turns"]
    conversation_runs = conversation["runs"]
    assert isinstance(conversation_turns, list) and isinstance(conversation_turns[0], dict)
    assert isinstance(conversation_runs, list) and isinstance(conversation_runs[0], dict)
    assert conversation_turns[0]["content"] == "hello"
    assert conversation_runs[0]["runId"] == "run-1"
