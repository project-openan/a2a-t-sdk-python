"""Tests of the run-console timeline source split, cache and process-log windows."""

from __future__ import annotations

import json
from pathlib import Path

from demo.web.runs_console.indexer import (
    TimelineCache,
    cap_wire_records,
    merge_server_spans,
    read_process_logs,
    read_server_trace_jsonl,
    read_wire_refs,
    split_transcript,
)
from demo.web.runs_console.timeline import TimelineDocument, build_entries


def _transcript() -> dict[str, object]:
    return {
        "events": [
            {"seq": 1, "entryType": "event", "phase": "dispatch", "summary": "Run started"},
            {
                "seq": 2,
                "entryType": "trace",
                "spanId": "llm_client_1",
                "phase": "task_prompt",
                "summary": "Client LLM",
            },
            {
                "seq": 3,
                "entryType": "trace",
                "spanId": "llm_server_1",
                "side": "server",
                "phase": "task_validation",
                "summary": "Server validation",
            },
            {
                "seq": 4,
                "entryType": "trace",
                "spanId": "llm_server_2",
                "phase": "task_validation",
                "summary": "Legacy server span without side",
            },
        ],
        "decisions": [
            {"seq": 5, "entryType": "decision", "phase": "negotiation", "summary": "Accept"},
        ],
    }


def test_transcript_trace_rows_are_split_by_side_and_span_prefix() -> None:
    client_events, decisions, server_spans = split_transcript(_transcript())

    assert [event["seq"] for event in client_events] == [1, 2]
    assert [event["seq"] for event in decisions] == [5]
    assert {span["spanId"] for span in server_spans} == {"llm_server_1", "llm_server_2"}


def test_jsonl_spans_fill_only_the_server_spans_missing_from_the_transcript() -> None:
    _client, _decisions, from_transcript = split_transcript(_transcript())

    merged = merge_server_spans(
        from_transcript,
        [
            {"spanId": "llm_server_1", "title": "duplicate", "startedAt": "2026-11-11T10:00:02.000Z"},
            {"spanId": "llm_server_3", "title": "only in jsonl", "startedAt": "2026-11-11T10:00:03.000Z"},
        ],
    )

    assert [span["spanId"] for span in merged] == ["llm_server_1", "llm_server_2", "llm_server_3"]
    assert merged[0]["summary"] == "Server validation"


def test_build_entries_routes_client_and_server_rows_to_their_sources() -> None:
    client_events, decisions, server_spans = split_transcript(_transcript())
    document = TimelineDocument(
        client_events=tuple(client_events),
        decisions=tuple(decisions),
        server_spans=tuple(server_spans),
    )

    entries = build_entries(document)

    assert [entry.source for entry in entries] == [
        "client",
        "client",
        "client",
        "server",
        "server",
    ]
    assert {entry.id for entry in entries} == {
        "client:event:1",
        "client:event:2",
        "client:decision:5",
        "server:llm_server_1",
        "server:llm_server_2",
    }


def test_the_cache_reuses_an_unchanged_fingerprint() -> None:
    cache = TimelineCache(max_entries=100)
    builds = 0

    def build() -> TimelineDocument:
        nonlocal builds
        builds += 1
        return TimelineDocument(
            client_events=(
                {"seq": 1, "entryType": "event", "phase": "dispatch", "summary": "Run started"},
            )
        )

    first = cache.get("run-1", (1, 2), build)
    second = cache.get("run-1", (1, 2), build)

    assert builds == 1
    assert [entry.id for entry in first] == [entry.id for entry in second]


def test_the_cache_rebuilds_when_the_fingerprint_changes() -> None:
    cache = TimelineCache(max_entries=100)
    builds = 0

    def build() -> TimelineDocument:
        nonlocal builds
        builds += 1
        return TimelineDocument()

    cache.get("run-1", (1, 2), build)
    cache.get("run-1", (1, 3), build)

    assert builds == 2


def test_the_cache_evicts_the_least_recently_used_run_when_over_budget() -> None:
    cache = TimelineCache(max_entries=9)
    builds: dict[str, int] = {}

    def make(run_id: str):
        def build() -> TimelineDocument:
            builds[run_id] = builds.get(run_id, 0) + 1
            return TimelineDocument(
                client_events=tuple(
                    {
                        "seq": seq,
                        "entryType": "event",
                        "phase": "dispatch",
                        "summary": f"{run_id}-{seq}",
                    }
                    for seq in range(1, 5)
                )
            )

        return build

    cache.get("run-1", ("a",), make("run-1"))
    cache.get("run-2", ("b",), make("run-2"))
    cache.get("run-1", ("a",), make("run-1"))
    cache.get("run-3", ("c",), make("run-3"))

    assert builds == {"run-1": 1, "run-2": 1, "run-3": 1}
    cache.get("run-1", ("a",), make("run-1"))
    cache.get("run-2", ("b",), make("run-2"))

    assert builds["run-1"] == 1
    assert builds["run-2"] == 2


def test_process_logs_read_the_head_and_tail_windows(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    head = "2026-11-11T10:00:01.000Z INFO    ran_energy_saving.server [run_id=abc] head"
    tail = "2026-11-11T10:00:09.000Z INFO    ran_energy_saving.server [run_id=abc] tail"
    filler = "x" * 2048
    (run_dir / "server.log").write_text(f"{head}\n{filler}\n{tail}\n", encoding="utf-8")

    lines, warnings = read_process_logs(run_dir, "abc", window_bytes=256)

    assert [line.text for line in lines] == ["head", "tail"]
    assert any("server.log" in warning and "首尾" in warning for warning in warnings)


def test_process_logs_ignore_other_runs(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "server.log").write_text(
        "2026-11-11T10:00:01.000Z INFO    ran_energy_saving.server [run_id=other] other\n",
        encoding="utf-8",
    )

    lines, _warnings = read_process_logs(run_dir, "abc")

    assert lines == []


def test_server_trace_keeps_the_first_and_last_half_when_over_the_limit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "server-trace.jsonl"
    path.write_text(
        "\n".join(json.dumps({"seq": seq, "spanId": f"s{seq}"}) for seq in range(10)),
        encoding="utf-8",
    )

    spans, warnings = read_server_trace_jsonl(path, limit=6)

    assert [span["seq"] for span in spans] == [0, 1, 2, 7, 8, 9]
    assert warnings


def test_wire_records_are_capped_at_the_first_and_last_half() -> None:
    records = [{"index": index} for index in range(10)]

    capped, warning = cap_wire_records(records, limit=6)

    assert [record["index"] for record in capped] == [0, 1, 2, 7, 8, 9]
    assert warning is not None


def test_wire_refs_keep_metadata_without_the_body(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-wire"
    run_dir.mkdir(parents=True)
    (run_dir / "wire.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-11-11T10:00:01.000Z",
                "dir": "out",
                "method": "POST",
                "url": "http://nms.test/message:stream",
                "status": 200,
                "body": "x" * 5000,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    refs, warnings = read_wire_refs(tmp_path / "runs", "run-wire")

    assert warnings == []
    assert len(refs) == 1
    ref = refs[0]
    assert ref.file == "wire.jsonl"
    assert ref.line_no == 1
    assert ref.method == "POST"
    assert ref.url == "http://nms.test/message:stream"
    assert len(ref.search_prefix) <= 512
    assert "body" not in ref.search_prefix or "x" * 600 not in ref.search_prefix
