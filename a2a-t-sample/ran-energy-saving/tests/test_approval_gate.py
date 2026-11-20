"""Tests of the human approval gate in front of a network change."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from demo import ran_data
from demo.agents.server_agent.langgraph_tools import (
    APPROVAL_DENIED,
    PlanGuard,
    build_execution_tools,
)
from protocol.approval_gate import ApprovalRegistry
from shared.config import DISCOVERY_NMS, SampleConfig


@pytest.fixture(autouse=True)
def _reset_plan_store() -> Iterator[None]:
    """Keep every test independent of the process-local plan store."""
    ran_data.reset()
    yield
    ran_data.reset()


async def test_registry_resolves_a_waiting_approval() -> None:
    registry = ApprovalRegistry()
    waiting = asyncio.create_task(registry.wait("run-1", 1.0))
    await asyncio.sleep(0.01)

    assert registry.pending("run-1") is True
    assert registry.resolve("run-1", True) is True

    assert await waiting is True
    assert registry.pending("run-1") is False


async def test_registry_times_out_into_a_rejection() -> None:
    registry = ApprovalRegistry()

    assert await registry.wait("run-2", 0.01) is False
    assert registry.resolve("run-2", True) is False


async def test_apply_tool_writes_nothing_when_the_engineer_rejects() -> None:
    async def reject(*_args: object) -> bool:
        return False

    guard = PlanGuard()
    profile, evaluate, apply, _modify, _report = build_execution_tools(guard, approval_gate=reject)
    await profile.ainvoke({"cell_id": "cell-001", "date": "2026-10-22"})
    await evaluate.ainvoke(
        {"cell_id": "cell-001", "window": "00:00~06:00", "target_rate": "30"}
    )

    result = json.loads(
        await apply.ainvoke(
            {"cell_id": "cell-001", "window": "00:00~06:00", "target_rate": "30"}
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == APPROVAL_DENIED
    assert ran_data.list_plans() == ()


async def test_modify_tool_updates_the_existing_plan_and_allows_the_report() -> None:
    guard = PlanGuard()
    profile, evaluate, _apply, modify, report = build_execution_tools(guard)
    plan = ran_data.apply_plan("cell-002", "01:00~05:00", "10")
    await profile.ainvoke({"cell_id": "cell-002", "date": "2026-10-22"})
    await evaluate.ainvoke(
        {"cell_id": "cell-002", "window": "01:00~05:00", "target_rate": "20"}
    )

    modified = json.loads(
        await modify.ainvoke(
            {
                "plan_id": plan.plan_id,
                "cell_id": "cell-002",
                "window": "01:00~05:00",
                "target_rate": "20",
            }
        )
    )
    report_result = json.loads(await report.ainvoke({"plan_id": plan.plan_id}))

    assert modified["ok"] is True
    assert modified["plan_id"] == plan.plan_id
    assert modified["status"] == "modified"
    assert guard.applied is not None
    assert guard.applied["plan_id"] == plan.plan_id
    assert report_result["ok"] is True
    assert report_result["status"] == "modified"


async def test_modify_tool_waits_for_the_second_approval() -> None:
    approved = False

    async def gate(*_args: object) -> bool:
        return approved

    guard = PlanGuard()
    profile, evaluate, _apply, modify, _report = build_execution_tools(guard, approval_gate=gate)
    plan = ran_data.apply_plan("cell-002", "01:00~05:00", "10")
    await profile.ainvoke({"cell_id": "cell-002", "date": "2026-10-22"})
    await evaluate.ainvoke(
        {"cell_id": "cell-002", "window": "01:00~05:00", "target_rate": "20"}
    )

    denied = json.loads(
        await modify.ainvoke(
            {
                "plan_id": plan.plan_id,
                "cell_id": "cell-002",
                "window": "01:00~05:00",
                "target_rate": "20",
            }
        )
    )

    assert denied["ok"] is False
    assert denied["error_code"] == APPROVAL_DENIED
    assert ran_data.get_plan(plan.plan_id) == plan

    approved = True
    accepted = json.loads(
        await modify.ainvoke(
            {
                "plan_id": plan.plan_id,
                "cell_id": "cell-002",
                "window": "01:00~05:00",
                "target_rate": "20",
            }
        )
    )

    assert accepted["ok"] is True
    assert accepted["status"] == "modified"


async def test_apply_tool_writes_the_plan_after_approval() -> None:
    async def approve(*_args: object) -> bool:
        return True

    guard = PlanGuard()
    profile, evaluate, apply, _modify, _report = build_execution_tools(guard, approval_gate=approve)
    await profile.ainvoke({"cell_id": "cell-001", "date": "2026-10-22"})
    await evaluate.ainvoke(
        {"cell_id": "cell-001", "window": "00:00~06:00", "target_rate": "30"}
    )

    result = json.loads(
        await apply.ainvoke(
            {"cell_id": "cell-001", "window": "00:00~06:00", "target_rate": "30"}
        )
    )

    assert result["ok"] is True
    assert len(ran_data.list_plans()) == 1


# --- Web UI approval forwarding ------------------------------------------


def _nms_config(config: SampleConfig) -> SampleConfig:
    """Return a configuration whose client talks to the vendor NMS."""
    return replace(
        config,
        discovery=DISCOVERY_NMS,
        nms_a2a_base="https://nms.example/a2a/json",
        nms_token_url="https://nms.example/sessions",
        nms_token_user="user",
        nms_token_value="secret",
    )


def test_a_server_url_equal_to_the_nms_base_is_rejected(offline_config: SampleConfig) -> None:
    from demo.web.app import _targets_the_nms

    config = _nms_config(offline_config)
    assert _targets_the_nms("https://nms.example/a2a/json/", config) is True
    assert _targets_the_nms("https://nms.example/a2a/json/", offline_config) is False
    assert _targets_the_nms(None, config) is False


def test_the_nms_meta_hides_the_local_approval_gate(
    offline_config: SampleConfig,
    tmp_path: Path,
) -> None:
    import httpx
    from demo.web.app import create_app

    app = create_app(_nms_config(offline_config), runs_dir=tmp_path, finished_grace_seconds=0.0)
    transport = httpx.ASGITransport(app=app)

    async def fetch() -> dict[str, object]:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/meta")
        assert response.status_code == 200
        return dict(response.json())

    meta = asyncio.run(fetch())
    assert meta["approvalGate"] is False


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeClient:
    """One HTTP client double whose POST either raises or returns a canned response."""

    def __init__(self, *, error: Exception | None = None, response: _FakeResponse | None = None) -> None:
        self.error = error
        self.response = response
        self.kwargs: dict[str, object] = {}
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> "_FakeClient":
        self.kwargs = kwargs
        return self

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False

    async def post(self, url: str, *, json: dict[str, object], headers: dict[str, str]) -> _FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _forward_with(
    monkeypatch: pytest.MonkeyPatch,
    fake: _FakeClient,
    *,
    verify_tls: bool = False,
) -> tuple[int, dict[str, object]]:
    import demo.web.app as app_module

    monkeypatch.setattr(app_module.httpx, "AsyncClient", fake)
    return asyncio.run(
        app_module.forward_approval_decision(
            "https://server.example/a2a",
            run_id="run-1",
            approved=True,
            headers={"authorization": "Bearer token"},
            verify_tls=verify_tls,
        )
    )


def test_forwarding_uses_the_configured_tls_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(response=_FakeResponse(200, {"accepted": True}))

    status, payload = _forward_with(monkeypatch, fake, verify_tls=False)

    assert status == 200
    assert payload["accepted"] is True
    assert fake.kwargs["verify"] is False
    assert fake.calls[0]["url"] == "https://server.example/a2a/approvals"
    assert fake.calls[0]["headers"] == {"authorization": "Bearer token"}


def test_forwarding_reports_a_timeout_with_a_distinct_code(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    status, payload = _forward_with(monkeypatch, _FakeClient(error=httpx.ReadTimeout("slow")))

    assert status == 504
    assert payload["code"] == "approval_timeout"


def test_forwarding_reports_a_tls_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import ssl

    import httpx

    cause = httpx.ConnectError("tls rejected")
    cause.__cause__ = ssl.SSLError("certificate verify failed")

    status, payload = _forward_with(monkeypatch, _FakeClient(error=cause))

    assert status == 502
    assert payload["code"] == "approval_tls_error"


def test_forwarding_reports_a_connect_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    status, payload = _forward_with(monkeypatch, _FakeClient(error=httpx.ConnectError("refused")))

    assert status == 502
    assert payload["code"] == "approval_connect_failed"


def test_forwarding_does_not_report_a_redirect_as_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    status, payload = _forward_with(monkeypatch, _FakeClient(response=_FakeResponse(302)))

    assert status == 502
    assert payload["code"] == "approval_target_rejected"


def test_forwarding_reports_a_missing_route(monkeypatch: pytest.MonkeyPatch) -> None:
    status, payload = _forward_with(monkeypatch, _FakeClient(response=_FakeResponse(404)))

    assert status == 404
    assert payload["code"] == "approval_target_not_found"


def test_forwarding_passes_the_server_code_through(monkeypatch: pytest.MonkeyPatch) -> None:
    status, payload = _forward_with(
        monkeypatch,
        _FakeClient(response=_FakeResponse(409, {"error": "already resolved", "code": "approval_not_pending"})),
    )

    assert status == 409
    assert payload["code"] == "approval_not_pending"


def test_web_serve_rejects_nms_with_the_local_approval_gate() -> None:
    from demo.web.serve import main

    with pytest.raises(SystemExit):
        main(["--nms", "--approval-gate"])
