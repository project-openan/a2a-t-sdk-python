"""Contract tests of the Excel-driven tester console."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import pytest
from demo.scenario import PURE_PROTOCOL_SCENARIOS, SUPPORTED_SCENARIO_NAMES
from demo.web.app import create_app
from demo.web.tester.excel import parse_test_cases, write_template
from openpyxl import Workbook, load_workbook
from protocol.client_flow import ClientFlowResult
from shared.config import MODEL_MODE_LIVE, SampleConfig
from shared.run_constants import (
    FINAL_STATE_TASK_COMPLETED,
    PROTOCOL_A2A_T,
    PROTOCOL_PURE,
)


class _PassRuntime:
    """Runtime double that completes every request without a negotiation."""

    def __init__(self, *, emit_tokens: bool = False) -> None:
        self.user_request: str | None = None
        self.emit_tokens = emit_tokens

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
    ) -> ClientFlowResult:
        del run_id, conversation_context
        del on_decision_requested, on_decision_made, on_intent_ready
        self.user_request = user_request
        if self.emit_tokens and trace_sink is not None:
            trace_sink(
                {
                    "kind": "llm",
                    "side": "client",
                    "phase": "decision",
                    "title": "Decision model call",
                    "tokens": {"prompt": 100, "completion": 50, "total": 150},
                }
            )
        if event_sink is not None:
            event_sink(
                {
                    "kind": "status",
                    "task_id": "task-1",
                    "context_id": "ctx-1",
                    "state": "TASK_STATE_COMPLETED",
                }
            )
        return ClientFlowResult(final_state=FINAL_STATE_TASK_COMPLETED)

    async def aclose(self) -> None:
        """Release the runtime."""


@pytest.fixture
def runtime_factory() -> Callable[..., Any]:
    """Build a pass-through runtime factory and keep the created runtimes."""

    async def build(*args: object, **kwargs: object) -> _PassRuntime:
        del args, kwargs
        return _PassRuntime()

    return build


@pytest.fixture
async def client(
    offline_config: SampleConfig,
    runtime_factory: Callable[..., Any],
    tmp_path: Path,
) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client bound to an app that advertises the two request forms."""
    app = create_app(
        offline_config,
        runs_dir=tmp_path,
        runtime_factory=runtime_factory,
        finished_grace_seconds=0.0,
        protocols=(PROTOCOL_A2A_T, PROTOCOL_PURE),
        nms_enabled=False,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


def _workbook_bytes(rows: list[list[object]]) -> bytes:
    """Build a minimal workbook with the template header."""
    template = load_workbook(BytesIO(write_template()), read_only=True)
    headers = [cell.value for cell in next(template["测试用例"].iter_rows(max_row=1))]
    template.close()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_the_template_parses_and_keeps_the_scenario_optional() -> None:
    cases = parse_test_cases(
        write_template(),
        protocols=(PROTOCOL_A2A_T, PROTOCOL_PURE),
        scenarios_by_protocol={
            PROTOCOL_A2A_T: SUPPORTED_SCENARIO_NAMES,
            PROTOCOL_PURE: PURE_PROTOCOL_SCENARIOS,
        },
        default_language="zh-CN",
    )

    assert [case.case_id for case in cases] == ["TC-001", "TC-002", "TC-003"]
    assert cases[0].scenario_explicit is True
    assert cases[0].expected_kind == "none"
    assert cases[0].expected_rounds == 0
    assert cases[2].scenario_explicit is False
    assert cases[2].expected_kind == ""
    assert cases[2].expected_rounds is None
    assert cases[2].user_request
    assert all(case.approval == "approve" for case in cases)
    assert all(not case.errors for case in cases)


def test_an_unknown_protocol_is_reported_per_row() -> None:
    data = _workbook_bytes(
        [
            ["TC-BAD", "bad", "Y", "SMTP", "night", "zh", "", "task_completed", "", "", "auto", 300, ""],
            ["TC-OK", "ok", "Y", "A2A-T", "night", "zh", "", "task_completed", "none", 0, "auto", 300, ""],
        ]
    )

    cases = parse_test_cases(
        data,
        protocols=(PROTOCOL_A2A_T, PROTOCOL_PURE),
        scenarios_by_protocol={
            PROTOCOL_A2A_T: SUPPORTED_SCENARIO_NAMES,
            PROTOCOL_PURE: PURE_PROTOCOL_SCENARIOS,
        },
        default_language="zh-CN",
    )

    assert cases[0].errors
    assert cases[0].runnable is False
    assert cases[1].runnable is True


async def test_the_tester_page_and_assets_are_served(client: httpx.AsyncClient) -> None:
    page = await client.get("/tester")
    assert page.status_code == 200
    assert 'id="dropzone"' in page.text
    assert "/tester/main.js" in page.text

    assert (await client.get("/tester/app.css")).status_code == 200
    assert (await client.get("/tester/main.js")).status_code == 200

    meta = await client.get("/api/tester/meta")
    assert meta.status_code == 200
    assert meta.json()["protocols"] == [PROTOCOL_A2A_T, PROTOCOL_PURE]
    assert meta.json()["defaultScenario"] == "night"
    # The console is an internal Chinese-team tool even when the process language is English.
    assert meta.json()["defaultLanguage"] == "zh"


async def test_importing_a_workbook_creates_a_draft_batch(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/tester/import",
        content=write_template(),
        headers={"X-File-Name": "night%20cases.xlsx"},
    )

    assert response.status_code == 201
    batch = response.json()
    assert batch["status"] == "draft"
    assert batch["sourceName"] == "night cases.xlsx"
    assert batch["summary"]["total"] == 3
    assert batch["summary"]["runnable"] == 3
    assert [case["caseId"] for case in batch["cases"]] == ["TC-001", "TC-002", "TC-003"]


async def test_an_invalid_upload_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/tester/import", content=b"not a workbook")

    assert response.status_code == 400
    assert response.json()["code"] == "workbook_invalid"


async def _wait_batch(
    client: httpx.AsyncClient,
    batch_id: str,
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = await client.get(f"/api/tester/batches/{batch_id}")
        assert response.status_code == 200
        latest = response.json()
        if latest["status"] != "running":
            return latest
        await asyncio.sleep(0.02)
    raise AssertionError(f"batch did not finish: {latest}")


async def test_a_batch_runs_every_case_and_reports_the_verdicts(
    client: httpx.AsyncClient,
) -> None:
    imported = await client.post("/api/tester/import", content=write_template())
    batch = imported.json()

    started = await client.post(f"/api/tester/batches/{batch['batchId']}/start")
    assert started.status_code == 200

    finished = await _wait_batch(client, batch["batchId"])
    assert finished["status"] == "finished"
    assert finished["summary"]["passed"] == 3
    assert all(result["runId"] for result in finished["results"])
    assert all(result["finalState"] == FINAL_STATE_TASK_COMPLETED for result in finished["results"])
    assert all(result["actualKind"] == "none" for result in finished["results"])
    for result in finished["results"]:
        assert isinstance(result["durationMs"], int)
        assert "tokens" in result["metrics"]
        assert "latency" in result["metrics"]
        assert result["metrics"]["tokens"]["scripted"] is True
        assert result["metrics"]["latency"]["totalMs"] is not None
    assert finished["summary"]["durationMs"] > 0


async def test_invalid_rows_are_listed_but_not_executed(client: httpx.AsyncClient) -> None:
    data = _workbook_bytes(
        [
            ["TC-BAD", "bad", "Y", "SMTP", "night", "zh", "", "task_completed", "", "", "auto", 300, ""],
            ["TC-OK", "ok", "Y", "A2A-T", "night", "zh", "", "task_completed", "none", 0, "auto", 300, ""],
        ]
    )
    imported = await client.post("/api/tester/import", content=data)
    batch = imported.json()
    assert batch["summary"]["invalid"] == 1
    assert batch["summary"]["runnable"] == 1

    await client.post(f"/api/tester/batches/{batch['batchId']}/start")
    finished = await _wait_batch(client, batch["batchId"])

    assert finished["results"][0]["status"] == "invalid"
    assert finished["results"][1]["status"] == "passed"
    assert finished["summary"]["invalid"] == 1
    assert finished["summary"]["passed"] == 1


async def test_retry_rebuilds_only_the_failed_cases(client: httpx.AsyncClient) -> None:
    data = _workbook_bytes(
        [
            ["TC-FAIL", "fail", "Y", "A2A-T", "night", "zh", "", "task_rejected", "none", 0, "approve", 300, ""],
            ["TC-PASS", "pass", "Y", "A2A-T", "night", "zh", "", "task_completed", "none", 0, "approve", 300, ""],
        ]
    )
    imported = await client.post("/api/tester/import", content=data)
    batch = imported.json()
    await client.post(f"/api/tester/batches/{batch['batchId']}/start")
    finished = await _wait_batch(client, batch["batchId"])
    assert finished["summary"]["failed"] == 1
    assert finished["summary"]["passed"] == 1

    retried = await client.post(f"/api/tester/batches/{batch['batchId']}/retry")
    assert retried.status_code == 201
    retry_batch = retried.json()
    assert retry_batch["status"] == "draft"
    assert [case["caseId"] for case in retry_batch["cases"]] == ["TC-FAIL"]

    await client.post(f"/api/tester/batches/{retry_batch['batchId']}/start")
    rerun = await _wait_batch(client, retry_batch["batchId"])
    assert rerun["summary"]["failed"] == 1


async def test_a_batch_is_still_listed_after_a_ui_restart(
    client: httpx.AsyncClient,
    offline_config: SampleConfig,
    runtime_factory: Callable[..., Any],
    tmp_path: Path,
) -> None:
    imported = await client.post("/api/tester/import", content=write_template())
    batch = imported.json()

    app = create_app(
        offline_config,
        runs_dir=tmp_path,
        runtime_factory=runtime_factory,
        finished_grace_seconds=0.0,
        protocols=(PROTOCOL_A2A_T, PROTOCOL_PURE),
        nms_enabled=False,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as other:
        restored = await other.get(f"/api/tester/batches/{batch['batchId']}")
        assert restored.status_code == 200
        assert restored.json()["status"] == "draft"
        assert [case["caseId"] for case in restored.json()["cases"]] == [
            "TC-001",
            "TC-002",
            "TC-003",
        ]

        listing = await other.get("/api/tester/batches")
        assert listing.status_code == 200
        assert batch["batchId"] in [item["batchId"] for item in listing.json()["batches"]]


async def test_nms_mode_is_exposed_to_the_page(
    offline_config: SampleConfig,
    runtime_factory: Callable[..., Any],
    tmp_path: Path,
) -> None:
    app = create_app(
        offline_config,
        runs_dir=tmp_path,
        runtime_factory=runtime_factory,
        finished_grace_seconds=0.0,
        protocols=(PROTOCOL_A2A_T,),
        nms_enabled=True,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        meta = await client.get("/api/tester/meta")
    assert meta.status_code == 200
    assert meta.json()["nmsEnabled"] is True


async def test_live_token_usage_is_collected_per_case(
    offline_config: SampleConfig,
    tmp_path: Path,
) -> None:
    live_config = replace(offline_config, model_mode=MODEL_MODE_LIVE)

    async def build(*args: object, **kwargs: object) -> _PassRuntime:
        del args, kwargs
        return _PassRuntime(emit_tokens=True)

    app = create_app(
        live_config,
        runs_dir=tmp_path,
        runtime_factory=build,
        finished_grace_seconds=0.0,
        protocols=(PROTOCOL_A2A_T,),
        nms_enabled=False,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        data = _workbook_bytes(
            [
                [
                    "TC-TOKENS",
                    "tokens",
                    "Y",
                    "A2A-T",
                    "night",
                    "zh",
                    "",
                    "task_completed",
                    "none",
                    0,
                    "approve",
                    300,
                    "",
                ]
            ]
        )
        imported = await client.post("/api/tester/import", content=data)
        batch = imported.json()
        await client.post(f"/api/tester/batches/{batch['batchId']}/start")
        finished = await _wait_batch(client, batch["batchId"])

        tokens = finished["results"][0]["metrics"]["tokens"]
        assert tokens["available"] is True
        assert tokens["prompt"] == 100
        assert tokens["completion"] == 50
        assert tokens["total"] == 150
        assert tokens["llmCalls"] == 1
        assert finished["summary"]["tokens"] == 150

        export = await client.get(f"/api/tester/batches/{batch['batchId']}/export.xlsx")
        workbook = load_workbook(BytesIO(export.content), read_only=True)
        summary_rows = list(workbook["汇总"].iter_rows(values_only=True))
        workbook.close()
    assert ("Token 总量", 150) in summary_rows


async def test_the_results_workbook_contains_the_verdicts(client: httpx.AsyncClient) -> None:
    imported = await client.post("/api/tester/import", content=write_template())
    batch = imported.json()
    await client.post(f"/api/tester/batches/{batch['batchId']}/start")
    await _wait_batch(client, batch["batchId"])

    response = await client.get(f"/api/tester/batches/{batch['batchId']}/export.xlsx")
    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.content), read_only=True)
    assert workbook.sheetnames == ["测试结果", "指标", "汇总"]
    rows = list(workbook["测试结果"].iter_rows(values_only=True))
    metric_rows = list(workbook["指标"].iter_rows(values_only=True))
    summary_rows = list(workbook["汇总"].iter_rows(values_only=True))
    workbook.close()

    assert rows[0][2] == "执行状态"
    assert "Token 总量" in rows[0]
    assert {row[2] for row in rows[1:]} == {"passed"}
    assert {row[0] for row in rows[1:]} == {"TC-001", "TC-002", "TC-003"}
    assert metric_rows[0][0] == "用例编号"
    assert {row[0] for row in metric_rows[1:]} == {"TC-001", "TC-002", "TC-003"}
    assert summary_rows[0] == ("指标", "值")
    assert ("Token 统计", "离线脚本或未上报") in summary_rows
