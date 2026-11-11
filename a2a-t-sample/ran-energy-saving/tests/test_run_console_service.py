"""Tests of the run-console service for live, UI-mode and process-log runs."""

from __future__ import annotations

import json
from pathlib import Path

from demo.web.runs_console.service import RunConsoleService
from protocol.client_store import ClientRunRecord, SqliteClientStore
from shared.run_constants import PROTOCOL_A2A_T


def _record(run_id: str, *, transcript_json: str | None = None, status: str = "finished") -> ClientRunRecord:
    return ClientRunRecord(
        run_id=run_id,
        conversation_id=f"conv-{run_id}",
        scenario="night",
        mode="auto",
        transport="sse",
        discovery="registry",
        protocol=PROTOCOL_A2A_T,
        user_request="save energy",
        status=status,
        trace_id="trace-" + run_id,
        chain_id="chain-" + run_id,
        transcript_json=transcript_json,
    )


async def _service(
    tmp_path: Path,
    record: ClientRunRecord,
    *,
    runs: object | None = None,
    run_dir: Path | None = None,
) -> RunConsoleService:
    db_path = tmp_path / "client.sqlite3"
    store = SqliteClientStore(db_path)
    await store.ensure_conversation(conversation_id=record.conversation_id or "")
    await store.create_run(record)
    return RunConsoleService(
        client_db_path=db_path,
        runs_dir=tmp_path / "runs",
        run_dir=run_dir,
        runs=runs,  # type: ignore[arg-type]
    )


async def test_ui_mode_server_spans_come_from_the_transcript_without_a_jsonl_file(
    tmp_path: Path,
) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    transcript = runs_dir / "transcript.json"
    transcript.write_text(
        json.dumps(
            {
                "events": [
                    {"seq": 1, "entryType": "event", "phase": "dispatch", "summary": "Run started"},
                    {
                        "seq": 2,
                        "entryType": "trace",
                        "spanId": "llm_server_1",
                        "side": "server",
                        "phase": "task_validation",
                        "summary": "Server validation",
                        "startedAt": "2026-11-11T10:00:02.000Z",
                    },
                ],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    service = await _service(tmp_path, _record("run-ui", transcript_json=str(transcript)))

    timeline = service.timeline("run-ui", sources=["server"])

    assert timeline is not None
    assert timeline["total"] == 1
    assert timeline["entries"][0]["id"] == "server:llm_server_1"


async def test_the_durable_server_trace_fills_a_transcript_without_server_rows(
    tmp_path: Path,
) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    transcript = runs_dir / "transcript.json"
    transcript.write_text(
        json.dumps(
            {
                "events": [
                    {"seq": 1, "entryType": "event", "phase": "dispatch", "summary": "Run started"},
                ],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    trace_dir = tmp_path / "runs" / "run-cli"
    trace_dir.mkdir(parents=True)
    (trace_dir / "server-trace.jsonl").write_text(
        json.dumps(
            {
                "seq": 2,
                "spanId": "srv-1",
                "phase": "task_validation",
                "startedAt": "2026-11-11T10:00:02.000Z",
                "title": "Server validation",
                "status": "ok",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    service = await _service(tmp_path, _record("run-cli", transcript_json=str(transcript)))

    timeline = service.timeline("run-cli", sources=["server"])

    assert timeline is not None
    assert timeline["total"] == 1
    assert timeline["entries"][0]["id"] == "server:srv-1"


async def test_a_live_run_uses_the_in_memory_transcript_and_spans(tmp_path: Path) -> None:
    class _Transcript:
        def to_dict(self) -> dict[str, object]:
            return {
                "events": [
                    {"seq": 1, "entryType": "event", "phase": "dispatch", "summary": "Run started"},
                ],
                "decisions": [],
            }

    class _LiveRun:
        status = "running"
        result = None
        error = None
        transcript = _Transcript()
        spans = {
            "srv-live": {
                "spanId": "srv-live",
                "phase": "negotiation",
                "startedAt": "2026-11-11T10:00:03.000Z",
                "title": "Live server span",
                "status": "ok",
            }
        }

    class _Runs:
        def get(self, run_id: str) -> object | None:
            return _LiveRun() if run_id == "run-live" else None

    service = await _service(
        tmp_path,
        _record("run-live", status="running"),
        runs=_Runs(),
    )

    timeline = service.timeline("run-live", sources=["server"])
    summary = service.summary("run-live")

    assert timeline is not None
    assert timeline["total"] == 1
    assert timeline["entries"][0]["id"] == "server:srv-live"
    assert summary is not None
    assert summary["run"]["status"] == "running"


async def test_process_logs_are_served_only_when_run_dir_is_configured(tmp_path: Path) -> None:
    run_dir = tmp_path / "launcher"
    run_dir.mkdir()
    (run_dir / "server.log").write_text(
        "2026-11-11T10:00:01.000Z INFO    ran_energy_saving.server [run_id=run-proc] boom\n",
        encoding="utf-8",
    )
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    transcript = runs_dir / "transcript.json"
    transcript.write_text(
        json.dumps({"events": [], "decisions": []}),
        encoding="utf-8",
    )
    service = await _service(
        tmp_path,
        _record("run-proc", transcript_json=str(transcript)),
        run_dir=run_dir,
    )

    timeline = service.timeline("run-proc", sources=["process"])
    files = service.files("run-proc")

    assert timeline is not None
    assert timeline["total"] == 1
    assert timeline["entries"][0]["source"] == "process"
    assert files is not None
    names = {item["name"] for item in files["files"]}
    assert "server.log" in names


async def test_entry_payload_reads_the_original_record(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    transcript = runs_dir / "transcript.json"
    transcript.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "seq": 1,
                        "entryType": "event",
                        "phase": "dispatch",
                        "summary": "Run started",
                        "detail": {"answer": 42},
                    },
                ],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    service = await _service(tmp_path, _record("run-payload", transcript_json=str(transcript)))
    timeline = service.timeline("run-payload")
    assert timeline is not None
    entry_id = timeline["entries"][0]["id"]

    payload = service.entry_payload("run-payload", entry_id)

    assert payload is not None
    assert '"answer": 42' in payload["payload"]
    assert payload["payloadTruncated"] is False


async def test_negotiation_returns_transcript_decisions(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    transcript = runs_dir / "transcript.json"
    transcript.write_text(
        json.dumps(
            {
                "events": [],
                "decisions": [
                    {
                        "seq": 4,
                        "entryType": "decision",
                        "phase": "negotiation",
                        "action": "accept",
                        "filledParams": {"rate": 2},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service = await _service(tmp_path, _record("run-decision", transcript_json=str(transcript)))

    payload = service.negotiation("run-decision")

    assert payload is not None
    assert payload["decisions"][0]["action"] == "accept"
    assert payload["decisions"][0]["filledParams"] == {"rate": 2}
