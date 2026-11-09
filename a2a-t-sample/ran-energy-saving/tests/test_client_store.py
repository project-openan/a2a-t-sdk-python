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


async def test_a_run_round_trips_its_vendor_trace_id(tmp_path: Path) -> None:
    """The vendor-facing trace id survives a restart; falling back to run_id loses the linkage."""
    store = SqliteClientStore(tmp_path / "client.sqlite3")
    await store.create_run(
        ClientRunRecord(
            run_id="run-trace",
            scenario="information",
            mode="auto",
            transport="sse",
            discovery="direct",
            user_request="request",
            trace_id="0123456789abcdef0123456789abcdef",
        )
    )

    record = await store.get_run("run-trace")

    assert record is not None
    assert record.trace_id == "0123456789abcdef0123456789abcdef"


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

    # Constructing the store applies the remaining migrations.
    SqliteClientStore(path)
    with connect(path) as connection:
        assert schema_version(connection) == 9
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
        source_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(runs)")
        }
        sources = connection.execute("SELECT source_kind, source_json FROM runs").fetchall()
        downstream = connection.execute(
            "SELECT downstream_task_id, downstream_context_id, downstream_hop FROM runs"
        ).fetchall()
        chains = connection.execute("SELECT chain_id, parent_run_id FROM runs").fetchall()
        trace_ids = connection.execute("SELECT trace_id FROM runs").fetchall()
    assert statuses == {"older": "interrupted", "newer": "running"}
    # Rows written before the switch existed were A2A-T; the migration keeps that history.
    assert protocols == {"a2a-t"}
    # The request-origin columns are added to an existing database; old rows read as unknown.
    assert {"source_kind", "source_json"} <= source_columns
    assert set(sources) == {("", "{}")}
    # The downstream identity columns exist too, and old rows carry no far-side Task.
    assert {"downstream_task_id", "downstream_context_id", "downstream_hop"} <= source_columns
    assert set(downstream) == {(None, None, "")}
    # The correction-chain columns exist; a legacy row carries no chain yet.
    assert {"chain_id", "parent_run_id"} <= source_columns
    assert set(chains) == {("", "")}
    # The vendor trace id column exists; a legacy row has no recorded value.
    assert "trace_id" in source_columns
    assert set(trace_ids) == {("",)}

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


async def test_mark_running_interrupted_keeps_runs_with_a_remote_task(tmp_path: Path) -> None:
    """A running row with ``server_task_id`` is recoverable, not interrupted."""
    store = SqliteClientStore(tmp_path / "client.sqlite3")
    for run_id, task_id in (("long", "task-1"), ("lost", None)):
        await store.ensure_conversation(conversation_id=f"conv-{run_id}")
        await store.create_run(
            ClientRunRecord(
                run_id=run_id,
                conversation_id=f"conv-{run_id}",
                scenario="information",
                mode="auto",
                transport="sse",
                discovery="nms",
                user_request="request",
                server_task_id=task_id,
            )
        )

    assert await store.mark_running_interrupted() == 1

    long_run = await store.get_run("long")
    lost = await store.get_run("lost")
    assert long_run is not None and long_run.status == "running"
    assert lost is not None and lost.status == "interrupted"


async def test_list_running_runs_and_mark_one_interrupted(tmp_path: Path) -> None:
    store = SqliteClientStore(tmp_path / "client.sqlite3")
    for run_id in ("run-1", "run-2"):
        await store.ensure_conversation(conversation_id=f"conv-{run_id}")
        await store.create_run(
            ClientRunRecord(
                run_id=run_id,
                conversation_id=f"conv-{run_id}",
                scenario="information",
                mode="auto",
                transport="sse",
                discovery="direct",
                user_request="request",
            )
        )

    running = await store.list_running_runs()
    assert [record.run_id for record in running] == ["run-1", "run-2"]

    await store.mark_run_interrupted("run-1")
    running = await store.list_running_runs()
    assert [record.run_id for record in running] == ["run-2"]


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


async def test_run_source_round_trips_and_filters_the_console_list(tmp_path: Path) -> None:
    """The request origin is persisted and queryable; old runs keep an empty source."""
    path = tmp_path / "client.sqlite3"
    store = SqliteClientStore(path)
    await store.ensure_conversation(conversation_id="conv-1")
    await store.create_run(
        ClientRunRecord(
            run_id="run-source",
            conversation_id="conv-1",
            scenario="night",
            mode="auto",
            transport="sse",
            discovery="registry",
            user_request="save energy",
            source_kind="tester",
            source={"kind": "tester", "batchId": "batch-1", "caseId": "TC-001"},
        )
    )
    await store.ensure_conversation(conversation_id="conv-legacy")
    await store.create_run(
        ClientRunRecord(
            run_id="run-legacy",
            conversation_id="conv-legacy",
            scenario="night",
            mode="auto",
            transport="sse",
            discovery="registry",
            user_request="legacy",
        )
    )

    created = await store.get_run("run-source")
    assert created is not None
    assert created.source_kind == "tester"
    assert created.source["caseId"] == "TC-001"

    rows = read_client_run_summaries(path, source_kind="tester")
    assert [row["runId"] for row in rows] == ["run-source"]
    assert rows[0]["sourceKind"] == "tester"

    detail = read_client_run_detail(path, "run-source")
    assert detail is not None
    assert detail["source"]["caseId"] == "TC-001"

    legacy = read_client_run_detail(path, "run-legacy")
    assert legacy is not None
    assert legacy["sourceKind"] == ""
    assert legacy["source"] == {}


async def test_downstream_identity_round_trips_and_is_searchable(tmp_path: Path) -> None:
    """The far-side Task lives beside the server one and is reachable from the console search."""
    path = tmp_path / "client.sqlite3"
    store = SqliteClientStore(path)
    await store.ensure_conversation(conversation_id="conv-1")
    await store.create_run(
        ClientRunRecord(
            run_id="run-1",
            conversation_id="conv-1",
            scenario="night",
            mode="auto",
            transport="webhook",
            discovery="nms",
            user_request="save energy",
            protocol="pure",
            server_task_id="outer-task",
            server_context_id="outer-ctx",
        )
    )
    await store.update_run(
        "run-1",
        status="finished",
        downstream_task_id="nms-task",
        downstream_context_id="nms-ctx",
        downstream_hop="nms",
    )

    run = await store.get_run("run-1")
    assert run is not None
    # The server identity is never replaced by the downstream one.
    assert run.server_task_id == "outer-task"
    assert run.server_context_id == "outer-ctx"
    assert run.downstream_task_id == "nms-task"
    assert run.downstream_context_id == "nms-ctx"
    assert run.downstream_hop == "nms"

    for needle in ("nms-ctx", "nms-task", "outer-ctx", "outer-task"):
        rows = read_client_run_summaries(path, query=needle)
        assert [row["runId"] for row in rows] == ["run-1"], needle
        assert rows[0]["downstreamContextId"] == "nms-ctx"
        assert rows[0]["downstreamHop"] == "nms"

    detail = read_client_run_detail(path, "run-1")
    assert detail is not None
    assert detail["serverContextId"] == "outer-ctx"
    assert detail["downstreamTaskId"] == "nms-task"
    assert detail["downstreamContextId"] == "nms-ctx"
    assert detail["downstreamHop"] == "nms"


async def test_chain_identity_round_trips_through_the_store(tmp_path: Path) -> None:
    """A retry keeps the chain id, names its parent and still gets its own conversation."""
    path = tmp_path / "client.sqlite3"
    store = SqliteClientStore(path)
    await store.ensure_conversation(conversation_id="conv-1")
    await store.ensure_conversation(conversation_id="conv-2")
    await store.create_run(
        ClientRunRecord(
            run_id="run-1",
            conversation_id="conv-1",
            scenario="information",
            mode="auto",
            transport="sse",
            discovery="registry",
            user_request="blocked request",
            chain_id="chain-1",
        )
    )
    await store.update_run("run-1", status="finished", final_state="input_invalid")
    await store.create_run(
        ClientRunRecord(
            run_id="run-2",
            conversation_id="conv-2",
            scenario="information",
            mode="auto",
            transport="sse",
            discovery="registry",
            user_request="corrected request",
            chain_id="chain-1",
            parent_run_id="run-1",
        )
    )

    first = await store.get_run("run-1")
    second = await store.get_run("run-2")
    assert first is not None and second is not None
    assert first.chain_id == "chain-1" and first.parent_run_id == ""
    assert second.chain_id == "chain-1" and second.parent_run_id == "run-1"
    # The retry is a new conversation, so the blocked turn is never prompt memory.
    assert second.conversation_id == "conv-2"


async def test_run_summaries_expose_the_trace_id_and_filter_by_it(tmp_path: Path) -> None:
    """The observe page needs a run's Langfuse trace id and an exact lookup by it."""
    path = tmp_path / "client.sqlite3"
    store = SqliteClientStore(path)
    await store.ensure_conversation(conversation_id="conv-a")
    await store.create_run(
        ClientRunRecord(
            run_id="run-a",
            conversation_id="conv-a",
            scenario="night",
            mode="auto",
            transport="sse",
            discovery="registry",
            user_request="first",
            trace_id="a" * 32,
            source_kind="tester",
            source={"kind": "tester", "caseId": "TC-001", "batchId": "batch-1"},
        )
    )
    await store.ensure_conversation(conversation_id="conv-b")
    await store.create_run(
        ClientRunRecord(
            run_id="run-b",
            conversation_id="conv-b",
            scenario="night",
            mode="auto",
            transport="sse",
            discovery="registry",
            user_request="second",
            trace_id="b" * 32,
        )
    )

    rows = read_client_run_summaries(path)
    assert {str(row["runId"]): row["traceId"] for row in rows} == {
        "run-a": "a" * 32,
        "run-b": "b" * 32,
    }
    by_run = {str(row["runId"]): row for row in rows}
    assert by_run["run-a"]["caseId"] == "TC-001"
    assert by_run["run-a"]["batchId"] == "batch-1"
    assert by_run["run-b"]["caseId"] == ""

    filtered = read_client_run_summaries(path, trace_id="b" * 32)
    assert [str(row["runId"]) for row in filtered] == ["run-b"]


async def test_negotiation_events_carry_the_protocol_that_produced_them(
    tmp_path: Path,
) -> None:
    """pure and A2A-T share the table; the column keeps a cross-run report honest."""
    store = SqliteClientStore(tmp_path / "client.sqlite3")
    await store.record_negotiation_event(
        NegotiationEventRecord.new(
            run_id="run-a2at",
            seq=1,
            direction="inbound",
            actor="server",
            kind="information",
            round=1,
            performative="PROPOSE",
        )
    )
    await store.record_negotiation_event(
        NegotiationEventRecord.new(
            run_id="run-pure",
            seq=1,
            direction="inbound",
            actor="server",
            kind="target",
            round=1,
            performative="PROPOSE",
            protocol="pure",
        )
    )

    a2at = await store.list_negotiation_events("run-a2at")
    pure = await store.list_negotiation_events("run-pure")

    assert a2at[0].protocol == "a2a-t"
    assert pure[0].protocol == "pure"
    assert pure[0].kind == "target"


def test_the_protocol_migration_backfills_existing_events(tmp_path: Path) -> None:
    """Rows written before the column existed are A2A-T by definition."""
    from protocol.client_store.store import _MIGRATIONS
    from shared.sqlite import apply_migrations, connect, schema_version

    path = tmp_path / "client.sqlite3"
    with connect(path) as connection:
        assert apply_migrations(connection, _MIGRATIONS[:-1]) == len(_MIGRATIONS) - 1
        connection.execute(
            """
            INSERT INTO negotiation_events
                (event_id, run_id, seq, direction, actor, content_json, created_at)
            VALUES ('event-1', 'run-1', 1, 'inbound', 'server', '{}', '2026-01-01T00:00:00Z')
            """
        )

    SqliteClientStore(path)

    with connect(path) as connection:
        assert schema_version(connection) == len(_MIGRATIONS)
        row = connection.execute(
            "SELECT protocol FROM negotiation_events WHERE event_id='event-1'"
        ).fetchone()
    assert row is not None
    assert row[0] == "a2a-t"
