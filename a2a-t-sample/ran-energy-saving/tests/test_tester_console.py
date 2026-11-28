"""Contract tests of the catalog-driven tester console."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import PromptGenerationError, SlotValidationError
from demo.web.app import create_app
from demo.web.tester.decisions import pure_answers
from demo.web.tester.service import (
    DEFAULT_CONCURRENCY,
    DEFAULT_ROUNDS,
    MAX_CONCURRENCY,
    MAX_ROUNDS,
)
from eval.osp.intent_schema import OSP_INTENT_SCHEMA
from eval.osp.runner import load_cases
from protocol.client_flow import ClientFlowResult
from shared.config import SampleConfig
from shared.run_constants import (
    FINAL_STATE_TASK_COMPLETED,
    LANGUAGE_EN,
    PROTOCOL_A2A_T,
    PROTOCOL_PURE,
)
from shared.trace import TraceRecorder


class _PassRuntime:
    """Runtime double that completes every request and emits one sent message."""

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
        on_intent_ready: Callable[..., None] | None = None,
        validate_before_send: bool = False,
        enforce_required_fields: bool = False,
        require_rate_target: bool = True,
        site_level_rate_optional: bool = False,
        run_mode: str = "create",
        target_task_id: str | None = None,
        target_context_id: str | None = None,
        target_server_task_id: str | None = None,
        target_server_context_id: str | None = None,
        decision_basis: object = None,
        **kwargs: object,
    ) -> ClientFlowResult:
        del (
            run_id,
            user_request,
            conversation_context,
            on_decision_requested,
            on_decision_made,
            on_intent_ready,
            validate_before_send,
            enforce_required_fields,
            require_rate_target,
            site_level_rate_optional,
            run_mode,
            target_task_id,
            target_context_id,
            target_server_task_id,
            target_server_context_id,
            decision_basis,
            kwargs,
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
        if trace_sink is not None:
            recorder = TraceRecorder("test", id_prefix="fake")
            span = recorder.emit(
                kind="message_sent",
                phase="task_prompt",
                title="message sent",
                actor="client",
                side="client",
            )
            trace_sink(span.to_dict())
        return ClientFlowResult(
            final_state=FINAL_STATE_TASK_COMPLETED,
            context_id="ctx-1",
            task_id="task-1",
        )

    async def aclose(self) -> None:
        """Release the runtime."""


CASES: dict[int, dict[str, Any]] = {case["id"]: case for case in load_cases()}


class FakePromptClient:
    """SDK double returning the expected Task-T and intent of each case.

    ``warn_ids`` renders a wrong Task-T, which the tester reports as a warning. ``block_ids``
    raises the SDK slot-extraction failure, which really fails a case that expects no block.
    """

    def __init__(
        self,
        *,
        warn_ids: frozenset[int] = frozenset(),
        block_ids: frozenset[int] = frozenset(),
    ) -> None:
        self._by_text = {case["user_input"]: case for case in CASES.values()}
        self._warn_ids = warn_ids
        self._block_ids = block_ids
        self._last_id: int | None = None

    def generate_task_prompt_from_text(self, text: str, template_uri: str) -> object:
        case = self._by_text[text]
        self._last_id = int(case["id"])
        if self._last_id in self._block_ids:
            raise PromptGenerationError(
                ErrorCatalog.SLOT_NOT_PROVIDED,
                {"slot_label": "Area"},
                message="blocked by test",
                failed_parameters=[
                    SlotValidationError(
                        slot_name="task_object",
                        code="slot.not_provided",
                        message="blocked by test",
                        facts={"slot_label": "Area"},
                    )
                ],
            )
        task_t = str(case["expect"]["protocols"]["a2a-t"]["task_t"])
        if self._last_id in self._warn_ids:
            task_t = task_t.replace("A2ATestRegion", "WrongRegion", 1)
        return SimpleNamespace(prompt_text=task_t, template_uri=template_uri)

    def validate_task_prompt_and_data_filling(
        self,
        prompt: str,
        schema: Mapping[str, object],
        template_uri: str,
    ) -> object:
        del prompt, template_uri
        assert self._last_id is not None
        case = CASES[self._last_id]
        if schema is OSP_INTENT_SCHEMA:
            return SimpleNamespace(data=dict(case["expect"]["intent"]))
        return SimpleNamespace(data={})


@pytest.fixture
def runtime_factory() -> Callable[..., Any]:
    """Build a pass-through runtime factory."""

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
        tester_prompt_client=FakePromptClient(),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def _wait_batch(
    client: httpx.AsyncClient, batch_id: str, timeout: float = 30.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await client.get(f"/api/tester/batches/{batch_id}")
        payload = response.json()
        if payload.get("status") not in {"draft", "running"}:
            return payload
        await asyncio.sleep(0.05)
    raise AssertionError(f"batch {batch_id} did not finish")


async def test_the_catalog_lists_the_100_osp_cases(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/tester/catalog")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 100
    first = payload["cases"][0]
    assert first["id"] == 1
    assert first["validate"] == "pass_with_terminology"
    assert first["protocols"] == ["a2a-t", "a2a"]


async def test_the_catalog_reload_endpoint_reloads_without_a_restart(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/api/tester/catalog/reload")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 100
    assert payload["added"] == []
    assert payload["removed"] == []
    assert payload["version"]
    assert payload["loadedAt"]


async def test_the_meta_lists_the_stage_chain(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/tester/meta")
    assert response.status_code == 200
    payload = response.json()
    assert payload["protocols"] == ["a2a-t", "a2a"]
    assert payload["stopAfter"] == [
        "intent",
        "task_t",
        "validate",
        "send",
        "negotiate",
        "accepted",
        "working",
        "final",
    ]
    assert payload["stageOrder"] == payload["stopAfter"]
    assert "decisionModes" not in payload
    assert "decisionMode" not in payload["defaults"]
    assert payload["defaults"]["workingTimeoutSeconds"] == 60
    assert payload["runnerDefaultsIndicateCase"] is True
    assert payload["transport"] in {"sse", "webhook"}
    assert payload["discovery"]
    assert payload["catalogCount"] == 100


async def test_creating_a_batch_expands_each_case_by_protocol(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/tester/batches",
        json={
            "caseIds": [1, 2],
            "protocols": ["a2a-t", "a2a"],
            "stopAfter": "final",
            "timeoutSeconds": 60,
        },
    )
    assert response.status_code == 201
    batch = response.json()
    assert batch["status"] == "draft"
    assert len(batch["items"]) == 4
    assert [item["protocol"] for item in batch["items"]] == ["a2a-t", "a2a", "a2a-t", "a2a"]


async def test_an_empty_selection_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/tester/batches",
        json={"caseIds": [], "protocols": ["a2a-t"], "stopAfter": "validate"},
    )
    assert response.status_code == 400


async def test_the_sdk_stage_batch_passes_with_the_fake_client(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/tester/batches",
        json={
            "caseIds": [1],
            "protocols": ["a2a-t"],
            "stopAfter": "validate",
            "timeoutSeconds": 60,
        },
    )
    batch_id = created.json()["batchId"]
    await client.post(f"/api/tester/batches/{batch_id}/start")
    finished = await _wait_batch(client, batch_id)
    assert finished["summary"]["passed"] == 1
    item = finished["items"][0]
    assert item["status"] == "passed"
    assert item["stages"]["intent"]["status"] == "recorded"
    assert item["stages"]["task_t"]["status"] == "passed"
    assert item["stages"]["validate"]["status"] == "passed"


async def test_the_full_flow_asserts_send_negotiate_and_final(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/tester/batches",
        json={
            "caseIds": [1],
            "protocols": ["a2a-t"],
            "stopAfter": "final",
            "timeoutSeconds": 60,
        },
    )
    batch_id = created.json()["batchId"]
    await client.post(f"/api/tester/batches/{batch_id}/start")
    finished = await _wait_batch(client, batch_id)
    item = finished["items"][0]
    assert item["status"] == "passed", item
    assert item["stages"]["send"]["status"] == "passed"
    assert item["stages"]["negotiate"]["status"] == "passed"
    assert item["stages"]["accepted"]["status"] == "passed"
    assert item["stages"]["working"]["status"] == "passed"
    assert item["stages"]["final"]["status"] == "passed"


async def test_the_trace_tab_reads_the_tester_run_timeline(
    client: httpx.AsyncClient,
) -> None:
    """The drawer's trace tab reuses the run trace endpoint: it must resolve a tester run."""
    created = await client.post(
        "/api/tester/batches",
        json={
            "caseIds": [1],
            "protocols": ["a2a-t"],
            "stopAfter": "final",
            "timeoutSeconds": 60,
        },
    )
    batch_id = created.json()["batchId"]
    await client.post(f"/api/tester/batches/{batch_id}/start")
    finished = await _wait_batch(client, batch_id)
    run_id = finished["items"][0]["runId"]
    assert run_id

    response = await client.get(f"/api/runs/{run_id}/trace.json")

    assert response.status_code == 200
    document = response.json()
    assert document["runId"] == run_id
    assert isinstance(document["transcript"], dict)
    assert document["transcript"]["timeline"]


async def test_a2a_rejects_a_sdk_only_stop_stage(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/tester/batches",
        json={
            "caseIds": [1],
            "protocols": ["a2a"],
            "stopAfter": "validate",
            "timeoutSeconds": 60,
        },
    )
    assert created.status_code == 400
    assert "SDK stages" in created.json()["error"]


async def test_a_trace_of_an_unknown_previous_run_is_explicitly_unavailable(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/runs/does-not-exist/trace.json")
    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == "trace_not_available"
    assert payload["error"]


async def test_the_catalog_detail_returns_the_full_case(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/tester/catalog/1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["id"] == 1
    assert payload["case"]["id"] == 1
    assert payload["case"]["user_input"]
    assert "runner" in payload["case"]
    assert "_path" not in payload["case"]

    assert (await client.get("/api/tester/catalog/9999")).status_code == 404
    assert (await client.get("/api/tester/catalog/not-a-number")).status_code == 404


class _HangingRuntime:
    """Runtime double that keeps the run open until it is cancelled."""

    async def run(self, **kwargs: object) -> ClientFlowResult:
        del kwargs
        await asyncio.sleep(30)
        return ClientFlowResult(final_state=FINAL_STATE_TASK_COMPLETED)

    async def aclose(self) -> None:
        """Release the runtime."""


async def test_rerun_is_rejected_while_that_items_run_is_still_running(
    offline_config: SampleConfig,
    tmp_path: Path,
) -> None:
    async def hanging_factory(*args: object, **kwargs: object) -> _HangingRuntime:
        del args, kwargs
        return _HangingRuntime()

    app = create_app(
        offline_config,
        runs_dir=tmp_path,
        runtime_factory=hanging_factory,
        finished_grace_seconds=0.0,
        protocols=(PROTOCOL_A2A_T, PROTOCOL_PURE),
        nms_enabled=False,
        tester_prompt_client=FakePromptClient(),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        created = await http.post(
            "/api/tester/batches",
            json={
                "caseIds": [1],
                "protocols": ["a2a-t"],
                "stopAfter": "final",
                "timeoutSeconds": 60,
            },
        )
        batch_id = created.json()["batchId"]
        await http.post(f"/api/tester/batches/{batch_id}/start")
        rerun = await http.post(f"/api/tester/batches/{batch_id}/items/0/rerun")
        assert rerun.status_code == 409
        await http.post(f"/api/tester/batches/{batch_id}/cancel")


async def test_rerun_of_an_unknown_batch_or_index_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    created = await _create_tester_batch(client)
    batch_id = created["batchId"]
    await client.post(f"/api/tester/batches/{batch_id}/start")
    finished = await _wait_batch(client, batch_id)

    assert finished["status"] == "finished"
    unknown = await client.post("/api/tester/batches/batch-missing/items/0/rerun")
    assert unknown.status_code == 404
    invalid = await client.post(f"/api/tester/batches/{batch_id}/items/9/rerun")
    assert invalid.status_code == 409


async def test_a_scratch_request_runs_without_expectations(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/tester/scratch",
        json={
            "userInput": "Create an energy saving task for A2ATestRegion.",
            "protocol": "a2a-t",
            "timeoutSeconds": 60,
        },
    )
    assert created.status_code == 201
    batch_id = created.json()["batchId"]
    await client.post(f"/api/tester/batches/{batch_id}/start")
    finished = await _wait_batch(client, batch_id)
    item = finished["items"][0]
    assert item["caseId"] == 0
    assert item["stages"]["final"]["status"] == "recorded"


async def test_retry_rebuilds_only_the_failed_items(
    offline_config: SampleConfig,
    runtime_factory: Callable[..., Any],
    tmp_path: Path,
) -> None:
    app = create_app(
        offline_config,
        runs_dir=tmp_path,
        runtime_factory=runtime_factory,
        finished_grace_seconds=0.0,
        protocols=(PROTOCOL_A2A_T, PROTOCOL_PURE),
        nms_enabled=False,
        tester_prompt_client=FakePromptClient(block_ids=frozenset({1})),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        created = await http.post(
            "/api/tester/batches",
            json={
                "caseIds": [1, 2],
                "protocols": ["a2a-t"],
                "stopAfter": "validate",
                "timeoutSeconds": 60,
            },
        )
        batch_id = created.json()["batchId"]
        await http.post(f"/api/tester/batches/{batch_id}/start")
        finished = await _wait_batch(http, batch_id)
        assert finished["summary"]["failed"] == 1
        assert finished["summary"]["passed"] == 1
        retried = await http.post(f"/api/tester/batches/{batch_id}/retry")
        assert retried.status_code == 201
        assert [item["caseId"] for item in retried.json()["items"]] == [1]


async def test_a_task_t_mismatch_makes_the_row_partial_and_is_not_retried(
    offline_config: SampleConfig,
    runtime_factory: Callable[..., Any],
    tmp_path: Path,
) -> None:
    """A Task-T difference is a warning: it must not fail the case nor be rebuilt by retry."""
    app = create_app(
        offline_config,
        runs_dir=tmp_path,
        runtime_factory=runtime_factory,
        finished_grace_seconds=0.0,
        protocols=(PROTOCOL_A2A_T, PROTOCOL_PURE),
        nms_enabled=False,
        tester_prompt_client=FakePromptClient(warn_ids=frozenset({1})),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        created = await http.post(
            "/api/tester/batches",
            json={
                "caseIds": [1, 2],
                "protocols": ["a2a-t"],
                "stopAfter": "validate",
                "approval": "approve",
                "timeoutSeconds": 60,
            },
        )
        batch_id = created.json()["batchId"]
        await http.post(f"/api/tester/batches/{batch_id}/start")
        finished = await _wait_batch(http, batch_id)
        assert finished["summary"]["failed"] == 0
        assert finished["summary"]["passed"] == 1
        assert finished["summary"]["partial"] == 1
        item = next(row for row in finished["items"] if row["caseId"] == 1)
        assert item["status"] == "partial"
        assert item["stages"]["task_t"]["status"] == "warning"
        assert item["stages"]["task_t"]["diffs"]
        retried = await http.post(f"/api/tester/batches/{batch_id}/retry")
        assert retried.status_code == 409


async def test_the_batch_exports_json_and_xlsx(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/tester/batches",
        json={
            "caseIds": [1],
            "protocols": ["a2a-t"],
            "stopAfter": "validate",
            "timeoutSeconds": 60,
        },
    )
    batch_id = created.json()["batchId"]
    exported = await client.get(f"/api/tester/batches/{batch_id}/export.json")
    assert exported.status_code == 200
    assert exported.json()["batchId"] == batch_id
    workbook = await client.get(f"/api/tester/batches/{batch_id}/export.xlsx")
    assert workbook.status_code == 200
    assert workbook.content.startswith(b"PK")


async def test_the_export_rejects_an_unknown_langfuse_switch(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/tester/batches",
        json={"caseIds": [1], "protocols": ["a2a-t"], "stopAfter": "validate", "approval": "approve"},
    )
    batch_id = created.json()["batchId"]

    rejected = await client.get(
        f"/api/tester/batches/{batch_id}/export.xlsx", params={"langfuseColumns": "yes"}
    )

    assert rejected.status_code == 400
    assert rejected.json()["code"] == "invalid_langfuse_columns"


async def test_auto_skips_the_fetch_without_langfuse_credentials(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from demo.web.tester import routes as tester_routes

    def fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("without credentials the auto mode must not fetch")

    monkeypatch.setattr(tester_routes, "_trace_columns", fail)
    created = await client.post(
        "/api/tester/batches",
        json={"caseIds": [1], "protocols": ["a2a-t"], "stopAfter": "validate", "approval": "approve"},
    )
    batch_id = created.json()["batchId"]

    automatic = await client.get(f"/api/tester/batches/{batch_id}/export.xlsx")
    off = await client.get(
        f"/api/tester/batches/{batch_id}/export.xlsx", params={"langfuseColumns": "0"}
    )

    assert automatic.status_code == 200
    assert off.status_code == 200


#: Cookie the tester page uses to keep each browser's batches apart.
CLIENT_COOKIE = "a2at_tester_client"


async def _create_tester_batch(
    client: httpx.AsyncClient,
    *,
    case_ids: tuple[int, ...] = (1,),
    protocols: tuple[str, ...] = ("a2a-t",),
    stop_after: str = "validate",
) -> dict[str, Any]:
    response = await client.post(
        "/api/tester/batches",
        json={
            "caseIds": list(case_ids),
            "protocols": list(protocols),
            "stopAfter": stop_after,
            "timeoutSeconds": 60,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_the_tester_page_issues_a_browser_identity_cookie(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/tester")
    assert response.status_code == 200
    assert CLIENT_COOKIE in response.headers.get("set-cookie", "")
    assert client.cookies.get(CLIENT_COOKIE)


async def test_the_tester_meta_reports_the_process_language(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/tester/meta")
    assert response.status_code == 200
    assert response.json()["language"] == LANGUAGE_EN


async def test_the_tester_page_carries_the_process_language_for_the_first_paint(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/tester")
    assert response.status_code == 200
    assert '<meta name="a2at-language" content="en-US">' in response.text
    assert "data-locale-pending" in response.text
    assert "__A2AT_LANGUAGE__" not in response.text


async def test_the_tester_serves_its_copy_module(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/tester/copy.js")
    assert response.status_code == 200
    assert "text/javascript" in response.headers["content-type"]
    assert "export const COPY" in response.text


async def test_error_bodies_follow_the_process_language(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/tester/batches/batch-missing")

    assert response.status_code == 404
    assert response.json() == {"error": "Unknown test batch.", "code": "unknown_batch"}


async def test_a_batch_records_the_language_it_was_created_in(
    client: httpx.AsyncClient,
) -> None:
    created = await _create_tester_batch(client)

    assert created["language"] == LANGUAGE_EN


def test_tester_copy_harness_runs_in_node() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    harness = Path(__file__).resolve().parent / "js" / "tester_i18n.test.mjs"

    result = subprocess.run(
        [node, str(harness)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "tester i18n harness: ok" in result.stdout


async def test_the_status_page_reports_the_process_and_is_linked_from_the_tester(
    client: httpx.AsyncClient,
) -> None:
    page = await client.get("/tester")
    assert 'href="/status"' in page.text

    status_page = await client.get("/status")
    assert status_page.status_code == 200
    assert "运行状态" in status_page.text

    response = await client.get("/api/status")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["process"]["maxConcurrency"] == MAX_CONCURRENCY
    assert payload["process"]["liveRuns"] == 0
    assert payload["batches"] == []
    assert payload["runs"] == []


async def test_a_batch_records_the_creating_browsers_network_origin(
    client: httpx.AsyncClient,
) -> None:
    await client.get("/tester")
    created = await _create_tester_batch(client)

    source = created["ownerSource"]
    assert source["kind"] == "tester"
    assert source["channel"] == "tester-console"
    assert source["entryPath"] == "/api/tester/batches"
    assert source["remoteAddress"]


async def test_batches_are_listed_only_for_the_browser_that_created_them(
    client: httpx.AsyncClient,
) -> None:
    await client.get("/tester")
    created = await _create_tester_batch(client)
    batch_id = created["batchId"]

    listed = await client.get("/api/tester/batches")
    assert batch_id in [batch["batchId"] for batch in listed.json()["batches"]]

    client.cookies.set(CLIENT_COOKIE, "another-browser")
    listed = await client.get("/api/tester/batches")
    assert batch_id not in [batch["batchId"] for batch in listed.json()["batches"]]


async def test_another_browser_cannot_open_start_retry_or_export_a_batch(
    client: httpx.AsyncClient,
) -> None:
    await client.get("/tester")
    batch_id = (await _create_tester_batch(client))["batchId"]
    client.cookies.set(CLIENT_COOKIE, "another-browser")

    assert (await client.get(f"/api/tester/batches/{batch_id}")).status_code == 404
    assert (await client.post(f"/api/tester/batches/{batch_id}/start")).status_code == 404
    assert (await client.post(f"/api/tester/batches/{batch_id}/cancel")).status_code == 404
    assert (await client.post(f"/api/tester/batches/{batch_id}/retry")).status_code == 404
    assert (
        await client.get(f"/api/tester/batches/{batch_id}/export.json")
    ).status_code == 404
    assert (
        await client.get(f"/api/tester/batches/{batch_id}/export.xlsx")
    ).status_code == 404


async def test_a_scratch_batch_belongs_to_the_browser_that_created_it(
    client: httpx.AsyncClient,
) -> None:
    client.cookies.set(CLIENT_COOKIE, "browser-a")
    created = await client.post(
        "/api/tester/scratch",
        json={
            "userInput": "Create an energy saving task for A2ATestRegion.",
            "protocol": "a2a-t",
            "timeoutSeconds": 60,
        },
    )
    assert created.status_code == 201
    batch_id = created.json()["batchId"]

    client.cookies.set(CLIENT_COOKIE, "browser-b")
    listed = await client.get("/api/tester/batches")
    assert batch_id not in [batch["batchId"] for batch in listed.json()["batches"]]


async def test_a_legacy_batch_without_an_owner_is_hidden_from_browsers(
    client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    history = tmp_path / "tester"
    history.mkdir()
    (history / "batch-legacy.json").write_text(
        json.dumps(
            {
                "schema": "osp-tester/v1",
                "batchId": "batch-legacy",
                "status": "finished",
                "createdAt": "2026-01-01T00:00:00.000Z",
                "items": [],
            }
        ),
        encoding="utf-8",
    )
    client.cookies.set(CLIENT_COOKIE, "browser-a")

    listed = await client.get("/api/tester/batches")
    assert "batch-legacy" not in [batch["batchId"] for batch in listed.json()["batches"]]
    assert (await client.get("/api/tester/batches/batch-legacy")).status_code == 404


def test_the_start_button_stays_available_after_a_batch_finished() -> None:
    """Opening yesterday's results must not trap the page on that batch.

    The results pane used to show the start button only for a ``draft`` batch, so a browser that
    reopened its last finished batch could not start a new run without clearing server state.
    """
    tester = Path(__file__).resolve().parent.parent / "demo" / "web" / "tester"
    js = (tester / "main.js").read_text(encoding="utf-8")

    assert re.search(r'\$\("start-button"\)\.hidden\s*=\s*running', js)
    assert not re.search(r'\$\("start-button"\)\.hidden\s*=\s*!draft', js)


def test_pure_answers_expose_the_p_column() -> None:
    answers = pure_answers(CASES[1])
    assert len(answers) == 1
    assert "Set the guaranteed throughput targets" in answers[0]


def test_the_tester_stylesheet_hides_hidden_elements() -> None:
    """The page hides panels with the ``hidden`` attribute, but their own ``display`` wins.

    ``.drawer { display: flex }`` (author sheet) overrides the UA ``[hidden] { display: none }``
    rule, so the stage-difference drawer was open on load and the Close button could not hide it.
    The same applies to the warning banner, the history picker and the progress bar.
    """
    tester = Path(__file__).resolve().parent.parent / "demo" / "web" / "tester"
    css = (tester / "app.css").read_text(encoding="utf-8")
    page = (tester / "index.html").read_text(encoding="utf-8")

    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important;?\s*\}", css)
    for element_id in ("drawer", "drawer-backdrop", "nms-warning", "history-wrap", "progress"):
        assert re.search(rf'id="{element_id}"[^>]*\bhidden\b', page), element_id


async def test_meta_exposes_the_acceptance_stage_model_and_transport(
    client: httpx.AsyncClient,
) -> None:
    """The page builds its stage chain and run form from meta, not from hard-coded lists."""
    meta = (await client.get("/api/tester/meta")).json()

    stage_order = meta["stageOrder"]
    for stage in ("send", "negotiate", "accepted", "working", "final"):
        assert stage in stage_order
    assert stage_order.index("accepted") < stage_order.index("working") < stage_order.index("final")
    assert meta["defaults"]["stopAfter"] == "accepted"
    assert "decisionMode" not in meta["defaults"]
    assert "decisionModes" not in meta
    assert meta["transport"] in {"sse", "webhook"}
    assert meta["discovery"]


async def test_a_batch_that_names_no_stage_stops_at_accepted(
    client: httpx.AsyncClient,
) -> None:
    """A vendor that pushes no final state must not turn a finished negotiation into a timeout."""
    response = await client.post(
        "/api/tester/batches",
        json={"caseIds": [1], "protocols": ["a2a-t"]},
    )

    assert response.status_code == 201, response.text
    assert response.json()["stopAfter"] == "accepted"


async def test_a_scratch_batch_stops_at_accepted(client: httpx.AsyncClient) -> None:
    """An ad-hoc request faces the same vendor behaviour as a catalog batch."""
    created = await client.post(
        "/api/tester/scratch",
        json={
            "userInput": "Create an energy saving task for AATestRegion.",
            "protocol": "a2a-t",
        },
    )

    assert created.status_code == 201, created.text
    assert created.json()["stopAfter"] == "accepted"


def test_the_tester_page_binds_the_case_detail_drawer_and_rerun() -> None:
    """The detail drawer and per-row rerun are DOM contracts, not just service behavior."""
    tester = Path(__file__).resolve().parent.parent / "demo" / "web" / "tester"
    js = (tester / "main.js").read_text(encoding="utf-8")
    page = (tester / "index.html").read_text(encoding="utf-8")

    assert "accepted" in js and "working" in js
    assert "/api/tester/catalog/" in js
    assert "/items/${index}/rerun" in js
    for element_id in (
        "drawer-tab-result",
        "drawer-tab-trace",
        "drawer-tab-case",
        "config-stop-after",
        "config-working-timeout",
    ):
        assert re.search(rf'id="{element_id}"', page), element_id


def test_the_catalog_offers_a_case_detail_without_running_it() -> None:
    """A detail click inside the selection <label> must not toggle the checkbox."""
    tester = Path(__file__).resolve().parent.parent / "demo" / "web" / "tester"
    js = (tester / "main.js").read_text(encoding="utf-8")
    copy = (tester / "copy.js").read_text(encoding="utf-8")

    assert "openCasePreview" in js
    assert 'el("button", "catalog-detail", t(copy, "action.detail"))' in js
    assert "event.preventDefault()" in js
    assert "event.stopPropagation()" in js
    # The drawer shows the other protocol's expectations too.
    assert "protocol-switch" in js
    # The case detail surfaces the paired Modify baseline and the blocked follow-up request.
    for key in ("case.baselineTitle", "case.followupTitle", "case.followupExpect"):
        assert f'"{key}":' in copy, key
        assert key in js, key


def test_the_result_row_and_drawer_expose_run_context_ids() -> None:
    """runId / traceId / taskId / contextId must be visible without exporting the report."""
    tester = Path(__file__).resolve().parent.parent / "demo" / "web" / "tester"
    js = (tester / "main.js").read_text(encoding="utf-8")
    copy = (tester / "copy.js").read_text(encoding="utf-8")
    css = (tester / "app.css").read_text(encoding="utf-8")
    page = (tester / "index.html").read_text(encoding="utf-8")

    assert "function itemContext" in js
    assert "function appendContextBlock" in js
    assert 't(copy, "drawer.runContext")' in js
    assert "context-line" in js
    assert 't(copy, "drawer.taskIdOuter")' in js
    assert ".context-line" in css
    # A Modify item shows both runs: its baseline Create and the Modify itself.
    assert "function runLine" in js and "function appendBaselineBlock" in js
    assert 't(copy, "baseline.title")' in js
    for key in ("drawer.runContext", "drawer.taskIdOuter", "baseline.title"):
        assert f'"{key}":' in copy, key
    assert "run-cell" in js and "run-line" in css
    assert '<th>RunId</th>' in page
    # The RunId cell must actually be appended: the header and the row have 11 columns.
    assert "tokens, runs, result, actions" in js
    # A live session-trace tab reuses the run trace endpoint for the baseline and Modify runs.
    assert "function renderTraceTab" in js
    assert "/trace.json" in js
    assert "function refreshOpenDrawer" in js
    assert 'id="drawer-tab-trace"' in page


def test_the_console_uses_the_full_width_of_a_large_screen() -> None:
    tester = Path(__file__).resolve().parent.parent / "demo" / "web" / "tester"
    css = (tester / "app.css").read_text(encoding="utf-8")

    assert not re.search(r"\.main\s*\{[^}]*max-width:\s*1440px", css)


async def test_the_meta_advertises_the_concurrency_cap(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/tester/meta")

    payload = response.json()
    assert payload["maxConcurrency"] == MAX_CONCURRENCY
    assert payload["defaults"]["concurrency"] == DEFAULT_CONCURRENCY


async def test_a_batch_can_be_created_with_concurrency(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/tester/batches",
        json={
            "caseIds": [1, 2],
            "protocols": ["a2a-t"],
            "stopAfter": "validate",
            "concurrency": 4,
        },
    )

    assert response.status_code == 201
    assert response.json()["concurrency"] == 4
    assert response.json()["activeIndices"] == []


async def test_an_out_of_range_concurrency_is_rejected(client: httpx.AsyncClient) -> None:
    for value in (0, MAX_CONCURRENCY + 1, True):
        response = await client.post(
            "/api/tester/batches",
            json={
                "caseIds": [1],
                "protocols": ["a2a-t"],
                "stopAfter": "validate",
                "concurrency": value,
            },
        )
        assert response.status_code == 400, value


async def test_an_item_cancel_needs_a_running_batch(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/tester/batches",
        json={"caseIds": [1], "protocols": ["a2a-t"], "stopAfter": "validate"},
    )
    batch_id = created.json()["batchId"]

    draft = await client.post(f"/api/tester/batches/{batch_id}/items/0/cancel")
    assert draft.status_code == 409
    assert draft.json()["code"] == "item_not_cancellable"

    unknown = await client.post("/api/tester/batches/batch-unknown/items/0/cancel")
    assert unknown.status_code == 404

    invalid = await client.post(f"/api/tester/batches/{batch_id}/items/99/cancel")
    assert invalid.status_code == 404


def test_the_console_has_a_concurrency_control() -> None:
    tester = Path(__file__).resolve().parent.parent / "demo" / "web" / "tester"
    page = (tester / "index.html").read_text(encoding="utf-8")
    js = (tester / "main.js").read_text(encoding="utf-8")

    assert 'id="config-concurrency"' in page
    assert "maxConcurrency" in js
    assert "concurrency: Number($(\"config-concurrency\")" in js
    assert "/items/${index}/cancel" in js
    assert 't(copy, "progress.running"' in js


async def test_the_meta_advertises_the_round_cap(client: httpx.AsyncClient) -> None:
    payload = (await client.get("/api/tester/meta")).json()

    assert payload["maxRounds"] == MAX_ROUNDS
    assert payload["defaults"]["rounds"] == DEFAULT_ROUNDS


async def test_a_batch_can_be_created_with_rounds(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/tester/batches",
        json={
            "caseIds": [1],
            "protocols": ["a2a-t"],
            "stopAfter": "validate",
            "rounds": 3,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["rounds"] == 3
    assert payload["currentRound"] == 1
    assert [item["round"] for item in payload["items"]] == [1, 2, 3]


async def test_an_out_of_range_rounds_is_rejected(client: httpx.AsyncClient) -> None:
    for value in (0, MAX_ROUNDS + 1, True):
        response = await client.post(
            "/api/tester/batches",
            json={
                "caseIds": [1],
                "protocols": ["a2a-t"],
                "stopAfter": "validate",
                "rounds": value,
            },
        )
        assert response.status_code == 400, value


async def test_a_multi_round_batch_rejects_keep_tasks(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/tester/batches",
        json={
            "caseIds": [1],
            "protocols": ["a2a-t"],
            "stopAfter": "validate",
            "rounds": 2,
            "keepTasks": True,
        },
    )

    assert response.status_code == 400
    assert "cannot keep tasks" in response.json()["error"]


def test_the_console_shows_a_langfuse_export_warning() -> None:
    tester = Path(__file__).resolve().parent.parent / "demo" / "web" / "tester"
    js = (tester / "main.js").read_text(encoding="utf-8")
    copy = (tester / "copy.js").read_text(encoding="utf-8")

    assert "item.langfuseWarning" in js
    assert 't(copy, "langfuse.exportFailed")' in js
    assert '"langfuse.exportFailed":' in copy


def test_the_console_has_a_round_control() -> None:
    tester = Path(__file__).resolve().parent.parent / "demo" / "web" / "tester"
    page = (tester / "index.html").read_text(encoding="utf-8")
    js = (tester / "main.js").read_text(encoding="utf-8")

    assert 'id="config-rounds"' in page
    assert 'id="round-filter"' in page
    assert 'id="round-head"' in page
    assert "maxRounds" in js
    assert "rounds," in js
    assert "roundSummaryNote" in js
    assert "syncRoundsKeepTasks" in js
