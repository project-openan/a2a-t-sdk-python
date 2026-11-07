"""Contract tests of the catalog-driven tester console."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from demo.web.app import create_app
from demo.web.tester.decisions import a2at_decisions, pure_answers
from eval.osp.intent_schema import OSP_INTENT_SCHEMA
from eval.osp.runner import load_cases
from protocol.client_flow import ClientFlowResult
from shared.config import SampleConfig
from shared.run_constants import (
    FINAL_STATE_TASK_COMPLETED,
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
        run_id: str | None = None,
        user_request: str | None = None,
        conversation_context: str | None = None,
        on_decision_requested: Callable[..., None] | None = None,
        on_decision_made: Callable[..., None] | None = None,
        on_intent_ready: Callable[..., None] | None = None,
        validate_before_send: bool = False,
        enforce_required_fields: bool = False,
        run_mode: str = "create",
        target_task_id: str | None = None,
        target_context_id: str | None = None,
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
            run_mode,
            target_task_id,
            target_context_id,
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
    """SDK double returning the expected Task-T and intent of each case."""

    def __init__(self, *, fail_ids: frozenset[int] = frozenset()) -> None:
        self._by_text = {case["user_input"]: case for case in CASES.values()}
        self._fail_ids = fail_ids
        self._last_id: int | None = None

    def generate_task_prompt_from_text(self, text: str, template_uri: str) -> object:
        case = self._by_text[text]
        self._last_id = int(case["id"])
        task_t = str(case["expect"]["protocols"]["a2a-t"]["task_t"])
        if self._last_id in self._fail_ids:
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


async def test_the_catalog_lists_the_51_osp_cases(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/tester/catalog")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 51
    first = payload["cases"][0]
    assert first["id"] == 1
    assert first["validate"] == "pass"
    assert first["protocols"] == ["a2a-t", "a2a"]


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
        "final",
    ]
    assert payload["catalogCount"] == 51


async def test_creating_a_batch_expands_each_case_by_protocol(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/tester/batches",
        json={
            "caseIds": [1, 2],
            "protocols": ["a2a-t", "a2a"],
            "stopAfter": "final",
            "approval": "approve",
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
            "approval": "approve",
            "timeoutSeconds": 60,
        },
    )
    batch_id = created.json()["batchId"]
    await client.post(f"/api/tester/batches/{batch_id}/start")
    finished = await _wait_batch(client, batch_id)
    assert finished["summary"]["passed"] == 1
    item = finished["items"][0]
    assert item["status"] == "passed"
    assert item["stages"]["intent"]["status"] == "passed"
    assert item["stages"]["task_t"]["status"] == "passed"
    assert item["stages"]["validate"]["status"] == "passed"


async def test_the_full_flow_asserts_send_negotiate_and_final(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/tester/batches",
        json={
            "caseIds": [1],
            "protocols": ["a2a-t"],
            "stopAfter": "final",
            "approval": "approve",
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
    assert item["stages"]["final"]["status"] == "passed"


async def test_a2a_rejects_a_sdk_only_stop_stage(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/tester/batches",
        json={
            "caseIds": [1],
            "protocols": ["a2a"],
            "stopAfter": "validate",
            "approval": "approve",
            "timeoutSeconds": 60,
        },
    )
    batch_id = created.json()["batchId"]
    await client.post(f"/api/tester/batches/{batch_id}/start")
    finished = await _wait_batch(client, batch_id)
    assert finished["items"][0]["status"] == "error"
    assert "SDK 阶段" in finished["items"][0]["error"]


async def test_a_scratch_request_runs_without_expectations(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/tester/scratch",
        json={
            "userInput": "Create an energy saving task for A2ATestRegion.",
            "protocol": "a2a-t",
            "approval": "approve",
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
        tester_prompt_client=FakePromptClient(fail_ids=frozenset({1})),
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
        assert finished["summary"]["failed"] == 1
        assert finished["summary"]["passed"] == 1
        retried = await http.post(f"/api/tester/batches/{batch_id}/retry")
        assert retried.status_code == 201
        assert [item["caseId"] for item in retried.json()["items"]] == [1]


async def test_the_batch_exports_json_and_xlsx(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/tester/batches",
        json={
            "caseIds": [1],
            "protocols": ["a2a-t"],
            "stopAfter": "validate",
            "approval": "approve",
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


def test_a2at_decision_parser_keeps_target_and_feasibility_order() -> None:
    decisions = a2at_decisions(CASES[34])
    assert len(decisions) == 2
    assert "10Mbps" in str(decisions[0].confirmed_intent)
    assert decisions[1].feasibility_summary


def test_a2at_decision_parser_reads_a_feasibility_reply() -> None:
    decisions = a2at_decisions(CASES[32])
    assert len(decisions) == 1
    assert "Reduce throughput target" in str(decisions[0].feasibility_summary)


def test_pure_answers_replay_the_p_column() -> None:
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
