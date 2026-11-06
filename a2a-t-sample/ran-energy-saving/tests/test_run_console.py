"""Tests of the internal run console and the request-origin record."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from demo.web.app import create_app
from protocol.client_flow import ClientFlowResult
from protocol.client_store import SqliteClientStore
from shared.config import SampleConfig
from shared.run_constants import FINAL_STATE_TASK_COMPLETED, PROTOCOL_A2A_T


class _PassRuntime:
    """Runtime double that completes every request without a negotiation."""

    async def run(
        self,
        *,
        event_sink: Callable[[dict[str, object]], None] | None = None,
        trace_sink: Callable[[dict[str, object]], None] | None = None,
        run_id: str | None = None,
        user_request: str | None = None,
        conversation_context: str | None = None,
        on_decision_requested: Callable[..., None] | None = None,
        on_decision_made: Callable[..., None] | None = None,
        on_intent_ready: Callable[[str], Awaitable[bool]] | None = None,
        validate_before_send: bool = False,
        run_mode: str = "create",
        target_task_id: str | None = None,
        target_context_id: str | None = None,
    ) -> ClientFlowResult:
        del trace_sink, run_id, user_request, conversation_context, validate_before_send
        del run_mode, target_task_id, target_context_id
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
