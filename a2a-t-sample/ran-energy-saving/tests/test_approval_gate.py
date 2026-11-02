"""Tests of the human approval gate in front of a network change."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

import pytest
from demo import ran_data
from demo.agents.server_agent.langgraph_tools import (
    APPROVAL_DENIED,
    PlanGuard,
    build_execution_tools,
)
from protocol.approval_gate import ApprovalRegistry


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
