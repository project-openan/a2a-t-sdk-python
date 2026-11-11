"""Tests of the internal run console and the request-origin record."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from demo.web.app import create_app
from protocol.client_flow import ClientFlowResult
from protocol.client_store import ClientRunRecord, SqliteClientStore
from shared.config import SampleConfig
from shared.run_constants import FINAL_STATE_TASK_COMPLETED, PROTOCOL_A2A_T, PROTOCOL_PURE


class _PassRuntime:
    """Runtime double that completes every request without a negotiation."""

    async def run(
        self,
        *,
        event_sink: Callable[[dict[str, object]], None] | None = None,
        trace_sink: Callable[[dict[str, object]], None] | None = None,
        activity_sink: Callable[[dict[str, object]], None] | None = None,
        run_id: str | None = None,
        user_request: str | None = None,
        conversation_context: str | None = None,
        on_decision_requested: Callable[..., None] | None = None,
        on_decision_made: Callable[..., None] | None = None,
        on_intent_ready: Callable[[str], Awaitable[bool]] | None = None,
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
        del trace_sink, activity_sink, run_id, user_request, conversation_context
        del validate_before_send, enforce_required_fields, require_rate_target, site_level_rate_optional
        del run_mode, target_task_id, target_context_id
        del target_server_task_id, target_server_context_id
        del on_decision_requested, on_decision_made, on_intent_ready
        if event_sink is not None:
            event_sink({"kind": "status", "task_id": "task-1", "context_id": "ctx-1"})
        return ClientFlowResult(final_state=FINAL_STATE_TASK_COMPLETED)

    async def aclose(self) -> None:
        """Release the runtime."""


def _runtime_factory() -> Callable[..., Any]:
    async def build(*args: object, **kwargs: object) -> _PassRuntime:
        del args, kwargs
        return _PassRuntime()

    return build


@pytest.fixture
async def console_client(
    offline_config: SampleConfig, tmp_path: Path
) -> AsyncIterator[tuple[httpx.AsyncClient, SqliteClientStore]]:
    """Bind an HTTP client to an app whose client store lives in the temp directory."""
    db_path = tmp_path / "client.sqlite3"
    store = SqliteClientStore(db_path)
    config = replace(offline_config, client_db_path=str(db_path))
    app = create_app(
        config,
        runs_dir=tmp_path,
        client_store=store,
        runtime_factory=_runtime_factory(),
        finished_grace_seconds=0.0,
        metrics_grace_seconds=0.0,
        protocols=(PROTOCOL_A2A_T,),
        nms_enabled=False,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, store


async def _wait_finished(client: httpx.AsyncClient, run_id: str) -> dict[str, object]:
    """Poll one run until its persisted status leaves ``running``."""
    for _ in range(200):
        response = await client.get(f"/api/client/runs/{run_id}")
        if response.status_code == 200:
            detail = response.json()
            if detail.get("status") != "running":
                return detail
        await asyncio.sleep(0.02)
    raise AssertionError(f"run {run_id} did not finish")


async def test_the_console_page_and_assets_are_served(
    console_client: tuple[httpx.AsyncClient, SqliteClientStore],
) -> None:
    client, _store = console_client

    page = await client.get("/runs")
    assert page.status_code == 200
    assert "Run 控制台" in page.text
    assert "请求来源" in page.text

    css = await client.get("/runs/app.css")
    script = await client.get("/runs/main.js")
    assert css.status_code == 200
    assert script.status_code == 200

    meta = await client.get("/api/run-console/meta")
    assert meta.status_code == 200
    payload = meta.json()
    assert payload["enabled"] is True
    assert "presentation" in payload["sourceKinds"]
    assert "tester" in payload["sourceKinds"]
    assert PROTOCOL_A2A_T in payload["protocols"]


async def test_a_page_run_records_its_request_origin_and_appears_in_the_console(
    console_client: tuple[httpx.AsyncClient, SqliteClientStore],
) -> None:
    client, _store = console_client
    started = await client.post(
        "/api/runs",
        json={
            "scenario": "night",
            "mode": "auto",
            "protocol": PROTOCOL_A2A_T,
            "userRequest": "在区域 A 开启节能",
            "source": "presentation",
        },
        headers={
            "user-agent": "unit-test/1.0",
            "referer": "https://ui.example/?token=secret&brand=huawei",
            "x-forwarded-for": "10.0.0.7, 10.0.0.8",
            "x-request-id": "req-123",
        },
    )
    assert started.status_code in (200, 202)
    run_id = started.json()["runId"]
    await _wait_finished(client, run_id)

    listing = await client.get("/api/run-console/runs")
    assert listing.status_code == 200
    rows = listing.json()["runs"]
    row = next(candidate for candidate in rows if candidate["runId"] == run_id)
    assert row["sourceKind"] == "presentation"
    assert row["sourceChannel"] == "presentation-page"
    assert row["protocol"] == PROTOCOL_A2A_T
    assert row["scenario"] == "night"

    filtered = await client.get("/api/run-console/runs?source=tester")
    assert filtered.status_code == 200
    assert filtered.json()["runs"] == []

    detail_response = await client.get(f"/api/run-console/runs/{run_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    source = detail["source"]
    assert source["kind"] == "presentation"
    assert source["channel"] == "presentation-page"
    assert source["forwardedFor"] == "10.0.0.7, 10.0.0.8"
    assert source["requestId"] == "req-123"
    assert source["entryPath"] == "/api/runs"
    assert source["userAgent"] == "unit-test/1.0"
    # A shared ?token=… link must not be persisted in the referer.
    assert "token" not in source["referer"]
    assert "brand=huawei" in source["referer"]
    assert detail["transcript"]["source"]["kind"] == "presentation"
    assert detail["wire"] == []

    transcript_json = await client.get(
        f"/api/run-console/runs/{run_id}/transcript.json"
    )
    transcript_md = await client.get(f"/api/run-console/runs/{run_id}/transcript.md")
    assert transcript_json.status_code == 200
    assert transcript_json.json()["source"]["kind"] == "presentation"
    assert transcript_md.status_code == 200
    assert "Source: `presentation`" in transcript_md.text


async def test_the_console_searches_the_request_and_run_id(
    console_client: tuple[httpx.AsyncClient, SqliteClientStore],
) -> None:
    client, _store = console_client
    started = await client.post(
        "/api/runs",
        json={
            "scenario": "night",
            "mode": "auto",
            "protocol": PROTOCOL_A2A_T,
            "userRequest": "唯一搜索词-A1",
            "source": "explorer",
        },
    )
    run_id = started.json()["runId"]
    await _wait_finished(client, run_id)

    by_request = await client.get("/api/run-console/runs?query=唯一搜索词-A1")
    assert [row["runId"] for row in by_request.json()["runs"]] == [run_id]

    by_id = await client.get(f"/api/run-console/runs?query={run_id}")
    assert [row["runId"] for row in by_id.json()["runs"]] == [run_id]

    missing = await client.get("/api/run-console/runs?query=no-such-request")
    assert missing.json()["runs"] == []
    assert missing.json()["total"] == 0


async def test_the_console_exposes_the_downstream_task_of_a_two_hop_run(
    console_client: tuple[httpx.AsyncClient, SqliteClientStore],
) -> None:
    """A tester can find the run by the NMS context, not only by the OSS one."""
    client, store = console_client
    await store.ensure_conversation(conversation_id="conv-down")
    await store.create_run(
        ClientRunRecord(
            run_id="run-down",
            conversation_id="conv-down",
            scenario="night",
            mode="auto",
            transport="webhook",
            discovery="nms",
            protocol=PROTOCOL_PURE,
            user_request="save energy",
            status="running",
            server_task_id="outer-task",
            server_context_id="outer-ctx",
        )
    )
    await store.update_run(
        "run-down",
        status="finished",
        downstream_task_id="nms-task",
        downstream_context_id="nms-ctx",
        downstream_hop="nms",
    )

    listing = await client.get("/api/run-console/runs?query=nms-ctx")
    rows = listing.json()["runs"]
    assert [row["runId"] for row in rows] == ["run-down"]
    assert rows[0]["downstreamTaskId"] == "nms-task"
    assert rows[0]["downstreamContextId"] == "nms-ctx"
    assert rows[0]["downstreamHop"] == "nms"

    detail = (await client.get("/api/run-console/runs/run-down")).json()
    assert detail["protocol"] == PROTOCOL_PURE
    assert detail["serverContextId"] == "outer-ctx"
    assert detail["downstreamContextId"] == "nms-ctx"
    assert detail["downstreamTaskId"] == "nms-task"


async def _start_finished_page_run(client: httpx.AsyncClient) -> str:
    """Start one page run and wait until its transcript is persisted."""
    started = await client.post(
        "/api/runs",
        json={
            "scenario": "night",
            "mode": "auto",
            "protocol": PROTOCOL_A2A_T,
            "userRequest": "timeline API run",
            "source": "presentation",
        },
    )
    assert started.status_code in (200, 202)
    run_id = started.json()["runId"]
    await _wait_finished(client, run_id)
    return run_id


async def test_the_detail_endpoints_serve_summary_timeline_negotiation_and_files(
    console_client: tuple[httpx.AsyncClient, SqliteClientStore],
) -> None:
    client, _store = console_client
    run_id = await _start_finished_page_run(client)

    summary = await client.get(f"/api/run-console/runs/{run_id}/summary")
    assert summary.status_code == 200
    summary_payload = summary.json()
    assert summary_payload["run"]["runId"] == run_id
    assert "traceId" in summary_payload["run"]
    assert "chainId" in summary_payload["run"]
    assert summary_payload["counts"]["bySource"]["client"] >= 1
    assert "metrics" in summary_payload
    assert summary_payload["processLogsEnabled"] is False

    timeline = await client.get(f"/api/run-console/runs/{run_id}/timeline?limit=2")
    assert timeline.status_code == 200
    timeline_payload = timeline.json()
    assert timeline_payload["total"] >= 1
    assert len(timeline_payload["entries"]) <= 2
    entry = timeline_payload["entries"][0]
    assert {"id", "source", "phase", "title", "payloadAvailable"} <= set(entry)
    assert "payload" not in entry

    one_entry = await client.get(
        f"/api/run-console/runs/{run_id}/timeline/{entry['id']}"
    )
    assert one_entry.status_code == 200
    assert "payload" in one_entry.json()

    negotiation = await client.get(f"/api/run-console/runs/{run_id}/negotiation")
    assert negotiation.status_code == 200
    assert "turns" in negotiation.json()
    assert "negotiationEvents" in negotiation.json()

    files = await client.get(f"/api/run-console/runs/{run_id}/files")
    assert files.status_code == 200
    by_name = {item["name"]: item for item in files.json()["files"]}
    assert by_name["transcript.json"]["available"] is True
    assert by_name["metrics.json"]["available"] is True

    transcript = await client.get(
        f"/api/run-console/runs/{run_id}/files/transcript.json"
    )
    assert transcript.status_code == 200
    assert '"scenario"' in transcript.text


async def test_the_timeline_filters_by_source_and_keyword(
    console_client: tuple[httpx.AsyncClient, SqliteClientStore],
) -> None:
    client, _store = console_client
    run_id = await _start_finished_page_run(client)

    clients_only = await client.get(
        f"/api/run-console/runs/{run_id}/timeline?source=client"
    )
    assert clients_only.status_code == 200
    assert clients_only.json()["total"] >= 1
    assert {entry["source"] for entry in clients_only.json()["entries"]} == {"client"}

    wires_only = await client.get(
        f"/api/run-console/runs/{run_id}/timeline?source=wire"
    )
    assert wires_only.status_code == 200
    assert wires_only.json()["total"] == 0

    missing = await client.get(
        f"/api/run-console/runs/{run_id}/timeline?q=no-such-log-text"
    )
    assert missing.status_code == 200
    assert missing.json()["total"] == 0


async def test_unknown_runs_and_unsafe_file_names_are_rejected(
    console_client: tuple[httpx.AsyncClient, SqliteClientStore],
) -> None:
    client, _store = console_client

    for suffix in ("summary", "timeline", "negotiation", "files"):
        response = await client.get(f"/api/run-console/runs/no-such-run/{suffix}")
        assert response.status_code == 404, suffix

    unsafe = await client.get("/api/run-console/runs/no-such-run/files/..%2F.env")
    assert unsafe.status_code in (400, 404)


def test_the_run_console_js_helpers_pass_node() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    harness = Path(__file__).resolve().parent / "js" / "run_console.test.mjs"
    completed = subprocess.run(
        [node, str(harness)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
