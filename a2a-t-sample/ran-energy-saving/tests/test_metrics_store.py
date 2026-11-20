"""Persistence and aggregation tests of the per-run comparison metrics."""

from __future__ import annotations

from pathlib import Path

from protocol.client_store import SqliteClientStore, read_run_metrics, read_run_metrics_rows
from protocol.metrics_summary import summarize_metrics, summarize_protocols


def _metrics(
    run_id: str,
    *,
    protocol: str,
    scenario: str,
    total_tokens: int | None,
    duration_ms: float,
    passed: bool,
) -> dict[str, object]:
    return {
        "runId": run_id,
        "conversationId": "conv-1",
        "protocol": protocol,
        "scenario": scenario,
        "mode": "auto",
        "transport": "sse",
        "language": "zh-CN",
        "modelMode": "live",
        "modelLabel": "test",
        "status": "finished",
        "finalState": "task_completed",
        "startedAt": "2026-01-01T00:00:00.000Z",
        "finishedAt": "2026-01-01T00:00:10.000Z",
        "durationMs": duration_ms,
        "tokens": {
            "available": total_tokens is not None,
            "prompt": 100 if total_tokens is not None else None,
            "completion": 20 if total_tokens is not None else None,
            "total": total_tokens,
            "llmCalls": 2,
            "bySide": {},
            "byComponent": {},
        },
        "latency": {
            "totalMs": duration_ms,
            "modelMs": 1000.0,
            "toolMs": 100.0,
            "approvalWaitMs": 300.0,
            "otherMs": duration_ms - 1400.0,
        },
        "negotiation": {"rounds": 1, "expectedRounds": 1, "messages": 2},
        "accuracy": {
            "score": 1.0 if passed else 0.5,
            "passed": passed,
            "checks": {},
        },
        "report": {"cell_id": "cell-001"},
    }


async def test_metrics_are_upserted_and_filterable(tmp_path: Path) -> None:
    path = tmp_path / "client.sqlite3"
    store = SqliteClientStore(path)
    await store.save_run_metrics(
        _metrics("run-a", protocol="a2a-t", scenario="information", total_tokens=120, duration_ms=9000, passed=True)
    )
    await store.save_run_metrics(
        _metrics("run-b", protocol="pure", scenario="information", total_tokens=60, duration_ms=5000, passed=True)
    )
    # A delayed second pass replaces the same row instead of duplicating it.
    await store.save_run_metrics(
        _metrics("run-a", protocol="a2a-t", scenario="information", total_tokens=130, duration_ms=9500, passed=True)
    )

    row = read_run_metrics(path, "run-a")
    assert row is not None
    assert row["tokens"]["total"] == 130
    assert row["durationMs"] == 9500

    all_rows = read_run_metrics_rows(path)
    assert len(all_rows) == 2
    pure_rows = read_run_metrics_rows(path, protocol="pure")
    assert [row["runId"] for row in pure_rows] == ["run-b"]


def test_summary_groups_by_protocol_and_scenario() -> None:
    rows = [
        _metrics("run-1", protocol="a2a-t", scenario="information", total_tokens=120, duration_ms=9000, passed=True),
        _metrics("run-2", protocol="a2a-t", scenario="information", total_tokens=80, duration_ms=7000, passed=False),
        _metrics("run-3", protocol="pure", scenario="information", total_tokens=60, duration_ms=5000, passed=True),
    ]
    groups = summarize_metrics(rows)
    by_key = {(row["protocol"], row["scenario"]): row for row in groups}
    a2at = by_key[("a2a-t", "information")]
    assert a2at["runs"] == 2
    assert a2at["successRate"] == 0.5
    assert a2at["tokens"]["total"]["avg"] == 100
    assert a2at["latency"]["totalMs"]["avg"] == 8000

    protocols = summarize_protocols(rows)
    by_protocol = {row["protocol"]: row for row in protocols}
    assert by_protocol["pure"]["runs"] == 1
    assert by_protocol["pure"]["tokens"]["total"]["avg"] == 60
