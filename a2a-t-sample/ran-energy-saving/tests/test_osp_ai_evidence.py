"""Tests for the read-only evidence helpers of ``eval.osp.ai``."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from eval.osp.ai.evidence import (
    excerpt,
    lookup_bindings,
    read_run_row,
    read_task_snapshot,
    read_trace,
    read_transcript,
    read_wire,
    read_wire_errors,
    redact,
)


def _write_jsonl(path: Path, records: list[object]) -> None:
    """Write one JSONL file, one value per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


def test_read_wire_reads_past_the_ui_window(tmp_path: Path) -> None:
    """The full file is read: a record after the UI's 200-row window is still returned."""
    run_dir = tmp_path / "run-1"
    records: list[object] = [
        {
            "ts": "2026-11-10T00:00:00Z",
            "dir": "out",
            "method": "message:send",
            "url": "https://nms/a2a/json/message:send",
            "body": {"message": {"taskId": "task-1"}},
        }
    ]
    records.extend(
        {"ts": "2026-11-10T00:00:01Z", "dir": "in", "line": "data: heartbeat", "seq": index}
        for index in range(250)
    )
    _write_jsonl(run_dir / "wire.jsonl", records)

    rows = read_wire(run_dir / "wire.jsonl")
    assert len(rows) == 251
    assert rows[0]["method"] == "message:send"
    assert rows[-1]["seq"] == 249


def test_read_wire_filters_direction_kind_and_task(tmp_path: Path) -> None:
    """Direction, kind (top-level and nested) and task ids all filter the same file."""
    wire = tmp_path / "wire.jsonl"
    _write_jsonl(
        wire,
        [
            {"dir": "out", "method": "message:send", "body": {"message": {"taskId": "task-1"}}},
            {"dir": "in", "type": "status", "body": {"performative": "propose", "task_id": "task-1"}},
            {"dir": "in", "kind": "status", "body": {"reference": {"id": "task-1"}}},
            {"dir": "in", "method": "task:get", "body": {"reference": {"id": "task-2"}}},
            {"dir": "out", "type": "notify", "body": {"message": {"messageId": "note"}}},
        ],
    )

    assert len(read_wire(wire, direction="out")) == 2
    assert len(read_wire(wire, kind="message:send")) == 1
    assert len(read_wire(wire, kind="propose")) == 1
    assert len(read_wire(wire, kind="status")) == 2
    assert len(read_wire(wire, task_id="task-1")) == 3
    assert len(read_wire(wire, task_id="task-2")) == 1
    assert len(read_wire(wire, task_id="task-1", direction="in")) == 2
    assert read_wire(wire, kind="missing") == []


def test_read_wire_grep_and_limit_keep_the_first_matches(tmp_path: Path) -> None:
    """Grep is case-insensitive and ``limit`` returns the earliest matches, not the newest."""
    wire = tmp_path / "wire.jsonl"
    _write_jsonl(
        wire,
        [
            {"dir": "out", "method": "message:send", "url": "https://nms/First"},
            {"dir": "out", "method": "message:send", "url": "https://nms/Second"},
            {"dir": "in", "line": "data: nothing"},
            {"dir": "out", "method": "message:send", "url": "https://nms/Third"},
        ],
    )

    matches = read_wire(wire, direction="out", limit=2)
    assert [row["url"] for row in matches] == ["https://nms/First", "https://nms/Second"]
    assert read_wire(wire, limit=0) == []
    assert len(read_wire(wire, grep="first")) == 1
    assert len(read_wire(wire, grep="FIRST")) == 1


def test_read_wire_skips_malformed_lines(tmp_path: Path) -> None:
    """Malformed JSON and non-object lines are skipped instead of raising."""
    wire = tmp_path / "wire.jsonl"
    wire.write_text(
        '{"dir": "out", "method": "message:send"}\n'
        "{not json}\n"
        "[1, 2, 3]\n"
        "\n"
        '{"dir": "in", "line": "data: {}"}\n',
        encoding="utf-8",
    )

    rows = read_wire(wire)
    assert [row["dir"] for row in rows] == ["out", "in"]
    assert read_wire(tmp_path / "missing.jsonl") == []


def test_read_wire_errors_accepts_file_or_run_directory(tmp_path: Path) -> None:
    """The wire-error reader resolves either the run directory or the exact file."""
    run_dir = tmp_path / "run-1"
    _write_jsonl(
        run_dir / "wire-error.jsonl",
        [{"type": "NmsProtocolError", "message": "HTTP 500"}],
    )

    assert read_wire_errors(run_dir)[0]["type"] == "NmsProtocolError"
    assert read_wire_errors(run_dir / "wire-error.jsonl")[0]["message"] == "HTTP 500"
    assert read_wire_errors(tmp_path / "absent") == []


def _client_state(path: Path) -> None:
    """Create the minimal client-store schema used by the readers."""
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE runs (
              run_id TEXT PRIMARY KEY,
              status TEXT,
              transcript_json TEXT,
              trace_json TEXT
            );
            CREATE TABLE task_snapshots (
              run_id TEXT PRIMARY KEY,
              task_id TEXT,
              context_id TEXT,
              state TEXT,
              updated_at TEXT,
              payload_json TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?)",
            (
                "run-1",
                "finished",
                json.dumps({"events": [{"kind": "task_created"}]}),
                json.dumps({"spans": [{"name": "dispatch"}]}),
            ),
        )
        connection.execute(
            "INSERT INTO task_snapshots VALUES (?, ?, ?, ?, ?, ?)",
            (
                "run-1",
                "task-1",
                "ctx-1",
                "SUBMITTED",
                "2026-11-10T00:00:00Z",
                json.dumps({"status": {"state": "SUBMITTED"}}),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def test_client_store_reads(tmp_path: Path) -> None:
    """Run rows, transcript/trace documents and task snapshots are parsed from SQLite."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _client_state(runs_dir / "client-state.sqlite3")

    row = read_run_row(runs_dir, "run-1")
    assert row is not None
    assert row["status"] == "finished"
    assert read_transcript(runs_dir, "run-1") == {"events": [{"kind": "task_created"}]}
    assert read_trace(runs_dir, "run-1") == {"spans": [{"name": "dispatch"}]}

    snapshot = read_task_snapshot(runs_dir, "run-1")
    assert snapshot is not None
    assert snapshot["task_id"] == "task-1"
    assert snapshot["payload"] == {"status": {"state": "SUBMITTED"}}

    assert read_run_row(runs_dir, "unknown") is None
    assert read_transcript(runs_dir, "unknown") is None
    assert read_task_snapshot(runs_dir, "unknown") is None


def test_client_store_missing_file_or_schema_returns_none(tmp_path: Path) -> None:
    """A missing database or a database without the tables never raises."""
    assert read_run_row(tmp_path / "absent", "run-1") is None

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    sqlite3.connect(empty_dir / "client-state.sqlite3").close()
    assert read_run_row(empty_dir, "run-1") is None
    assert read_task_snapshot(empty_dir, "run-1") is None


def _task_stores(runs_dir: Path) -> None:
    """Create both Task databases with one matching row each."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    task_connection = sqlite3.connect(runs_dir / "task-status.sqlite3")
    try:
        task_connection.executescript(
            """
            CREATE TABLE tasks (
              task_id TEXT PRIMARY KEY,
              owner TEXT,
              context_id TEXT,
              state TEXT,
              updated_at REAL,
              run_id TEXT,
              summary TEXT,
              task_blob BLOB
            );
            """
        )
        task_connection.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("task-1", "owner", "ctx-1", "WORKING", 1.0, "run-1", "summary", b""),
        )
        task_connection.commit()
    finally:
        task_connection.close()

    oss_connection = sqlite3.connect(runs_dir / "oss-task-status.sqlite3")
    try:
        oss_connection.executescript(
            """
            CREATE TABLE downstream_bindings (
              inner_task_id TEXT,
              inner_context_id TEXT,
              outer_task_id TEXT,
              run_id TEXT,
              owner TEXT,
              protocol TEXT,
              state TEXT
            );
            """
        )
        oss_connection.execute(
            "INSERT INTO downstream_bindings VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("inner-1", "inner-ctx-1", "outer-1", "run-2", "owner", "a2a", "working"),
        )
        oss_connection.commit()
    finally:
        oss_connection.close()


def test_lookup_bindings_searches_both_databases(tmp_path: Path) -> None:
    """Task rows and downstream bindings are tagged with their source."""
    runs_dir = tmp_path / "runs"
    _task_stores(runs_dir)

    by_task = lookup_bindings(runs_dir, task_id="task-1")
    assert len(by_task) == 1
    assert by_task[0]["source"] == "task-store"
    assert by_task[0]["context_id"] == "ctx-1"

    by_context = lookup_bindings(runs_dir, context_id="ctx-1")
    assert len(by_context) == 1
    assert by_context[0]["task_id"] == "task-1"

    by_outer = lookup_bindings(runs_dir, task_id="outer-1")
    assert len(by_outer) == 1
    assert by_outer[0]["source"] == "oss-binding"
    assert by_outer[0]["run_id"] == "run-2"

    by_inner_context = lookup_bindings(runs_dir, context_id="inner-ctx-1")
    assert len(by_inner_context) == 1
    assert by_inner_context[0]["outer_task_id"] == "outer-1"

    assert lookup_bindings(runs_dir, task_id="absent") == []
    assert lookup_bindings(runs_dir) == []
    assert lookup_bindings(tmp_path / "absent") == []


def test_redact_tokens_and_auth_values() -> None:
    """Sensitive keys and Bearer/Basic values are redacted while other types survive."""
    value = {
        "access_token": "abc",
        "Authorization": "Bearer abc",
        "nested": {"clientSecret": "s", "keep": 3},
        "items": [{"password": "p"}, "Basic dXNlcjpwYXNz"],
        "text": "Bearer tok123 / Basic xyz; tail",
        "count": 7,
        "flag": True,
    }

    safe = redact(value)
    assert isinstance(safe, Mapping)
    assert safe["access_token"] == "***"
    assert safe["Authorization"] == "***"
    assert safe["nested"] == {"clientSecret": "***", "keep": 3}
    assert safe["items"][0] == {"password": "***"}
    assert safe["items"][1] == "Basic ***"
    assert safe["text"] == "Bearer *** / Basic ***; tail"
    assert safe["count"] == 7
    assert safe["flag"] is True


def test_excerpt_redacts_and_truncates() -> None:
    """Excerpts keep the first ``max_lines`` lines and mark the cut."""
    payload = {f"key{index}": "中文" for index in range(30)}
    text = excerpt(payload, max_lines=10)
    lines = text.splitlines()
    assert len(lines) == 11
    assert lines[-1] == "… (truncated)"
    assert "中文" in text

    short = excerpt({"token": "abc", "value": 1}, max_lines=20)
    assert "abc" not in short
    assert '"***"' in short
    assert not short.endswith("… (truncated)")
