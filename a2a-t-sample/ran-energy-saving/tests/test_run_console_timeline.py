"""Tests of the run-console timeline normalization, filtering and pagination."""

from __future__ import annotations

from demo.web.runs_console.timeline import (
    TimelineDocument,
    assign_inferred_phases,
    build_entries,
    filter_entries,
    page_entries,
    parse_process_log,
)


def _client_event(seq: int, phase: str | None, summary: str, **extra: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "seq": seq,
        "entryType": "event",
        "phase": phase,
        "kind": extra.pop("kind", "status"),
        "summary": summary,
        "status": "ok",
        "ts": extra.pop("ts", f"2026-11-11T10:00:{seq:02d}.000Z"),
    }
    entry.update(extra)
    return entry


def test_client_events_and_decisions_are_normalized_in_timeline_order() -> None:
    document = TimelineDocument(
        client_events=(
            _client_event(2, "negotiation", "PROPOSE received", performative="PROPOSE"),
            _client_event(1, "dispatch", "Run started"),
        ),
        decisions=(
            {
                "seq": 3,
                "entryType": "decision",
                "phase": "negotiation",
                "kind": "accept",
                "summary": "Accept the proposal",
                "status": "ok",
                "ts": "2026-11-11T10:00:03.000Z",
                "action": "accept",
            },
        ),
    )

    entries = build_entries(document)

    assert [entry.id for entry in entries] == [
        "client:event:1",
        "client:event:2",
        "client:decision:3",
    ]
    assert entries[0].source == "client"
    assert entries[0].phase == "dispatch"
    assert entries[0].phase_inferred is False
    assert entries[1].title == "PROPOSE received"
    assert entries[2].title == "Accept the proposal"
    assert entries[2].payload_available is True


def test_wire_and_process_entries_infer_the_nearest_preceding_phase() -> None:
    document = TimelineDocument(
        client_events=(
            _client_event(1, "dispatch", "Run started", ts="2026-11-11T10:00:01.000Z"),
            _client_event(2, "negotiation", "PROPOSE received", ts="2026-11-11T10:00:05.000Z"),
        ),
        wire=(
            {
                "ts": "2026-11-11T10:00:03.000Z",
                "dir": "out",
                "method": "POST",
                "url": "http://nms.test/message:stream",
                "status": 200,
            },
            {
                "ts": "2026-11-11T10:00:02.000Z",
                "dir": "in",
                "method": "GET",
                "url": "http://nms.test/tasks/task-1",
                "status": 200,
            },
        ),
        process=(
            {
                "file": "server",
                "line_no": 7,
                "ts": "2026-11-11T10:00:04.000Z",
                "level": "INFO",
                "text": "server handled the proposal",
                "detail": "",
            },
        ),
    )

    entries = build_entries(document)

    inferred = [entry for entry in entries if entry.source in {"wire", "process"}]
    assert [entry.phase for entry in inferred] == ["dispatch", "dispatch", "dispatch"]
    assert all(entry.phase_inferred is True for entry in inferred)
    # The merged view stays in timestamp order even though wire input was unsorted.
    assert [entry.ts for entry in entries] == sorted(entry.ts or "" for entry in entries)


def test_entries_without_a_phase_anchor_stay_unknown() -> None:
    document = TimelineDocument(
        wire=(
            {
                "ts": "2026-11-11T10:00:03.000Z",
                "dir": "out",
                "method": "POST",
                "url": "http://nms.test/message:stream",
            },
        ),
    )

    entries = build_entries(document)

    assert len(entries) == 1
    assert entries[0].phase == "unknown"
    assert entries[0].phase_inferred is True


def test_filter_entries_matches_sources_phase_and_keyword() -> None:
    document = TimelineDocument(
        client_events=(
            _client_event(1, "dispatch", "Run started"),
            _client_event(2, "negotiation", "PROPOSE received"),
        ),
        wire=(
            {
                "ts": "2026-11-11T10:00:03.000Z",
                "dir": "out",
                "method": "POST",
                "url": "http://nms.test/message:stream",
            },
        ),
    )
    entries = assign_inferred_phases(build_entries(document))

    only_wire = filter_entries(entries, sources={"wire"})
    assert [entry.source for entry in only_wire] == ["wire"]

    only_negotiation = filter_entries(entries, phase="negotiation")
    assert [entry.id for entry in only_negotiation] == ["client:event:2", "wire:0"]

    by_keyword = filter_entries(entries, query="MESSAGE:STREAM")
    assert [entry.source for entry in by_keyword] == ["wire"]

    nothing = filter_entries(entries, query="no-such-text")
    assert nothing == []


def test_page_entries_reports_next_offset_and_end() -> None:
    document = TimelineDocument(
        client_events=tuple(
            _client_event(seq, "dispatch", f"event {seq}") for seq in range(1, 6)
        ),
    )
    entries = build_entries(document)

    first, next_offset = page_entries(entries, offset=0, limit=2)
    assert [entry.id for entry in first] == ["client:event:1", "client:event:2"]
    assert next_offset == 2

    last, next_offset = page_entries(entries, offset=4, limit=2)
    assert [entry.id for entry in last] == ["client:event:5"]
    assert next_offset is None


def test_process_text_line_matches_run_id_and_keeps_traceback() -> None:
    text = "\n".join(
        (
            "2026-11-11T10:00:01.000Z INFO    ran_energy_saving.client_service "
            "[run_id=abc123 conversation_id=conv-1] run started",
            "Traceback (most recent call last):",
            '  File "x.py", line 1, in <module>',
            "RuntimeError: boom",
            "2026-11-11T10:00:02.000Z INFO    ran_energy_saving.other "
            "[run_id=other] another run",
        )
    )

    lines = parse_process_log(text, file="server", run_id="abc123")

    assert len(lines) == 1
    assert lines[0].ts == "2026-11-11T10:00:01.000Z"
    assert lines[0].level == "INFO"
    assert lines[0].text.endswith("run started")
    assert "RuntimeError: boom" in lines[0].detail


def test_process_json_line_matches_run_id() -> None:
    text = "\n".join(
        (
            '{"ts": "2026-11-11T10:00:01.000Z", "level": "WARNING", '
            '"logger": "ran_energy_saving.client", "message": "slow call", "runId": "abc123"}',
            '{"ts": "2026-11-11T10:00:02.000Z", "level": "INFO", '
            '"logger": "ran_energy_saving.client", "message": "other", "runId": "other"}',
        )
    )

    lines = parse_process_log(text, file="server", run_id="abc123")

    assert len(lines) == 1
    assert lines[0].level == "WARNING"
    assert lines[0].text == "slow call"


def test_process_line_without_run_id_is_dropped() -> None:
    text = "2026-11-11T10:00:01.000Z INFO    ran_energy_saving.server no correlation here"

    lines = parse_process_log(text, file="server", run_id="abc123")

    assert lines == []
