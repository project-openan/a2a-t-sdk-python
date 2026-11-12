"""Unit tests of the tester batch service: stage model, state and rerun identity."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import ContentValidationError
from demo.web.tester.service import (
    ALL_STAGES,
    CASE_CANCELLED,
    CASE_ERROR,
    CASE_FAILED,
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
    _Batch,
    _pairs,
)
from eval.osp.catalog import PROTOCOL_A2A, PROTOCOL_A2A_T, CaseCatalog
from eval.osp.followup import followup_input
from eval.osp.intent_schema import OSP_INTENT_SCHEMA
from eval.osp.runner import load_cases
from protocol.client_flow import ClientFlowResult
from protocol.client_runtime import MODE_AUTO, MODE_SCRIPTED
from protocol.client_service import RunState
from shared.run_constants import (
    FINAL_STATE_INPUT_INVALID,
    FINAL_STATE_TASK_COMPLETED,
    FINAL_STATE_TASK_FAILED,
    PROTOCOL_PURE,
    TASK_STATE_SUBMITTED,
)

CASES: dict[int, dict[str, Any]] = {case["id"]: case for case in load_cases()}

PROTOCOL_DEFAULTS: dict[str, dict[str, str]] = {
    PROTOCOL_A2A_T: {"transport": "sse", "discovery": "direct"},
    PROTOCOL_A2A: {"transport": "sse", "discovery": "direct"},
}
DIRECT_PURE_DEFAULTS: dict[str, dict[str, str]] = {
    PROTOCOL_A2A_T: {"transport": "webhook", "discovery": "nms"},
    PROTOCOL_PURE: {"transport": "webhook", "discovery": "nms"},
}


class _PromptClient:
    """SDK double returning the expected Task-T and intent of each case."""

    def __init__(self) -> None:
        self._by_text = {case["user_input"]: case for case in CASES.values()}
        for case in CASES.values():
            text = followup_input(case)
            if text is not None:
                self._by_text[text] = case
        self._last_id: int | None = None
        self._last_text = ""
        #: Extra operator texts whose runtime validation must report ``blocked``.
        self.blocked_texts: set[str] = set()

    def generate_task_prompt_from_text(self, text: str, template_uri: str) -> object:
        case = self._by_text[text]
        self._last_id = int(case["id"])
        self._last_text = text
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
        case = CASES[self._last_id]
        blocked_case = case["expect"]["protocols"]["a2a-t"]["validate"] == "blocked"
        original_input = self._last_text == str(case.get("user_input"))
        if self._last_text in self.blocked_texts or (blocked_case and original_input):
            raise ContentValidationError(
                ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED,
                {"reason": "test"},
                message="blocked by test",
            )
        return SimpleNamespace(data={})


class _FakeRuns:
    """RunManager double recording every ``start`` call."""

    def __init__(self, builder: Callable[[RunState], None]) -> None:
        self._builder = builder
        self.calls: list[dict[str, object]] = []
        self.cancelled: list[str] = []
        self.runs: dict[str, RunState] = {}

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
        self.runs[run.run_id] = run
        self._builder(run)
        return run

    def get(self, run_id: str) -> RunState | None:
        return self.runs.get(run_id)

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


def _working(run: RunState) -> None:
    """Leave one fake run accepted and working, as a long energy-saving Task stays."""
    run.status = "running"
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = "TASK_STATE_WORKING"
    run.traces.append({"kind": "message_sent"})


def _manager(
    tmp_path: Path,
    runs: _FakeRuns,
    *,
    model_mode: str = "offline",
    nms_sim: bool = False,
    nms_sim_pure_question: str = "issue",
    protocol_defaults: dict[str, dict[str, str]] | None = None,
) -> EvalBatchManager:
    return EvalBatchManager(
        runs=runs,  # type: ignore[arg-type]
        catalog=CaseCatalog(),
        protocol_defaults=protocol_defaults or PROTOCOL_DEFAULTS,
        server_url=None,
        server_auth_token=None,
        runs_dir=tmp_path,
        nms_sim=nms_sim,
        nms_sim_pure_question=nms_sim_pure_question,
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


async def test_accepted_waits_for_a_success_state_not_identity_alone(tmp_path: Path) -> None:
    """An INPUT_REQUIRED Task carries identity but the negotiation is not finished."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = "TASK_STATE_INPUT_REQUIRED"
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A_T)

    result = await manager._judge_accepted(
        run, item, CASES[1], True, timeout_seconds=0.2
    )

    assert result.status == STAGE_WAITING


async def test_pure_accepted_is_judged_by_the_outer_task_state(tmp_path: Path) -> None:
    """Pure A2A acceptance is the outer Task's first successful state, not a recording."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = "TASK_STATE_WORKING"
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A)

    result = await manager._judge_accepted(
        run, item, CASES[1], True, timeout_seconds=0.2
    )

    assert result.status == STAGE_PASSED


async def test_negotiation_without_expected_rounds_ends_on_the_first_response(
    tmp_path: Path,
) -> None:
    """A no-negotiation case must not wait for the long-task run to finish."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.status = "running"
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = TASK_STATE_SUBMITTED

    started = time.monotonic()
    await manager._wait_for_negotiation(run, 0, timeout_seconds=5.0)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0


async def test_negotiation_without_expected_rounds_ends_on_an_unexpected_proposal(
    tmp_path: Path,
) -> None:
    """An unexpected proposal is the first response too: record it instead of stalling."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.status = "running"
    run.proposals.append(SimpleNamespace())  # type: ignore[arg-type]

    started = time.monotonic()
    await manager._wait_for_negotiation(run, 0, timeout_seconds=5.0)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0


async def test_pure_replay_mode_asserts_the_osp_round_count(tmp_path: Path) -> None:
    """With the vendor dialog replay, one recorded proposal is the case's expected round."""
    manager = _manager(
        tmp_path, _FakeRuns(_finish), nms_sim=True, nms_sim_pure_question="confirm"
    )
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.status = "running"
    run.proposals.append(SimpleNamespace(kind="feasibility"))  # type: ignore[arg-type]
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A)
    batch = _Batch(
        batch_id="batch",
        case_ids=[1],
        protocols=[PROTOCOL_A2A],
        stop_after="negotiate",
        approval=None,
        timeout_seconds=1.0,
        items=[item],
        decision_mode=DECISION_MODE_LIVE,
    )

    await manager._wait_for_stage(
        run,
        item,
        batch,
        ("negotiate",),
        case=CASES[1],
        timeout_seconds=1.0,
        working_timeout_seconds=1.0,
    )

    assert item.stages["negotiate"].status == STAGE_PASSED


async def test_pure_issue_mode_still_records_the_round_count(tmp_path: Path) -> None:
    """The historical issue renderer cannot reproduce the workbook dialogue, so it only records."""
    manager = _manager(tmp_path, _FakeRuns(_finish), nms_sim=True)
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.status = "running"
    run.proposals.append(SimpleNamespace(kind="feasibility"))  # type: ignore[arg-type]
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A)
    batch = _Batch(
        batch_id="batch",
        case_ids=[1],
        protocols=[PROTOCOL_A2A],
        stop_after="negotiate",
        approval=None,
        timeout_seconds=1.0,
        items=[item],
    )

    await manager._wait_for_stage(
        run,
        item,
        batch,
        ("negotiate",),
        case=CASES[1],
        timeout_seconds=1.0,
        working_timeout_seconds=1.0,
    )

    assert item.stages["negotiate"].status == STAGE_RECORDED


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


async def test_pure_working_is_judged_by_the_outer_task_state(tmp_path: Path) -> None:
    """A pure item is green only when the outer Task was accepted and is running."""
    manager = _manager(tmp_path, _FakeRuns(_hang))
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = "TASK_STATE_WORKING"
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A)

    result = await manager._judge_working(
        run, item, CASES[1], True, working_timeout_seconds=1.0
    )

    assert result.status == STAGE_PASSED


async def test_pure_working_waits_while_the_outer_task_is_input_required(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_hang))
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = "TASK_STATE_INPUT_REQUIRED"
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A)

    result = await manager._judge_working(
        run, item, CASES[1], True, working_timeout_seconds=1.0
    )

    assert result.status == STAGE_WAITING


async def test_working_fails_on_a_refused_completed_task(tmp_path: Path) -> None:
    """The vendor signals a refusal as COMPLETED; the run layer downgrades it to task_failed."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = "TASK_STATE_COMPLETED"
    run.status = "finished"
    run.result = ClientFlowResult(
        final_state="task_failed", context_id="ctx-1", task_id="task-1"
    )
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A_T)

    result = await manager._judge_working(
        run, item, CASES[1], True, working_timeout_seconds=1.0
    )

    assert result.status == STAGE_FAILED
    assert "task_failed" in result.message


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
    assert stages["accepted"]["status"] == STAGE_PASSED
    assert stages["working"]["status"] == STAGE_PASSED


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
    assert "10Mbps from 08:00 to 10:00" in request
    assert "Region A" not in request
    # The served window replaces the day plan instead of being inserted next to it: two segments
    # covering the same hours would describe an ambiguous prior task.
    assert "07:00 to 18:00" not in request


def test_the_modify_baseline_is_a_one_day_region_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default fixture covers the day so a Modify's "keep the rest unchanged" has a rest."""
    from demo.web.tester.service import _baseline_request

    monkeypatch.delenv("A2AT_SAMPLE_NMS_SIM_WINDOW", raising=False)
    monkeypatch.setenv("A2AT_SAMPLE_NMS_REGION", "OtherRegion")

    request = _baseline_request()

    assert "Create an energy saving task for OtherRegion" in request
    assert "2Mbps from 00:00 to 07:00" in request
    assert "20Mbps from 18:00 to 24:00" in request


def test_a_case_can_override_the_paired_baseline() -> None:
    """A row whose prior task is not the shared fixture states its own paired Create."""
    from demo.web.tester.service import _baseline_request

    request = _baseline_request({"baseline": {"request": "Create the prior task."}})

    assert request == "Create the prior task."


def _seed_then_modify(seed_state: str) -> Callable[[RunState], None]:
    """Build a fake RunManager that reports the paired Create, then the Modify run."""
    calls = {"count": 0}

    def build(run: RunState) -> None:
        calls["count"] += 1
        index = calls["count"]
        run.remote_task_id = f"task-{index}"
        run.remote_context_id = f"ctx-{index}"
        if index == 1:
            run.latest_task_state = seed_state
            # The proxy hop only proves the downstream accepted the Create through its binding.
            if seed_state in {TASK_STATE_SUBMITTED, "TASK_STATE_WORKING"}:
                run.downstream_task_id = "inner-1"
            run.status = (
                "finished" if seed_state in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED"} else "running"
            )
            return
        run.latest_task_state = "TASK_STATE_WORKING"
        run.status = "running"
        run.traces.append({"kind": "message_sent"})

    return build


async def test_an_a2a_t_modify_continues_the_task_it_just_created(tmp_path: Path) -> None:
    """The Modify must carry the baseline runner's Task id, not pick a Task on its own."""
    runs = _FakeRuns(_seed_then_modify(TASK_STATE_SUBMITTED))
    manager = _manager(tmp_path, runs)
    created = manager.create(
        case_ids=[18],
        protocols=[PROTOCOL_A2A_T],
        stop_after="send",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    snapshot = await _wait_status(manager, str(created["batchId"]), "finished")

    item = snapshot["items"][0]
    assert isinstance(item, Mapping)
    baseline = item["baseline"]
    assert isinstance(baseline, Mapping)
    assert baseline["status"] == "accepted"
    assert baseline["taskId"] == "task-1"
    assert baseline["state"] == TASK_STATE_SUBMITTED
    assert runs.calls[0]["protocol"] == PROTOCOL_A2A_T
    assert runs.calls[1]["target_task_id"] == "task-1"
    assert runs.calls[1]["target_context_id"] == "ctx-1"
    assert runs.calls[1]["target_server_task_id"] is None


async def test_a_live_a2a_t_modify_baseline_uses_the_live_decision_provider(
    tmp_path: Path,
) -> None:
    """A live batch must not seed the baseline with the empty scripted decision script.

    The baseline is a normal Create, but the server may still ask a question (INPUT_REQUIRED).
    ``MODE_SCRIPTED`` with no scripted decisions then fails with "The scripted decision sequence
    is exhausted" before the Modify is ever sent.
    """
    runs = _FakeRuns(_seed_then_modify(TASK_STATE_SUBMITTED))
    manager = _manager(tmp_path, runs, model_mode="live")
    created = manager.create(
        case_ids=[18],
        protocols=[PROTOCOL_A2A_T],
        stop_after="send",
        approval=None,
        timeout_seconds=2,
        decision_mode=DECISION_MODE_LIVE,
    )

    await manager.start(str(created["batchId"]))
    await _wait_status(manager, str(created["batchId"]), "finished")

    assert runs.calls[0]["mode"] == MODE_AUTO
    assert runs.calls[0]["scripted_decisions"] == ()


async def test_a_negotiating_baseline_is_recorded_as_failed_with_its_run(tmp_path: Path) -> None:
    """A baseline that opens a negotiation is not continuable; the export must still name it."""

    def _negotiating(run: RunState) -> None:
        run.remote_task_id = "task-1"
        run.remote_context_id = "ctx-1"
        run.latest_task_state = "TASK_STATE_INPUT_REQUIRED"
        run.status = "finished"

    runs = _FakeRuns(_negotiating)
    manager = _manager(tmp_path, runs)
    created = manager.create(
        case_ids=[18],
        protocols=[PROTOCOL_A2A_T],
        stop_after="send",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    snapshot = await _wait_status(manager, str(created["batchId"]), "finished")

    item = snapshot["items"][0]
    baseline = item["baseline"]
    assert isinstance(baseline, Mapping)
    assert item["status"] == CASE_ERROR
    assert baseline["status"] == "failed"
    assert baseline["runId"] == "run-1"
    assert baseline["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert "协商" in str(baseline["error"])
    assert "协商" in str(item["error"])


async def test_a_pure_modify_continues_the_outer_task_it_just_created(tmp_path: Path) -> None:
    """Pure A2A continues the OSS Agent's outer Task, not a new message on a new Task."""
    runs = _FakeRuns(_seed_then_modify("TASK_STATE_WORKING"))
    manager = _manager(tmp_path, runs)
    created = manager.create(
        case_ids=[18],
        protocols=[PROTOCOL_A2A],
        stop_after="send",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    snapshot = await _wait_status(manager, str(created["batchId"]), "finished")

    item = snapshot["items"][0]
    assert isinstance(item, Mapping)
    baseline = item["baseline"]
    assert isinstance(baseline, Mapping)
    assert baseline["protocol"] == "pure"
    assert baseline["status"] == "accepted"
    assert baseline["taskId"] == "task-1"
    assert runs.calls[0]["protocol"] == "pure"
    assert runs.calls[1]["protocol"] == "pure"
    assert runs.calls[1]["target_task_id"] is None
    assert runs.calls[1]["target_server_task_id"] == "task-1"
    assert runs.calls[1]["target_server_context_id"] == "ctx-1"


def _seed_then_fail_downstream(run: RunState) -> None:
    """A proxy baseline that published outer WORKING, then failed downstream (NMS HTTP 500)."""
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = "TASK_STATE_WORKING"
    run.status = "finished"
    run.error = (
        "NMS A2A request returned HTTP 500: Running task limit reached, please try again later."
    )
    run.result = ClientFlowResult(final_state=FINAL_STATE_TASK_FAILED)


async def test_a_proxy_baseline_that_failed_downstream_never_becomes_the_modify_target(
    tmp_path: Path,
) -> None:
    """The outer Task was WORKING, but the NMS Create failed; the Modify must not be launched."""
    runs = _FakeRuns(_seed_then_fail_downstream)
    manager = _manager(tmp_path, runs)
    created = manager.create(
        case_ids=[18],
        protocols=[PROTOCOL_A2A],
        stop_after="send",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    snapshot = await _wait_status(manager, str(created["batchId"]), "finished")

    item = snapshot["items"][0]
    assert isinstance(item, Mapping)
    assert item["status"] == CASE_ERROR
    assert "Running task limit" in str(item["error"])
    baseline = item["baseline"]
    assert isinstance(baseline, Mapping)
    assert baseline["status"] == "failed"
    assert "Running task limit" in str(baseline["error"])
    assert len(runs.calls) == 1


async def test_a_direct_pure_modify_continues_the_vendor_task(tmp_path: Path) -> None:
    """On the direct hop the baseline Task is the vendor's own, so the pair goes to target."""
    runs = _FakeRuns(_seed_then_modify(TASK_STATE_SUBMITTED))
    manager = _manager(tmp_path, runs, protocol_defaults=DIRECT_PURE_DEFAULTS)
    created = manager.create(
        case_ids=[18],
        protocols=[PROTOCOL_A2A],
        stop_after="send",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    snapshot = await _wait_status(manager, str(created["batchId"]), "finished")

    item = snapshot["items"][0]
    assert isinstance(item, Mapping)
    baseline = item["baseline"]
    assert isinstance(baseline, Mapping)
    assert baseline["status"] == "accepted"
    assert baseline["taskId"] == "task-1"
    assert runs.calls[1]["discovery"] == "nms"
    assert runs.calls[1]["target_task_id"] == "task-1"
    assert runs.calls[1]["target_server_task_id"] is None


async def test_a_proxy_pure_modify_still_waits_for_the_outer_working_state(tmp_path: Path) -> None:
    """The proxy hop owns the outer Task, which is only continuable while WORKING."""
    runs = _FakeRuns(_seed_then_modify(TASK_STATE_SUBMITTED))
    manager = _manager(tmp_path, runs)
    created = manager.create(
        case_ids=[18],
        protocols=[PROTOCOL_A2A],
        stop_after="send",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    snapshot = await _wait_status(manager, str(created["batchId"]), "finished")

    item = snapshot["items"][0]
    assert isinstance(item, Mapping)
    assert item["status"] == CASE_ERROR
    assert len(runs.calls) == 1


async def test_a_pure_modify_refuses_a_terminal_baseline_task(tmp_path: Path) -> None:
    """A2A rejects a message addressed to a terminal Task; the tester must say so, not modify."""
    runs = _FakeRuns(_seed_then_modify("TASK_STATE_COMPLETED"))
    manager = _manager(tmp_path, runs)
    created = manager.create(
        case_ids=[18],
        protocols=[PROTOCOL_A2A],
        stop_after="send",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    snapshot = await _wait_status(manager, str(created["batchId"]), "finished")

    item = snapshot["items"][0]
    assert isinstance(item, Mapping)
    assert item["status"] == CASE_ERROR
    assert "WORKING" in str(item["error"])
    assert len(runs.calls) == 1
    stages = item["stages"]
    assert isinstance(stages, Mapping)
    assert stages["send"]["status"] == STAGE_ERROR


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


async def test_a_validate_only_batch_freezes_the_case_reference_and_intent(
    tmp_path: Path,
) -> None:
    """The arbitration sheet must carry the intent even when no run was started."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="validate",
        approval=None,
        timeout_seconds=None,
    )
    batch_id = str(created["batchId"])

    await manager.start(batch_id)
    await _wait_status(manager, batch_id, "finished")

    item = manager._batches[batch_id].items[0]
    assert item.trace_id == ""
    assert item.answer is None
    assert item.refs["caseName"] == CASES[1]["name"]
    assert str(item.refs["taskT"]).startswith("## Operation Type")
    assert item.refs["expectedIntent"] == CASES[1]["expect"]["intent"]
    assert item.intent == CASES[1]["expect"]["intent"]
    snapshot = manager.get(batch_id)
    assert snapshot is not None
    assert snapshot["items"][0]["intent"]["Operation type"] == "Create"


async def test_a_full_run_records_the_trace_id_and_answer(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=None,
    )
    batch_id = str(created["batchId"])

    await manager.start(batch_id)
    await _wait_status(manager, batch_id, "finished")

    item = manager._batches[batch_id].items[0]
    assert item.trace_id == "run-1"
    assert item.answer is not None
    assert item.answer["code"] == "SUCCESS"
    assert item.answer["taskId"] == "task-1"
    data = item.to_dict()
    assert data["traceId"] == "run-1"
    assert data["answer"] == item.answer


async def _run_item_with_server_intent(
    tmp_path: Path,
    payload: Mapping[str, object] | None,
    *,
    nms_sim: bool = False,
    protocol: str = PROTOCOL_A2A_T,
    protocol_defaults: dict[str, dict[str, str]] | None = None,
) -> CaseItem:
    """Run case 1 to ``final`` against a fake run carrying one downstream payload."""

    def builder(run: RunState) -> None:
        _finish(run)
        run.server_intent = dict(payload) if payload is not None else None

    manager = _manager(
        tmp_path,
        _FakeRuns(builder),
        nms_sim=nms_sim,
        protocol_defaults=protocol_defaults,
    )
    created = manager.create(
        case_ids=[1],
        protocols=[protocol],
        stop_after="final",
        approval=None,
        timeout_seconds=None,
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    await _wait_status(manager, batch_id, "finished")
    return manager._batches[batch_id].items[0]


async def test_the_intent_stage_judges_the_downstream_payload_not_the_sdk_extraction(
    tmp_path: Path,
) -> None:
    """The SDK extraction matches the case, but the server understood something else."""
    item = await _run_item_with_server_intent(
        tmp_path,
        {"Operation type": "Modify", "Energy saving region": "OtherRegion"},
    )

    stage = item.stages["intent"]
    assert stage.status == STAGE_FAILED
    assert stage.actual == {"Operation type": "Modify", "Energy saving region": "OtherRegion"}
    assert any(diff["path"] == "intent.Energy saving region" for diff in stage.diffs)
    # The SDK extraction stays available for the auxiliary export column.
    assert item.intent == CASES[1]["expect"]["intent"]
    assert item.server_intent == {"Operation type": "Modify", "Energy saving region": "OtherRegion"}
    assert item.intent_meta["source"] == "server"
    assert item.status == "failed"


async def test_a_matching_downstream_payload_passes_the_intent_stage(tmp_path: Path) -> None:
    item = await _run_item_with_server_intent(tmp_path, CASES[1]["expect"]["intent"])

    stage = item.stages["intent"]
    assert stage.status == STAGE_PASSED
    assert stage.diffs == []
    assert item.server_intent == CASES[1]["expect"]["intent"]
    assert item.to_dict()["serverIntent"] == CASES[1]["expect"]["intent"]


async def test_a_downstream_that_returns_no_payload_records_the_intent_stage(
    tmp_path: Path,
) -> None:
    """Our own RAN Agent answers SUBMITTED without a payload; that is not a red result."""
    item = await _run_item_with_server_intent(tmp_path, None)

    stage = item.stages["intent"]
    assert stage.status == STAGE_RECORDED
    assert item.server_intent is None
    assert item.status == "passed"


async def test_the_simulator_payload_is_recorded_not_judged(tmp_path: Path) -> None:
    """The simulator answers a reduced key set; comparing it would be a false failure."""
    item = await _run_item_with_server_intent(
        tmp_path,
        {"Operation type": "Create", "Energy saving region": "A2ATestRegion"},
        nms_sim=True,
    )

    assert item.stages["intent"].status == STAGE_RECORDED
    assert item.server_intent == {
        "Operation type": "Create",
        "Energy saving region": "A2ATestRegion",
    }


async def test_a_direct_pure_a2a_run_judges_the_vendor_intent(tmp_path: Path) -> None:
    item = await _run_item_with_server_intent(
        tmp_path,
        CASES[1]["expect"]["intent"],
        protocol=PROTOCOL_A2A,
        protocol_defaults=DIRECT_PURE_DEFAULTS,
    )

    stage = item.stages["intent"]
    assert stage.status == STAGE_PASSED
    assert stage.actual == CASES[1]["expect"]["intent"]
    assert stage.diffs == []


async def test_a_direct_pure_a2a_intent_mismatch_fails_the_stage(tmp_path: Path) -> None:
    payload = dict(CASES[1]["expect"]["intent"])
    payload["Energy saving region"] = "OtherRegion"
    item = await _run_item_with_server_intent(
        tmp_path,
        payload,
        protocol=PROTOCOL_A2A,
        protocol_defaults=DIRECT_PURE_DEFAULTS,
    )

    stage = item.stages["intent"]
    assert stage.status == STAGE_FAILED
    assert any(diff["path"] == "intent.Energy saving region" for diff in stage.diffs)
    assert item.status == "failed"


async def test_a_proxy_pure_a2a_intent_is_recorded_not_judged(tmp_path: Path) -> None:
    payload = dict(CASES[1]["expect"]["intent"])
    payload["Energy saving region"] = "OtherRegion"
    item = await _run_item_with_server_intent(
        tmp_path,
        payload,
        protocol=PROTOCOL_A2A,
    )

    stage = item.stages["intent"]
    assert stage.status == STAGE_RECORDED
    assert stage.diffs


async def test_a_pure_a2a_sim_intent_is_recorded_not_judged(tmp_path: Path) -> None:
    payload = dict(CASES[1]["expect"]["intent"])
    payload["Energy saving region"] = "OtherRegion"
    item = await _run_item_with_server_intent(
        tmp_path,
        payload,
        protocol=PROTOCOL_A2A,
        protocol_defaults=DIRECT_PURE_DEFAULTS,
        nms_sim=True,
    )

    assert item.stages["intent"].status == STAGE_RECORDED


async def test_the_serialized_intent_comparison_lists_every_nested_leaf(
    tmp_path: Path,
) -> None:
    item = await _run_item_with_server_intent(tmp_path, CASES[1]["expect"]["intent"])

    rows = item.to_dict()["intentCompare"]

    paths = {row["path"] for row in rows}
    assert (
        "intent.Guaranteed throughput target by zone[0].Guaranteed throughput target"
        in paths
    )
    assert "intent.Operation type" in paths
    assert all(row["status"] == "same" for row in rows)


async def test_the_item_snapshot_carries_the_langfuse_trace_url(tmp_path: Path) -> None:
    url = "https://langfuse.example/project/p1/traces/trace-1"

    def builder(run: RunState) -> None:
        _finish(run)
        run.langfuse = {"enabled": True, "traceId": "trace-1", "url": url}

    runs = _FakeRuns(builder)
    manager = _manager(tmp_path, runs)
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=None,
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    await _wait_status(manager, batch_id, "finished")

    item = manager.get(batch_id)["items"][0]
    assert item["langfuseUrl"] == url
    assert manager._batches[batch_id].items[0].to_dict()["langfuseUrl"] == url


async def test_the_langfuse_url_is_recovered_from_the_live_run_after_the_evidence(
    tmp_path: Path,
) -> None:
    """The background URL lookup may finish after the tester collected its evidence."""
    url = "https://langfuse.example/project/p1/traces/trace-late"

    runs = _FakeRuns(_finish)
    manager = _manager(tmp_path, runs)
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=None,
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    await _wait_status(manager, batch_id, "finished")

    runs.runs["run-1"].langfuse = {"enabled": True, "traceId": "trace-late", "url": url}

    assert manager.get(batch_id)["items"][0]["langfuseUrl"] == url


async def test_a_working_item_releases_its_remote_task_after_the_evidence(
    tmp_path: Path,
) -> None:
    """The Task is accepted and WORKING; the batch then releases it instead of holding quota."""
    runs = _FakeRuns(_working)
    manager = _manager(tmp_path, runs)
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="working",
        approval=None,
        timeout_seconds=1,
    )
    batch_id = str(created["batchId"])

    await manager.start(batch_id)
    await _wait_status(manager, batch_id, "finished")

    item = manager._batches[batch_id].items[0]
    assert item.stages["working"].status == STAGE_PASSED
    assert runs.cancelled == ["run-1"]


async def test_keep_tasks_leaves_the_remote_task_running(tmp_path: Path) -> None:
    runs = _FakeRuns(_working)
    manager = _manager(tmp_path, runs)
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="working",
        approval=None,
        timeout_seconds=1,
        keep_tasks=True,
    )
    assert created["keepTasks"] is True

    await manager.start(str(created["batchId"]))
    await _wait_status(manager, str(created["batchId"]), "finished")

    assert runs.cancelled == []


async def test_releasing_an_item_covers_its_baseline_run(tmp_path: Path) -> None:
    runs = _FakeRuns(_finish)
    manager = _manager(tmp_path, runs)
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A_T)
    item.run_id = "run-1"
    item.baseline = {"runId": "run-baseline", "taskId": "task-0", "contextId": "ctx-0"}

    await manager._release_item_runs(item)

    assert runs.cancelled == ["run-1", "run-baseline"]


async def test_shutdown_releases_the_runs_of_finished_batches(tmp_path: Path) -> None:
    runs = _FakeRuns(_finish)
    manager = _manager(tmp_path, runs)
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="validate",
        approval=None,
        timeout_seconds=1,
    )
    batch = manager._batches[str(created["batchId"])]
    batch.items[0].run_id = "run-1"
    batch.status = "finished"

    await manager.shutdown()

    assert runs.cancelled == ["run-1"]


def test_the_default_decision_mode_is_live_for_an_online_model(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish), model_mode="live")

    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="validate",
        approval=None,
        timeout_seconds=None,
    )

    assert created["decisionMode"] == "live"


def test_the_default_decision_mode_stays_scripted_offline(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish), model_mode="offline")

    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="validate",
        approval=None,
        timeout_seconds=None,
    )

    assert created["decisionMode"] == "scripted"


async def test_a_rerun_clears_the_previous_arbitration_evidence(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_hang))
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=None,
    )
    batch_id = str(created["batchId"])
    item = manager._batches[batch_id].items[0]
    item.trace_id = "old-trace"
    item.intent = {"Operation type": "Create"}
    item.intent_meta = {"rawRequest": "old"}
    item.refs = {"caseName": "old"}
    item.answer = {"code": "OLD"}

    try:
        await manager.rerun_item(batch_id, 0)
        assert item.trace_id == ""
        assert item.intent is None
        assert item.intent_meta == {}
        assert item.refs == {}
        assert item.answer is None
    finally:
        await manager.cancel(batch_id)


def test_an_old_v2_batch_loads_with_empty_export_fields(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    history = tmp_path / "tester"
    history.mkdir(parents=True)
    document = {
        "schema": "osp-tester/v2",
        "batchId": "old-batch",
        "caseIds": [1],
        "protocols": [PROTOCOL_A2A_T],
        "stopAfter": "validate",
        "items": [
            {
                "caseId": 1,
                "name": "old",
                "protocol": PROTOCOL_A2A_T,
                "status": "passed",
            }
        ],
    }
    (history / "old-batch.json").write_text(json.dumps(document), encoding="utf-8")

    snapshot = manager.get("old-batch")

    assert snapshot is not None
    item = snapshot["items"][0]
    assert item["traceId"] == ""
    assert item["intent"] is None
    assert item["answer"] is None
    assert item["refs"] == {}


FOLLOWUP_INPUT = "Implement energy-saving in A2ATestRegion. Set the gain target to 30%."


def _inject_followup(manager: EvalBatchManager, case_id: int, text: str = FOLLOWUP_INPUT) -> None:
    """Give one catalog case a replacement request and teach the SDK double about it."""
    case = manager._catalog.get(case_id)
    assert case is not None
    case["blocked_followup"] = {"input": text}
    client = manager._prompt_client
    assert isinstance(client, _PromptClient)
    client._by_text[text] = case
    client.blocked_texts.add(str(case["user_input"]))


def _only_item(snapshot: Mapping[str, object]) -> Mapping[str, Any]:
    items = snapshot["items"]
    assert isinstance(items, list)
    item = items[0]
    assert isinstance(item, Mapping)
    return item


async def test_a_blocked_case_with_a_followup_records_the_interception(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    _inject_followup(manager, 22)
    created = manager.create(
        case_ids=[22],
        protocols=[PROTOCOL_A2A_T],
        stop_after="validate",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    finished = await _wait_status(manager, str(created["batchId"]), "finished")

    item = _only_item(finished)
    interception = item["interception"]
    assert isinstance(interception, Mapping)
    assert interception["input"] == str(CASES[22]["user_input"])
    assert interception["followupInput"] == FOLLOWUP_INPUT
    blocked = interception["stages"]
    assert isinstance(blocked, Mapping)
    assert blocked["validate"]["status"] == STAGE_PASSED
    assert blocked["validate"]["expected"] == "blocked"
    assert blocked["validate"]["actual"] == "blocked"
    assert blocked["intent"]["status"] == STAGE_RECORDED
    assert blocked["task_t"]["status"] == STAGE_RECORDED
    stages = item["stages"]
    assert isinstance(stages, Mapping)
    assert stages["validate"]["expected"] == "pass"
    assert stages["validate"]["status"] == STAGE_PASSED
    assert item["status"] == CASE_PASSED
    runs = manager._runs
    assert isinstance(runs, _FakeRuns)
    assert runs.calls == []


async def test_the_followup_request_is_what_the_full_flow_dispatches(tmp_path: Path) -> None:
    runs = _FakeRuns(_finish)
    manager = _manager(tmp_path, runs)
    _inject_followup(manager, 22)
    created = manager.create(
        case_ids=[22],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    finished = await _wait_status(manager, str(created["batchId"]), "finished")

    assert len(runs.calls) == 1
    assert runs.calls[0]["userRequest"] == FOLLOWUP_INPUT
    item = _only_item(finished)
    assert isinstance(item["interception"], Mapping)
    stages = item["stages"]
    assert isinstance(stages, Mapping)
    assert stages["send"]["expected"] is True
    assert stages["send"]["status"] == STAGE_PASSED
    assert stages["final"]["expected"] == "task_completed"
    assert stages["final"]["status"] == STAGE_PASSED
    assert item["status"] == CASE_PASSED
    refs = item["refs"]
    assert isinstance(refs, Mapping)
    assert refs["userInput"] == FOLLOWUP_INPUT
    assert refs["expectedFinalState"] == "task_completed"


async def test_a_blocked_case_without_a_followup_keeps_the_original_flow(tmp_path: Path) -> None:
    runs = _FakeRuns(_finish)
    manager = _manager(tmp_path, runs)
    case = manager._catalog.get(22)
    assert case is not None
    case.pop("blocked_followup", None)
    created = manager.create(
        case_ids=[22],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    finished = await _wait_status(manager, str(created["batchId"]), "finished")

    item = _only_item(finished)
    assert item["interception"] is None
    assert runs.calls[0]["userRequest"] == str(CASES[22]["user_input"])


async def test_a_followup_that_is_still_blocked_fails_the_item(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    _inject_followup(manager, 22)
    client = manager._prompt_client
    assert isinstance(client, _PromptClient)
    client.blocked_texts.add(FOLLOWUP_INPUT)
    created = manager.create(
        case_ids=[22],
        protocols=[PROTOCOL_A2A_T],
        stop_after="validate",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    finished = await _wait_status(manager, str(created["batchId"]), "finished")

    item = _only_item(finished)
    stages = item["stages"]
    assert isinstance(stages, Mapping)
    assert stages["validate"]["actual"] == "blocked"
    assert stages["validate"]["status"] == STAGE_FAILED
    assert item["status"] == "failed"


async def test_a_pure_a2a_item_ignores_a_followup(tmp_path: Path) -> None:
    runs = _FakeRuns(_finish)
    manager = _manager(tmp_path, runs)
    _inject_followup(manager, 22)
    created = manager.create(
        case_ids=[22],
        protocols=[PROTOCOL_A2A],
        stop_after="final",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    finished = await _wait_status(manager, str(created["batchId"]), "finished")

    item = _only_item(finished)
    assert item["interception"] is None
    assert runs.calls[0]["userRequest"] == str(CASES[22]["user_input"])


async def test_an_unblocked_case_ignores_a_followup(tmp_path: Path) -> None:
    runs = _FakeRuns(_finish)
    manager = _manager(tmp_path, runs)
    _inject_followup(manager, 1)
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    finished = await _wait_status(manager, str(created["batchId"]), "finished")

    item = _only_item(finished)
    assert item["interception"] is None
    assert runs.calls[0]["userRequest"] == str(CASES[1]["user_input"])


async def test_load_restores_the_interception_record(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    _inject_followup(manager, 22)
    created = manager.create(
        case_ids=[22],
        protocols=[PROTOCOL_A2A_T],
        stop_after="validate",
        approval=None,
        timeout_seconds=2,
    )
    batch_id = str(created["batchId"])

    await manager.start(batch_id)
    await _wait_status(manager, batch_id, "finished")

    reloaded = _manager(tmp_path, _FakeRuns(_finish)).get(batch_id)

    assert reloaded is not None
    item = _only_item(reloaded)
    interception = item["interception"]
    assert isinstance(interception, Mapping)
    assert interception["followupInput"] == FOLLOWUP_INPUT
    blocked = interception["stages"]
    assert isinstance(blocked, Mapping)
    assert blocked["validate"]["status"] == STAGE_PASSED


async def test_a_case_file_followup_is_used_without_test_injection(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    case = manager._catalog.get(22)
    assert case is not None
    supplement = followup_input(case)
    assert supplement is not None
    created = manager.create(
        case_ids=[22],
        protocols=[PROTOCOL_A2A_T],
        stop_after="validate",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    finished = await _wait_status(manager, str(created["batchId"]), "finished")

    item = _only_item(finished)
    interception = item["interception"]
    assert isinstance(interception, Mapping)
    assert interception["followupInput"] == supplement
    blocked = interception["stages"]
    assert isinstance(blocked, Mapping)
    assert blocked["validate"]["status"] == STAGE_PASSED
    assert item["status"] == CASE_PASSED


def test_pairs_join_the_two_protocol_rows_of_one_case() -> None:
    """The acceptance report needs the per-case A2A-T vs A2A join, not two flat rows."""
    a2at = CaseItem(case_id=7, name="case-7", protocol=PROTOCOL_A2A_T)
    a2at.status = CASE_PASSED
    a2at.duration_ms = 1200
    a2at.tokens = {"total": 900}
    a2at.stages["negotiate"] = StageResult(
        "negotiate", STAGE_PASSED, actual={"rounds": 1, "kinds": []}
    )
    pure = CaseItem(case_id=7, name="case-7", protocol=PROTOCOL_A2A)
    pure.status = CASE_FAILED
    pure.duration_ms = 4200
    pure.tokens = {"total": 300}
    pure.stages["negotiate"] = StageResult(
        "negotiate", STAGE_FAILED, actual={"rounds": 3, "kinds": []}
    )

    pairs = _pairs([a2at, pure])

    assert len(pairs) == 1
    row = pairs[0]
    assert row["caseId"] == 7
    # Every delta is (A2A-T - A2A).
    assert row["roundsDelta"] == -2.0
    assert row["durationDeltaMs"] == -3000.0
    assert row["tokensDelta"] == 600.0
    assert row["sameStatus"] is False
    assert row["protocols"][PROTOCOL_A2A_T]["rounds"] == 1
    assert row["protocols"][PROTOCOL_A2A]["rounds"] == 3
    assert row["comparable"] is True


def test_pairs_flag_a_blocked_case_as_not_comparable() -> None:
    """The two rows of a blocked case send different request text, so the delta is not a delta."""
    a2at = CaseItem(case_id=12, name="case-12", protocol=PROTOCOL_A2A_T)
    a2at.interception = {"followupInput": "retry with the agreed values"}
    pure = CaseItem(case_id=12, name="case-12", protocol=PROTOCOL_A2A)

    row = _pairs([a2at, pure])[0]

    assert row["comparable"] is False
    assert row["protocols"][PROTOCOL_A2A_T]["blocked"] is True
    assert row["protocols"][PROTOCOL_A2A]["blocked"] is False


def test_pairs_skip_cases_that_ran_under_one_protocol() -> None:
    only = CaseItem(case_id=9, name="case-9", protocol=PROTOCOL_A2A_T)

    assert _pairs([only]) == []
