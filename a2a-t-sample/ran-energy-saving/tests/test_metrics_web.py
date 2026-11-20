"""Web API and page tests of the protocol comparison metrics."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import httpx
from demo.web.app import create_app
from protocol.client_store import SqliteClientStore
from shared.config import SampleConfig


def _metrics(run_id: str, *, protocol: str, scenario: str, tokens: int | None) -> dict[str, object]:
    return {
        "runId": run_id,
        "conversationId": "conv-1",
        "protocol": protocol,
        "scenario": scenario,
        "mode": "auto",
        "transport": "sse",
        "language": "zh-CN",
        "modelMode": "live" if tokens is not None else "offline",
        "modelLabel": "test",
        "status": "finished",
        "finalState": "task_completed",
        "startedAt": "2026-01-01T00:00:00.000Z",
        "finishedAt": "2026-01-01T00:00:10.000Z",
        "durationMs": 10000.0,
        "tokens": {
            "available": tokens is not None,
            "prompt": 100 if tokens is not None else None,
            "completion": 20 if tokens is not None else None,
            "total": tokens,
            "llmCalls": 2,
            "bySide": {},
            "byComponent": {},
        },
        "latency": {
            "totalMs": 10000.0,
            "modelMs": 2000.0,
            "toolMs": 100.0,
            "approvalWaitMs": 500.0,
            "otherMs": 7400.0,
        },
        "negotiation": {"rounds": 1, "expectedRounds": 1, "messages": 2},
        "accuracy": {"score": 1.0, "passed": True, "checks": {}},
        "report": {"cell_id": "cell-001"},
    }


async def test_metrics_api_and_comparison_page(
    offline_config: SampleConfig, tmp_path: Path
) -> None:
    db_path = tmp_path / "client.sqlite3"
    store = SqliteClientStore(db_path)
    await store.save_run_metrics(
        _metrics("run-a2at", protocol="a2a-t", scenario="information", tokens=120)
    )
    await store.save_run_metrics(
        _metrics("run-pure", protocol="pure", scenario="information", tokens=60)
    )
    config = replace(offline_config, client_db_path=str(db_path))
    app = create_app(
        config,
        runs_dir=tmp_path,
        client_store=store,
        finished_grace_seconds=0.0,
        metrics_grace_seconds=0.0,
        ui_mode="presentation",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        runs = await client.get("/api/metrics/runs")
        assert runs.status_code == 200
        payload = runs.json()
        assert payload["enabled"] is True
        assert {row["runId"] for row in payload["runs"]} == {"run-a2at", "run-pure"}

        filtered = await client.get("/api/metrics/runs", params={"protocol": "pure"})
        assert [row["runId"] for row in filtered.json()["runs"]] == ["run-pure"]

        summary = await client.get("/api/metrics/summary")
        assert summary.status_code == 200
        body = summary.json()
        assert body["runs"] == 2
        groups = {(row["protocol"], row["scenario"]): row for row in body["groups"]}
        assert groups[("pure", "information")]["tokens"]["total"]["avg"] == 60
        protocols = {row["protocol"]: row for row in body["protocols"]}
        assert protocols["a2a-t"]["runs"] == 1

        detail = await client.get("/api/runs/run-pure/metrics")
        assert detail.status_code == 200
        assert detail.json()["tokens"]["total"] == 60
        assert detail.json()["traceFinal"] is True

        missing = await client.get("/api/runs/unknown/metrics")
        assert missing.status_code == 404

        csv_response = await client.get("/api/metrics/export.csv")
        assert csv_response.status_code == 200
        assert "runId,protocol,scenario" in csv_response.text
        assert "run-pure" in csv_response.text

        page = await client.get("/comparison")
        assert page.status_code == 200
        assert "协议对比" in page.text
