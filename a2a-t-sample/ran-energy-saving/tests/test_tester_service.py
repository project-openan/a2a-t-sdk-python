"""Unit tests of the tester batch service: stage model, state and rerun identity."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from demo.web.tester.service import (
    ALL_STAGES,
    CASE_CANCELLED,
    CASE_PARTIAL,
    CASE_PASSED,
    DECISION_MODE_LIVE,
    STAGE_ERROR,
    STAGE_FAILED,
    STAGE_PASSED,
    STAGE_RECORDED,
    STAGE_SKIPPED,
    STAGE_WAITING,
    CaseItem,
    EvalBatchManager,
    StageResult,
    _apply_assertion,
)
from eval.osp.catalog import PROTOCOL_A2A, PROTOCOL_A2A_T, CaseCatalog
from eval.osp.intent_schema import OSP_INTENT_SCHEMA
from eval.osp.runner import load_cases
from protocol.client_flow import ClientFlowResult
from protocol.client_runtime import MODE_AUTO, MODE_SCRIPTED
from protocol.client_service import RunState
from shared.run_constants import (
    FINAL_STATE_INPUT_INVALID,
    FINAL_STATE_TASK_COMPLETED,
    TASK_STATE_SUBMITTED,
)

CASES: dict[int, dict[str, Any]] = {case["id"]: case for case in load_cases()}

PROTOCOL_DEFAULTS: dict[str, dict[str, str]] = {
    PROTOCOL_A2A_T: {"transport": "sse", "discovery": "direct"},
    PROTOCOL_A2A: {"transport": "sse", "discovery": "direct"},
}


class _PromptClient:
    """SDK double returning the expected Task-T and intent of each case."""

    def __init__(self) -> None:
        self._by_text = {case["user_input"]: case for case in CASES.values()}
        self._last_id: int | None = None

    def generate_task_prompt_from_text(self, text: str, template_uri: str) -> object:
        case = self._by_text[text]
        self._last_id = int(case["id"])
        return SimpleNamespace(
            prompt_text=str(case["expect"]["protocols"]["a2a-t"]["task_t"]),
            template_uri=template_uri,
        )

    def validate_task_prompt_and_data_filling(
        self,
        prompt: str,
        schema: Mapping[str, object],
        template_uri: str,
    ) -> object:
        del prompt, template_uri
        assert self._last_id is not None
        if schema is OSP_INTENT_SCHEMA:
            return SimpleNamespace(data=dict(CASES[self._last_id]["expect"]["intent"]))
        return SimpleNamespace(data={})


class _FakeRuns:
    """RunManager double recording every ``start`` call."""

    def __init__(self, builder: Callable[[RunState], None]) -> None:
        self._builder = builder
        self.calls: list[dict[str, object]] = []
        self.cancelled: list[str] = []

    async def start(
        self,
        scenario: str,
        mode: str,
        transport: str,
        discovery: str = "direct",
        user_request: str | None = None,
        **kwargs: object,
    ) -> RunState:
        self.calls.append(
            {
                "scenario": scenario,
                "mode": mode,
                "transport": transport,
                "discovery": discovery,
                "userRequest": user_request,
                **kwargs,
            }
        )
        run = RunState(
            run_id=f"run-{len(self.calls)}",
            scenario=scenario,
            mode=mode,
            transport=transport,
            discovery=discovery,
        )
        self._builder(run)
        return run

    async def cancel(self, run_id: str) -> bool:
        self.cancelled.append(run_id)
        return True


def _finish(run: RunState) -> None:
    """Make one fake run look like a completed Task."""
    run.status = "finished"
    run.result = ClientFlowResult(
        final_state=FINAL_STATE_TASK_COMPLETED,
        context_id="ctx-1",
        task_id="task-1",
    )
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = "TASK_STATE_COMPLETED"
    run.traces.append({"kind": "message_sent"})


def _hang(run: RunState) -> None:
    """Leave one fake run running forever; the tester must not mark it passed on cancel."""
    run.status = "running"


def _manager(
    tmp_path: Path,
    runs: _FakeRuns,
    *,
    model_mode: str = "offline",
) -> EvalBatchManager:
    return EvalBatchManager(
        runs=runs,  # type: ignore[arg-type]
        catalog=CaseCatalog(),
        protocol_defaults=PROTOCOL_DEFAULTS,
        server_url=None,
        server_auth_token=None,
        runs_dir=tmp_path,
        language="en-US",
        model_mode=model_mode,
        prompt_client=_PromptClient(),
    )


async def _wait_status(
    manager: EvalBatchManager, batch_id: str, expected: str, timeout: float = 10.0
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        snapshot = manager.get(batch_id)
        assert snapshot is not None
        if snapshot["status"] == expected:
            return snapshot
        await asyncio.sleep(0.02)
    raise AssertionError(f"batch {batch_id} did not reach {expected}")


def test_all_stages_include_accepted_and_working() -> None:
    assert ALL_STAGES == (
        "intent",
        "task_t",
        "validate",
        "send",
        "negotiate",
        "accepted",
        "working",
        "final",
    )


async def test_accepted_passes_when_a_task_exists(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = TASK_STATE_SUBMITTED
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A_T)

    result = await manager._judge_accepted(
        run, item, CASES[1], True, timeout_seconds=1.0
    )

    assert result.status == STAGE_PASSED
    assert result.actual == {
        "taskId": "task-1",
        "contextId": "ctx-1",
        "state": TASK_STATE_SUBMITTED,
    }


async def test_accepted_fails_fast_when_the_run_ended_before_sending(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.status = "finished"
    run.result = ClientFlowResult(final_state=FINAL_STATE_INPUT_INVALID)
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A_T)

    result = await manager._judge_accepted(
        run, item, CASES[1], True, timeout_seconds=30.0
    )

    assert result.status == STAGE_FAILED
    assert FINAL_STATE_INPUT_INVALID in result.message


async def test_accepted_is_skipped_when_the_case_expects_no_send(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    item = CaseItem(case_id=20, name="case", protocol=PROTOCOL_A2A_T)

    result = await manager._judge_accepted(
        run, item, CASES[20], False, timeout_seconds=1.0
    )

    assert result.status == STAGE_SKIPPED
    assert result.message == "用例期望不发送"


async def test_working_passes_on_working_or_completed(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = "TASK_STATE_WORKING"
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A_T)

    result = await manager._judge_working(
        run, item, CASES[1], True, working_timeout_seconds=1.0
    )

    assert result.status == STAGE_PASSED


async def test_working_is_waiting_while_the_task_stays_submitted(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_hang))
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = TASK_STATE_SUBMITTED
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A_T)

    result = await manager._judge_working(
        run, item, CASES[1], True, working_timeout_seconds=1.0
    )

    assert result.status == STAGE_WAITING
    assert "SUBMITTED" in result.message


async def test_working_is_waiting_while_input_required(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_hang))
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = "TASK_STATE_INPUT_REQUIRED"
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A_T)

    result = await manager._judge_working(
        run, item, CASES[1], True, working_timeout_seconds=1.0
    )

    assert result.status == STAGE_WAITING
    assert "INPUT_REQUIRED" in result.message


def test_a_disabled_assertion_is_recorded_but_keeps_the_evidence() -> None:
    case = {"assert": {"a2a-t": {"task_t": False}}}
    result = StageResult(
        "task_t",
        STAGE_FAILED,
        expected="expected",
        actual="actual",
        diffs=[{"path": "Task Object", "expected": "A", "actual": "B"}],
    )

    applied = _apply_assertion(case, PROTOCOL_A2A_T, "task_t", result)

    assert applied.status == STAGE_RECORDED
    assert applied.message == "用例关闭了该断言。"
    assert applied.expected == "expected"
    assert applied.diffs


async def test_an_a2a_item_has_no_sdk_stages(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A],
        stop_after="final",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    finished = await _wait_status(manager, str(created["batchId"]), "finished")

    item = finished["items"][0]
    assert isinstance(item, Mapping)
    stages = item["stages"]
    assert isinstance(stages, Mapping)
    for stage in ("intent", "task_t", "validate"):
        assert stage not in stages
    assert stages["accepted"]["status"] == STAGE_RECORDED
    assert stages["working"]["status"] == STAGE_RECORDED


def test_a2a_with_an_sdk_only_stop_stage_is_rejected(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))

    with pytest.raises(ValueError, match="SDK 阶段"):
        manager.create(
            case_ids=[1],
            protocols=[PROTOCOL_A2A],
            stop_after="validate",
            approval=None,
            timeout_seconds=2,
        )


async def test_the_case_runner_block_drives_the_run(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    manager._catalog.get(1)["runner"] = {
        "scenario": "runner-scenario",
        "language": "zh-CN",
        "approval": "reject",
        "timeout_seconds": 7,
        "pre_send_validation": "off",
    }
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=None,
    )
    batch = manager._batches[str(created["batchId"])]

    await manager.start(str(created["batchId"]))
    await _wait_status(manager, str(created["batchId"]), "finished")

    runs = manager._runs
    assert isinstance(runs, _FakeRuns)
    call = runs.calls[0]
    assert call["scenario"] == "runner-scenario"
    assert call["language"] == "zh-CN"
    assert call["validate_before_send"] is False
    case = manager._catalog.get(1)
    assert case is not None
    assert manager._resolved_timeout(batch, case) == 7
    assert manager._resolved_approval(batch, case) == "reject"


async def test_live_decision_mode_is_rejected_offline(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish), model_mode="offline")

    with pytest.raises(ValueError, match="在线模型"):
        manager.create(
            case_ids=[1],
            protocols=[PROTOCOL_A2A_T],
            stop_after="final",
            approval=None,
            timeout_seconds=2,
            decision_mode=DECISION_MODE_LIVE,
        )


async def test_live_decision_mode_uses_auto_for_a2a_t(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish), model_mode="live")
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=2,
        decision_mode=DECISION_MODE_LIVE,
    )

    await manager.start(str(created["batchId"]))
    await _wait_status(manager, str(created["batchId"]), "finished")

    runs = manager._runs
    assert isinstance(runs, _FakeRuns)
    assert runs.calls[0]["mode"] == MODE_AUTO
    assert manager.get(str(created["batchId"]))["decisionMode"] == DECISION_MODE_LIVE


async def test_cancel_marks_the_item_cancelled_not_passed(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_hang))
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=30,
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    await asyncio.sleep(0.05)

    assert await manager.cancel(batch_id)
    snapshot = await _wait_status(manager, batch_id, "cancelled")
    item = snapshot["items"][0]
    assert isinstance(item, Mapping)
    assert item["status"] == CASE_CANCELLED
    assert snapshot["summary"]["passed"] == 0


async def test_load_restores_the_persisted_stages(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=2,
    )
    batch_id = str(created["batchId"])
    batch = manager._batches[batch_id]
    item = batch.items[0]
    item.status = CASE_PASSED
    item.stages["accepted"] = StageResult(
        "accepted",
        STAGE_PASSED,
        expected={"taskId": "非空"},
        actual={"taskId": "task-1"},
        diffs=[{"path": "taskId", "expected": "x", "actual": "y"}],
        duration_ms=12,
    )
    manager._persist(batch)

    reloaded = _manager(tmp_path, _FakeRuns(_finish)).get(batch_id)

    assert reloaded is not None
    reloaded_item = reloaded["items"][0]
    assert isinstance(reloaded_item, Mapping)
    stage = reloaded_item["stages"]["accepted"]
    assert stage["status"] == STAGE_PASSED
    assert stage["expected"] == {"taskId": "非空"}
    assert stage["actual"] == {"taskId": "task-1"}
    assert stage["diffs"] == [{"path": "taskId", "expected": "x", "actual": "y"}]
    assert stage["durationMs"] == 12


async def test_rerun_uses_a_new_run_identity(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=2,
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    first = await _wait_status(manager, batch_id, "finished")
    first_run_id = first["items"][0]["runId"]

    await manager.rerun_item(batch_id, 0)
    second = await _wait_status(manager, batch_id, "finished")

    runs = manager._runs
    assert isinstance(runs, _FakeRuns)
    assert runs.calls[0]["idempotency_key"] == f"tester-{batch_id}-0"
    assert runs.calls[1]["idempotency_key"] == f"tester-{batch_id}-0-r1"
    assert runs.calls[1]["conversation_id"] == f"tester-{batch_id}-0-r1"
    item = second["items"][0]
    assert item["runId"] != first_run_id
    assert item["attempts"][0]["runId"] == first_run_id


async def test_rerun_is_rejected_while_running(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_hang))
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=30,
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    await asyncio.sleep(0.02)

    with pytest.raises(ValueError, match="执行中"):
        await manager.rerun_item(batch_id, 0)

    task = manager._batches[batch_id].task
    await manager.cancel(batch_id)
    if task is not None:
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_partial_status_counts_waiting_stages(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=2,
    )
    batch = manager._batches[str(created["batchId"])]
    batch.items[0].status = CASE_PARTIAL
    batch.items[0].stages["working"] = StageResult("working", STAGE_WAITING)

    snapshot = manager._snapshot(batch)

    assert snapshot["summary"]["partial"] == 1
    assert snapshot["summary"]["stageWaiting"] == 1


async def test_working_timeout_defaults_to_sixty_seconds(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=2,
    )
    batch = manager._batches[str(created["batchId"])]

    assert manager._resolved_working_timeout(batch) == 60
    assert created["workingTimeoutSeconds"] is None


async def test_a_persisted_run_can_still_be_reported_as_error(tmp_path: Path) -> None:
    """A run whose stage errored keeps its error string after a reload."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=2,
    )
    batch = manager._batches[str(created["batchId"])]
    batch.items[0].status = "error"
    batch.items[0].error = "boom"
    batch.items[0].stages["send"] = StageResult("send", STAGE_ERROR, "boom")
    manager._persist(batch)

    reloaded = _manager(tmp_path, _FakeRuns(_finish)).get(str(created["batchId"]))

    assert reloaded is not None
    item = reloaded["items"][0]
    assert item["status"] == "error"
    assert item["error"] == "boom"
    assert item["stages"]["send"]["status"] == STAGE_ERROR


async def test_a_negotiation_cases_task_t_is_recorded_not_asserted(tmp_path: Path) -> None:
    """The YAML Task-T is the post-negotiation value; the SDK runner only sees the request."""
    runs = _FakeRuns(_finish)
    manager = _manager(tmp_path, runs)
    created = manager.create(
        case_ids=[32],
        protocols=[PROTOCOL_A2A_T],
        stop_after="validate",
        approval=None,
        timeout_seconds=5,
    )
    await manager.start(str(created["batchId"]))
    snapshot = await _wait_status(manager, str(created["batchId"]), "finished")

    stages = snapshot["items"][0]["stages"]
    assert stages["task_t"]["status"] == STAGE_RECORDED
    assert "协商后" in stages["task_t"]["message"]


def test_the_modify_baseline_uses_the_configured_region_and_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Region A`` is not served; the baseline must name the NMS region and its exact window."""
    from demo.web.tester.service import _baseline_request

    monkeypatch.setenv("A2AT_SAMPLE_NMS_REGION", "OtherRegion")
    monkeypatch.setenv("A2AT_SAMPLE_NMS_SIM_WINDOW", "08:00~10:00")

    request = _baseline_request()

    assert "OtherRegion" in request
    assert "{08:00~10:00,10Mbps}" in request
    assert "Region A" not in request


def test_an_item_whose_failure_was_recorded_is_partial(tmp_path: Path) -> None:
    """A recorded red stage must not turn into a green row just because no stage stayed red."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    item = CaseItem(
        case_id=32,
        name="x",
        protocol=PROTOCOL_A2A,
        status="running",
        error="RuntimeError: script exhausted",
        stages={"send": StageResult("send", STAGE_PASSED)},
    )

    assert manager._item_status(item) == CASE_PARTIAL
