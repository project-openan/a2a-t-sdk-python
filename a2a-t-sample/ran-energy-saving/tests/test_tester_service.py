"""Unit tests of the tester batch service: stage model, state and rerun identity."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import ContentValidationError
from demo.web.tester import service
from demo.web.tester.service import (
    ALL_STAGES,
    CASE_CANCELLED,
    CASE_ERROR,
    CASE_FAILED,
    CASE_PARTIAL,
    CASE_PASSED,
    CASE_PENDING,
    CASE_RUNNING,
    DECISION_MODE_LIVE,
    DEFAULT_CONCURRENCY,
    DEFAULT_ROUNDS,
    MAX_CONCURRENCY,
    MAX_ROUNDS,
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
from eval.osp.followup import followup_input, is_blocked_case
from eval.osp.intent_schema import OSP_INTENT_SCHEMA
from eval.osp.runner import load_cases
from protocol.client_flow import ClientFlowResult
from protocol.client_runtime import MODE_AUTO, MODE_SCRIPTED
from protocol.client_service import RunState
from shared.models import NegotiationDecision, NegotiationProposeView
from shared.run_constants import (
    FINAL_STATE_INCOMPLETE,
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
        #: Texts the client must accept even when their case expects an interception.
        self.unblocked_texts: set[str] = set()

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
        if self._last_text in self.unblocked_texts:
            return SimpleNamespace(data={})
        if self._last_text in self.blocked_texts or (blocked_case and original_input):
            raise ContentValidationError(
                ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED,
                {"reason": "test"},
                message="blocked by test",
            )
        return SimpleNamespace(data={})


class _FakeRuns:
    """RunManager double recording every ``start`` call.

    ``gate`` scripts the pre-send gate the real run executes: ``block_then_pass`` (the default
    when the tester passes a correction) emits the blocked attempt, the correction and the
    passing retry; ``pass`` emits a single passing verdict (the interception missed); and
    ``block_then_block`` blocks both attempts. ``None`` emits no gate evidence at all.
    """

    def __init__(
        self,
        builder: Callable[[RunState], None],
        *,
        gate: str | None = None,
    ) -> None:
        self._builder = builder
        self._gate = gate
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
        run.raw_request = user_request
        run.correction_request = kwargs.get("correction")  # type: ignore[assignment]
        self.runs[run.run_id] = run
        self._builder(run)
        gate = self._gate or ("block_then_pass" if run.correction_request else None)
        if gate is not None:
            run.traces.extend(_gate_traces(run, gate))
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


def _blocked_run(run: RunState) -> None:
    """Make one fake run end at its own pre-send gate without sending anything."""
    run.status = "finished"
    run.result = ClientFlowResult(
        final_state=FINAL_STATE_INPUT_INVALID,
        error="Task-T 校验未通过。",
    )


def _gate_traces(run: RunState, mode: str) -> list[dict[str, object]]:
    """Return the pre-send gate evidence of one fake run.

    This mirrors what ``run_client_flow`` records: the first render and its blocked validation,
    the correction hand-off, and the retry's render and verdict. The real flow owns the behavior;
    the tester only reads this evidence.
    """
    request = str(run.raw_request or "")
    correction = str(run.correction_request or "")
    blocked: dict[str, object] = {
        "kind": "task_validation",
        "phase": "task_validation",
        "detail": {
            "verdict": "blocked",
            "blocking": True,
            "errors": [{"slotName": "region", "code": "content.param_missing"}],
            "reason": "content.param_missing",
        },
    }
    passed: dict[str, object] = {
        "kind": "task_validation",
        "phase": "task_validation",
        "detail": {"verdict": "pass", "blocking": False, "errors": []},
    }

    def rendered(text: str, attempt: int) -> dict[str, object]:
        # No promptText: this harness models the gate hand-off (who requested what), while the
        # Task-T render comparison is the real flow's job and needs a real SDK render.
        return {
            "kind": "task_prompt_rendered",
            "phase": "task_prompt",
            "detail": {"userRequest": text, "attempt": attempt},
        }

    if mode == "pass":
        return [rendered(request, 1), passed]
    traces: list[dict[str, object]] = [rendered(request, 1), blocked]
    if correction:
        traces.append(
            {
                "kind": "correction_applied",
                "phase": "task_prompt",
                "detail": {
                    "attempt": 1,
                    "originalRequest": request,
                    "correctedRequest": correction,
                    "verdict": "blocked",
                    "reason": "content.param_missing",
                    "errors": [{"slotName": "region", "code": "content.param_missing"}],
                },
            }
        )
        traces.append(rendered(correction, 2))
        traces.append(passed if mode == "block_then_pass" else blocked)
    return traces


def _manager(
    tmp_path: Path,
    runs: _FakeRuns,
    *,
    model_mode: str = "offline",
    nms_sim: bool = False,
    nms_sim_pure_question: str = "issue",
    protocol_defaults: dict[str, dict[str, str]] | None = None,
    judge: Any | None = None,
    model_label: str = "",
    judge_model_label: str = "",
    pure_hop: str = "",
) -> EvalBatchManager:
    # Only pass ``judge`` when a test supplies one: every historical test keeps constructing the
    # manager without the P2 keyword so an unimplemented judge cannot make them fail too.
    judge_kwarg: dict[str, Any] = {"judge": judge} if judge is not None else {}
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
        model_label=model_label,
        judge_model_label=judge_model_label,
        prompt_client=_PromptClient(),
        pure_hop=pure_hop,
        **judge_kwarg,
    )


class _StubJudge:
    """Fake judge returned by the tester integration tests.

    ``response`` is either one structured answer or an exception instance the client raises.
    """

    configured = True

    def __init__(self, response: object) -> None:
        self.response = response
        self.calls = 0
        self.prompts: list[str] = []

    async def complete_json(
        self,
        prompt: str,
        *,
        system: str = "",
        json_schema: object | None = None,
    ) -> dict[str, object]:
        del system, json_schema
        self.calls += 1
        self.prompts.append(prompt)
        if isinstance(self.response, BaseException):
            raise self.response
        assert isinstance(self.response, Mapping)
        return dict(self.response)


def _accepted_negotiation(run: RunState) -> None:
    """Finish one run that observed an information proposal and accepted it."""
    _finish(run)
    run.proposals.append(
        NegotiationProposeView(
            negotiation_id="nego-1",
            kind="information",
            round=1,
            max_rounds=1,
            content="Region: A2ATestRegion.",
        )
    )
    assert run.result is not None
    run.result.decisions.append(
        NegotiationDecision(action="accept", rationale="Accept the proposed region.")
    )
    #: The protocol really dispatched an ACCEPT: the quality judge's structure check reads it.
    #: ``summary`` is the Task-T action label the real flow emits, not the negotiation reply.
    run.traces.append(
        {
            "kind": "message_sent",
            "phase": "negotiation",
            "round": 1,
            "detail": {
                "performative": "ACCEPT",
                "summary": "create ran energy saving task",
                "templateUri": "urn:a2a:negotiation:v1",
            },
        }
    )
    #: The substantive reply lives on the decision trace, exactly as ``record_decision`` writes it.
    run.traces.append(
        {
            "kind": "accept",
            "phase": "negotiation",
            "round": 1,
            "detail": {
                "action": "accept",
                "rationale": "Accept the proposed region.",
                "confirmedIntent": "A2ATestRegion",
            },
        }
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


async def test_accepted_passes_when_the_task_moved_past_submitted(tmp_path: Path) -> None:
    """WORKING is the other proof of acceptance for A2A-T."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = "TASK_STATE_WORKING"
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A_T)

    result = await manager._judge_accepted(
        run, item, CASES[1], True, timeout_seconds=1.0
    )

    assert result.status == STAGE_PASSED


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


async def test_vendor_accepted_waits_for_the_success_copy(tmp_path: Path) -> None:
    """A bare SUBMITTED only acknowledges the Task; the vendor may still cancel it."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    run = RunState(
        run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="webhook", discovery="nms"
    )
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = TASK_STATE_SUBMITTED
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A_T)

    result = await manager._judge_accepted(
        run, item, CASES[1], True, timeout_seconds=0.2
    )

    assert result.status == STAGE_WAITING
    assert "TASK_STATE_SUBMITTED" in result.message


async def test_vendor_accepted_passes_on_the_structured_task_payload(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    run = RunState(
        run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="webhook", discovery="nms"
    )
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = TASK_STATE_SUBMITTED
    run.server_intent = {"Operation type": "Create"}
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A_T)

    result = await manager._judge_accepted(
        run, item, CASES[1], True, timeout_seconds=0.2
    )

    assert result.status == STAGE_PASSED


async def test_vendor_accepted_passes_on_the_success_copy(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    run = RunState(
        run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="webhook", discovery="nms"
    )
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = TASK_STATE_SUBMITTED
    run.acceptance_copy = "Task creation succeeded"
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A_T)

    result = await manager._judge_accepted(
        run, item, CASES[1], True, timeout_seconds=0.2
    )

    assert result.status == STAGE_PASSED


async def test_vendor_accepted_fails_when_the_task_is_cancelled_after_the_ack(
    tmp_path: Path,
) -> None:
    """The acceptance must not be reported green before the vendor's later refusal arrives."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    run = RunState(
        run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="webhook", discovery="nms"
    )
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = TASK_STATE_SUBMITTED
    item = CaseItem(case_id=1, name="case", protocol=PROTOCOL_A2A_T)

    async def cancel_after_ack() -> None:
        await asyncio.sleep(0.05)
        run.latest_task_state = "TASK_STATE_CANCELED"

    publisher = asyncio.create_task(cancel_after_ack())
    try:
        result = await manager._judge_accepted(
            run, item, CASES[1], True, timeout_seconds=1.0
        )
    finally:
        await publisher

    assert result.status == STAGE_FAILED
    assert "TASK_STATE_CANCELED" in result.message


def test_a_vendor_baseline_needs_the_acceptance_evidence() -> None:
    """Continuing an acknowledged-but-unaccepted Task makes the Modify fail unexplained."""
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="webhook")
    run.remote_task_id = "task-1"
    run.remote_context_id = "ctx-1"
    run.latest_task_state = TASK_STATE_SUBMITTED

    assert not service._baseline_continuable(
        run, PROTOCOL_A2A_T, "nms", TASK_STATE_SUBMITTED
    )

    run.server_intent = {"Operation type": "Create"}
    assert service._baseline_continuable(run, PROTOCOL_A2A_T, "nms", TASK_STATE_SUBMITTED)


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


def test_a_task_t_stage_serializes_its_section_rows() -> None:
    task_t = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]
    result = StageResult("task_t", STAGE_PASSED, expected=task_t, actual=task_t)

    data = result.to_dict()

    sections = data["sections"]
    assert isinstance(sections, list)
    assert [row["path"] for row in sections][0] == "task_t.Operation Type"
    assert "sections" not in StageResult("intent", STAGE_PASSED).to_dict()


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


def test_an_offline_batch_rejects_the_agent_negotiation_source(tmp_path: Path) -> None:
    """An offline process has no model, so it cannot author a live negotiation reply."""
    manager = _manager(tmp_path, _FakeRuns(_finish), model_mode="offline")

    with pytest.raises(ValueError):
        manager.create(
            case_ids=[1],
            protocols=[PROTOCOL_A2A_T],
            stop_after="validate",
            negotiation_source="agent",
        )


def test_the_default_negotiation_source_follows_the_model_mode(tmp_path: Path) -> None:
    """Offline defaults to the recorded script; an online model defaults to the client agent."""
    offline = _manager(tmp_path / "offline", _FakeRuns(_finish), model_mode="offline").create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="validate",
    )
    live = _manager(tmp_path / "live", _FakeRuns(_finish), model_mode="live").create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="validate",
    )

    assert offline["negotiationSource"] == "script"
    assert live["negotiationSource"] == "agent"


def test_an_unknown_negotiation_source_is_rejected(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish), model_mode="live")

    with pytest.raises(ValueError):
        manager.create(
            case_ids=[1],
            protocols=[PROTOCOL_A2A_T],
            stop_after="validate",
            negotiation_source="human",
        )


async def test_an_agent_negotiation_case_runs_in_auto_mode(tmp_path: Path) -> None:
    """The agent source must not force MODE_SCRIPTED and must not carry a decision script."""
    manager = _manager(tmp_path, _FakeRuns(_finish), model_mode="live")
    created = manager.create(
        case_ids=[51],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        negotiation_source="agent",
    )

    await manager.start(str(created["batchId"]))
    await _wait_status(manager, str(created["batchId"]), "finished")

    runs = manager._runs
    assert isinstance(runs, _FakeRuns)
    assert runs.calls[0]["mode"] == MODE_AUTO
    assert runs.calls[0]["scripted_decisions"] is None


async def test_a_script_negotiation_case_runs_scripted_with_its_decisions(tmp_path: Path) -> None:
    """The script source keeps replaying the case Q column."""
    manager = _manager(tmp_path, _FakeRuns(_finish), model_mode="live")
    created = manager.create(
        case_ids=[51],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        negotiation_source="script",
    )

    await manager.start(str(created["batchId"]))
    await _wait_status(manager, str(created["batchId"]), "finished")

    runs = manager._runs
    assert isinstance(runs, _FakeRuns)
    assert runs.calls[0]["mode"] == MODE_SCRIPTED
    assert runs.calls[0]["scripted_decisions"]


async def test_a_non_negotiation_agent_case_keeps_its_normal_mode(tmp_path: Path) -> None:
    """Only an expected A2A-T negotiation is diverted; an ordinary case keeps ``_mode_for``."""
    manager = _manager(tmp_path, _FakeRuns(_finish), model_mode="live")
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        negotiation_source="agent",
    )

    await manager.start(str(created["batchId"]))
    await _wait_status(manager, str(created["batchId"]), "finished")

    runs = manager._runs
    assert isinstance(runs, _FakeRuns)
    assert runs.calls[0]["mode"] == MODE_AUTO
    assert runs.calls[0]["scripted_decisions"] is None


async def test_an_agent_pure_case_does_not_replay_the_p_column(tmp_path: Path) -> None:
    """The agent source is protocol-neutral: the A2A reply is decided, not replayed."""
    manager = _manager(tmp_path, _FakeRuns(_accepted_negotiation), model_mode="live")
    created = manager.create(
        case_ids=[51],
        protocols=[PROTOCOL_A2A],
        stop_after="final",
        negotiation_source="agent",
    )

    await manager.start(str(created["batchId"]))
    await _wait_status(manager, str(created["batchId"]), "finished")

    runs = manager._runs
    assert isinstance(runs, _FakeRuns)
    assert runs.calls[0]["mode"] == MODE_AUTO
    assert runs.calls[0].get("pure_answers") is None
    source = runs.calls[0]["source"]
    assert isinstance(source, Mapping)
    assert source["negotiationExpected"] is True
    item = manager._batches[str(created["batchId"])].items[0]
    # The P column is not lost: it stays in the export as the recorded client reply.
    assert item.refs["clientReplies"] == ["The region is A2ATestRegion."]


async def test_a_script_pure_case_still_replays_the_p_column(tmp_path: Path) -> None:
    """The script source keeps the deterministic P-column replay unchanged."""
    manager = _manager(tmp_path, _FakeRuns(_accepted_negotiation), model_mode="live")
    created = manager.create(
        case_ids=[51],
        protocols=[PROTOCOL_A2A],
        stop_after="final",
        negotiation_source="script",
    )

    await manager.start(str(created["batchId"]))
    await _wait_status(manager, str(created["batchId"]), "finished")

    runs = manager._runs
    assert isinstance(runs, _FakeRuns)
    assert runs.calls[0]["pure_answers"] == ["The region is A2ATestRegion."]


async def test_a_pure_item_reads_its_own_expectation_block(tmp_path: Path) -> None:
    """Case 3 negotiates on A2A only; a pure item must not read the A2A-T block."""
    manager = _manager(tmp_path, _FakeRuns(_accepted_negotiation), model_mode="live")
    created = manager.create(
        case_ids=[3],
        protocols=[PROTOCOL_A2A],
        stop_after="final",
        negotiation_source="agent",
    )

    await manager.start(str(created["batchId"]))
    await _wait_status(manager, str(created["batchId"]), "finished")

    runs = manager._runs
    assert isinstance(runs, _FakeRuns)
    source = runs.calls[0]["source"]
    assert isinstance(source, Mapping)
    assert source["negotiationExpected"] is True
    item = manager._batches[str(created["batchId"])].items[0]
    assert item.refs["negotiationExpected"] is True
    assert item.refs["expectedRounds"] == 1


async def test_an_agent_pure_item_gets_its_client_quality(tmp_path: Path) -> None:
    """The quality appendix follows the decision, not the protocol."""
    judge = _StubJudge(
        {"verdict": "equal", "covered": True, "reason": "the accepted region matches"}
    )
    manager = _manager(
        tmp_path, _FakeRuns(_accepted_negotiation), model_mode="live", judge=judge
    )
    created = manager.create(
        case_ids=[51],
        protocols=[PROTOCOL_A2A],
        stop_after="final",
        negotiation_source="agent",
    )

    await manager.start(str(created["batchId"]))
    await _wait_status(manager, str(created["batchId"]), "finished")

    item = manager._batches[str(created["batchId"])].items[0]
    assert isinstance(item.client_quality, Mapping)
    assert item.client_quality["quality"] == "pass"
    assert judge.calls == 1


def test_an_agent_pure_batch_rejects_the_oss_proxy_hop(tmp_path: Path) -> None:
    """The OSS proxy is a deprecated hop; it cannot host an agent-authored reply."""
    manager = _manager(
        tmp_path, _FakeRuns(_finish), model_mode="live", pure_hop="oss"
    )

    with pytest.raises(ValueError, match="OSS"):
        manager.create(
            case_ids=[51],
            protocols=[PROTOCOL_A2A],
            stop_after="final",
            negotiation_source="agent",
        )
    # The script source still runs the recorded dialogue through the proxy.
    created = manager.create(
        case_ids=[51],
        protocols=[PROTOCOL_A2A],
        stop_after="final",
        negotiation_source="script",
    )
    assert created["batchId"]


async def test_an_agent_negotiation_item_gets_its_client_quality(tmp_path: Path) -> None:
    """P2: an agent-authored A2A-T negotiation item carries the quality appendix."""
    judge = _StubJudge(
        {"verdict": "equal", "covered": True, "reason": "the accepted region matches"}
    )
    manager = _manager(
        tmp_path, _FakeRuns(_accepted_negotiation), model_mode="live", judge=judge
    )
    created = manager.create(
        case_ids=[51],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        negotiation_source="agent",
    )

    await manager.start(str(created["batchId"]))
    finished = await _wait_status(manager, str(created["batchId"]), "finished")

    item = manager._batches[str(created["batchId"])].items[0]
    assert isinstance(item.client_quality, Mapping)
    assert item.client_quality["quality"] == "pass"
    assert judge.calls == 1
    # The judge must see the decision's confirmed intent, never the Task-T action label.
    assert "A2ATestRegion" in judge.prompts[0]
    assert "create ran energy saving task" not in judge.prompts[0]
    # The JSON export is the batch snapshot, so the same field must be exposed there.
    assert finished["items"][0]["clientQuality"] == item.client_quality


async def test_client_quality_never_changes_the_item_status(tmp_path: Path) -> None:
    """A failing quality row is an appendix: the NMS acceptance verdict stays authoritative."""
    judge = _StubJudge(
        {"verdict": "not_equal", "covered": True, "reason": "the reply differs"}
    )
    manager = _manager(
        tmp_path, _FakeRuns(_accepted_negotiation), model_mode="live", judge=judge
    )
    created = manager.create(
        case_ids=[51],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        negotiation_source="agent",
    )

    await manager.start(str(created["batchId"]))
    await _wait_status(manager, str(created["batchId"]), "finished")

    item = manager._batches[str(created["batchId"])].items[0]
    assert item.status == CASE_PASSED
    assert isinstance(item.client_quality, Mapping)
    assert item.client_quality["quality"] == "fail"


async def test_an_unavailable_judge_leaves_the_item_green(tmp_path: Path) -> None:
    """A judge outage records "unjudged"; it must not fail the item or the batch."""
    from shared.judge_llm import JudgeUnavailable

    judge = _StubJudge(JudgeUnavailable("judge endpoint is not configured"))
    manager = _manager(
        tmp_path, _FakeRuns(_accepted_negotiation), model_mode="live", judge=judge
    )
    created = manager.create(
        case_ids=[51],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        negotiation_source="agent",
    )

    await manager.start(str(created["batchId"]))
    finished = await _wait_status(manager, str(created["batchId"]), "finished")

    item = manager._batches[str(created["batchId"])].items[0]
    assert item.status == CASE_PASSED
    assert isinstance(item.client_quality, Mapping)
    assert item.client_quality["quality"] == "unjudged"
    assert finished["items"][0]["clientQuality"]["quality"] == "unjudged"


def test_the_snapshot_records_the_batch_plan(tmp_path: Path) -> None:
    """One batch must say who ran what, with which models, principles version and NMS."""
    manager = _manager(
        tmp_path,
        _FakeRuns(_finish),
        model_mode="live",
        model_label="negotiation-model",
        judge_model_label="judge-model",
    )
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="accepted",
        negotiation_source="agent",
        nms_version="NMS-1.2",
    )

    assert created["nmsVersion"] == "NMS-1.2"
    plan = created["plan"]
    assert plan["protocols"] == [PROTOCOL_A2A_T]
    assert plan["decisionSource"] == "agent"
    assert plan["negotiationModel"] == "negotiation-model"
    assert plan["judgeModel"] == "judge-model"
    assert plan["principlesVersion"] == created["principlesVersion"]
    assert plan["stopAfter"] == "accepted"
    assert plan["environment"]["transport"] == "sse"
    reloaded = _manager(
        tmp_path,
        _FakeRuns(_finish),
        model_mode="live",
        model_label="negotiation-model",
        judge_model_label="judge-model",
    ).get(str(created["batchId"]))
    assert reloaded is not None
    assert reloaded["nmsVersion"] == "NMS-1.2"
    assert reloaded["plan"]["judgeModel"] == "judge-model"


def test_a_batch_round_trips_the_negotiation_source(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish), model_mode="live")
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="validate",
        negotiation_source="agent",
    )

    assert created["schema"] == "osp-tester/v5"
    assert created["negotiationSource"] == "agent"
    reloaded = _manager(tmp_path, _FakeRuns(_finish), model_mode="live").get(
        str(created["batchId"])
    )
    assert reloaded is not None
    assert reloaded["negotiationSource"] == "agent"


@pytest.mark.parametrize(
    "schema",
    ["osp-tester/v1", "osp-tester/v2", "osp-tester/v3", "osp-tester/v4"],
)
def test_an_old_batch_loads_with_the_script_negotiation_source(
    tmp_path: Path, schema: str
) -> None:
    """A batch persisted before the source switch replayed the case script."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    history = tmp_path / "tester"
    history.mkdir(parents=True)
    document = {
        "schema": schema,
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
    assert snapshot["negotiationSource"] == "script"


def test_an_agent_source_needs_the_live_decision_mode(tmp_path: Path) -> None:
    """A live process still cannot pair the agent source with the scripted decision mode."""
    manager = _manager(tmp_path, _FakeRuns(_finish), model_mode="live")

    with pytest.raises(ValueError):
        manager.create(
            case_ids=[1],
            protocols=[PROTOCOL_A2A_T],
            stop_after="validate",
            decision_mode="scripted",
            negotiation_source="agent",
        )


def test_create_freezes_the_shared_principles(tmp_path: Path) -> None:
    """A batch records the principles version and body its agent will be told."""
    manager = _manager(tmp_path, _FakeRuns(_finish), model_mode="live")
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="validate",
        negotiation_source="agent",
    )

    assert created["principlesVersion"]
    assert created["principlesText"].strip()
    reloaded = _manager(tmp_path, _FakeRuns(_finish), model_mode="live").get(
        str(created["batchId"])
    )
    assert reloaded is not None
    assert reloaded["principlesVersion"] == created["principlesVersion"]
    assert reloaded["principlesText"] == created["principlesText"]


def test_a_loaded_agent_batch_falls_back_to_script_on_a_scripted_decision_mode(
    tmp_path: Path,
) -> None:
    """A batch persisted for another process must not inject agent replies here."""
    manager = _manager(tmp_path, _FakeRuns(_finish), model_mode="live")
    history = tmp_path / "tester"
    history.mkdir(parents=True)
    document = {
        "schema": "osp-tester/v5",
        "batchId": "scripted-batch",
        "caseIds": [1],
        "protocols": [PROTOCOL_A2A_T],
        "stopAfter": "validate",
        "decisionMode": "scripted",
        "negotiationSource": "agent",
        "principlesVersion": "2026-11-23.1",
        "principlesText": "frozen principles",
        "items": [
            {
                "caseId": 1,
                "name": "old",
                "protocol": PROTOCOL_A2A_T,
                "status": "passed",
            }
        ],
    }
    (history / "scripted-batch.json").write_text(json.dumps(document), encoding="utf-8")

    snapshot = manager.get("scripted-batch")

    assert snapshot is not None
    assert snapshot["negotiationSource"] == "script"


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
            if seed_state == TASK_STATE_SUBMITTED:
                # The vendor ships its success copy with the accepting status: a bare SUBMITTED is
                # only an acknowledgement, and a refused Create is cancelled after it.
                run.acceptance_copy = "Task creation succeeded"
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
        case_ids=[13],
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
        case_ids=[13],
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


async def test_a_negotiation_case_without_a_parsed_reply_never_starts_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing scripted reply is reported as case data before any Task is created."""
    from demo.web.tester import service as tester_service

    runs = _FakeRuns(_seed_then_modify(TASK_STATE_SUBMITTED))
    manager = _manager(tmp_path, runs)
    monkeypatch.setattr(tester_service, "a2at_decisions", lambda case: [])
    created = manager.create(
        case_ids=[32],
        protocols=[PROTOCOL_A2A_T],
        stop_after="negotiate",
        approval=None,
        timeout_seconds=2,
        decision_mode="scripted",
    )

    await manager.start(str(created["batchId"]))
    snapshot = await _wait_status(manager, str(created["batchId"]), "finished")

    item = snapshot["items"][0]
    assert isinstance(item, Mapping)
    assert item["status"] == CASE_ERROR
    assert runs.calls == []


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
        case_ids=[13],
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
        case_ids=[13],
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
        case_ids=[13],
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
        case_ids=[13],
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
        case_ids=[13],
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
        case_ids=[13],
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


async def test_a_full_flow_item_gets_its_task_t_comparison(tmp_path: Path) -> None:
    task_t = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]

    def builder(run: RunState) -> None:
        _finish(run)
        run.traces.append({"kind": "task_prompt_rendered", "detail": {"promptText": task_t}})

    manager = _manager(tmp_path, _FakeRuns(builder))
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=None,
    )
    await manager.start(str(created["batchId"]))
    finished = await _wait_status(manager, str(created["batchId"]), "finished")

    stage = finished["items"][0]["stages"]["task_t"]
    assert stage["status"] == STAGE_PASSED
    assert stage["expected"] == task_t
    assert stage["actual"] == task_t
    assert [row["path"] for row in stage["sections"]][0] == "task_t.Operation Type"


async def test_a_full_flow_item_gets_its_validation_stage(tmp_path: Path) -> None:
    def builder(run: RunState) -> None:
        _finish(run)
        run.traces.append(
            {
                "kind": "task_validation",
                "phase": "task_validation",
                # The client flow spells a clean validation ``passed``; the case expects ``pass``.
                "detail": {
                    "verdict": "passed",
                    "errors": [],
                    "reason": "content.param_missing",
                },
            }
        )

    manager = _manager(tmp_path, _FakeRuns(builder))
    created = manager.create(
        case_ids=[51],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=None,
    )
    await manager.start(str(created["batchId"]))
    finished = await _wait_status(manager, str(created["batchId"]), "finished")

    stage = finished["items"][0]["stages"]["validate"]
    assert stage["status"] == STAGE_PASSED
    assert stage["expected"] == "pass"
    assert stage["actual"] == "pass"


async def test_a_full_flow_task_t_mismatch_fails_the_task_t_stage(tmp_path: Path) -> None:
    task_t = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]
    changed = task_t.replace("720p video", "1080p video")

    def builder(run: RunState) -> None:
        _finish(run)
        run.traces.append({"kind": "task_prompt_rendered", "detail": {"promptText": changed}})

    manager = _manager(tmp_path, _FakeRuns(builder))
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=None,
    )
    await manager.start(str(created["batchId"]))
    finished = await _wait_status(manager, str(created["batchId"]), "finished")

    stage = finished["items"][0]["stages"]["task_t"]
    assert stage["status"] == STAGE_FAILED
    assert stage["diffs"]


async def _run_item_with_server_intent(
    tmp_path: Path,
    payload: Mapping[str, object] | None,
    *,
    nms_sim: bool = False,
    protocol: str = PROTOCOL_A2A_T,
    protocol_defaults: dict[str, dict[str, str]] | None = None,
    judge: Any | None = None,
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
        judge=judge,
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


def _intent_judge_answer(
    expected: Mapping[str, object],
    observed: Mapping[str, object],
    *,
    equal: bool,
) -> dict[str, object]:
    """Return one intent-judge answer covering every residual field of this comparison."""
    from eval.osp.compare import compare_intent

    return {
        "all_equal": equal,
        "fields": [
            {
                "path": diff.path,
                "equal": equal,
                "reason": "same business value" if equal else "differs",
            }
            for diff in compare_intent(expected, observed)
        ],
    }


def _clear_intent_judge_cache() -> None:
    """Clear the process-wide intent-judge cache so each test owns its call counts."""
    from eval.osp.intent_judge import clear_intent_judge_cache

    clear_intent_judge_cache()


async def test_the_intent_stage_judges_the_downstream_payload_not_the_sdk_extraction(
    tmp_path: Path,
) -> None:
    """The SDK extraction matches the case, but the server understood something else."""
    expected = CASES[1]["expect"]["intent"]
    payload = dict(expected)
    payload["Operation type"] = "Modify"
    payload["Energy saving region"] = "OtherRegion"
    item = await _run_item_with_server_intent(
        tmp_path,
        payload,
        judge=_StubJudge(_intent_judge_answer(expected, payload, equal=False)),
    )

    stage = item.stages["intent"]
    assert stage.status == STAGE_FAILED
    assert stage.actual == payload
    assert any(diff["path"] == "intent.Energy saving region" for diff in stage.diffs)
    # A full flow no longer runs the SDK preflight, so there is no auxiliary SDK extraction.
    assert item.intent is None
    assert item.server_intent == payload
    assert item.intent_meta["source"] == "server"
    assert item.status == "failed"


async def test_a_matching_downstream_payload_passes_the_intent_stage(tmp_path: Path) -> None:
    item = await _run_item_with_server_intent(tmp_path, CASES[1]["expect"]["intent"])

    stage = item.stages["intent"]
    assert stage.status == STAGE_PASSED
    assert stage.diffs == []
    assert item.server_intent == CASES[1]["expect"]["intent"]
    assert item.to_dict()["serverIntent"] == CASES[1]["expect"]["intent"]


async def test_an_unfinished_flow_reason_is_visible_in_the_item(tmp_path: Path) -> None:
    """The result column shows why a run stopped unfinished, without opening the drawer."""
    reason = (
        "第 2 轮追问没有可用答案（用例提供的答案已耗尽）；未再作答，下游回执与意图已保留。"
    )

    def builder(run: RunState) -> None:
        _finish(run)
        run.result = ClientFlowResult(final_state=FINAL_STATE_INCOMPLETE, error=reason)
        run.latest_task_state = "TASK_STATE_INPUT_REQUIRED"

    manager = _manager(tmp_path, _FakeRuns(builder))
    created = manager.create(
        case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="final", approval=None
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    await _wait_status(manager, batch_id, "finished")

    item = manager._batches[batch_id].items[0]
    assert item.error == reason
    assert item.to_dict()["error"] == reason


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
    expected = CASES[1]["expect"]["intent"]
    payload = dict(expected)
    payload["Energy saving region"] = "OtherRegion"
    item = await _run_item_with_server_intent(
        tmp_path,
        payload,
        protocol=PROTOCOL_A2A,
        protocol_defaults=DIRECT_PURE_DEFAULTS,
        judge=_StubJudge(_intent_judge_answer(expected, payload, equal=False)),
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


async def test_exact_and_normalized_intents_pass_with_no_intent_judge_call(
    tmp_path: Path,
) -> None:
    """Only a genuine residual needs the model; notation is decided structurally."""
    expected = CASES[1]["expect"]["intent"]
    judge = _StubJudge({"all_equal": False, "fields": []})
    _clear_intent_judge_cache()

    exact = await _run_item_with_server_intent(tmp_path / "exact", expected, judge=judge)
    assert exact.stages["intent"].status == STAGE_PASSED
    assert isinstance(exact.intent_verdict, Mapping)
    assert exact.intent_verdict["method"] == "structured"
    assert exact.intent_verdict["status"] == "pass"

    normalized_payload = dict(expected)
    normalized_payload["Energy saving region"] = " a2atestregion "
    normalized = await _run_item_with_server_intent(
        tmp_path / "normalized", normalized_payload, judge=judge
    )
    assert normalized.stages["intent"].status == STAGE_PASSED
    assert isinstance(normalized.intent_verdict, Mapping)
    assert normalized.intent_verdict["method"] == "structured"
    region_row = next(
        row
        for row in normalized.intent_comparison()
        if row["path"] == "intent.Energy saving region"
    )
    assert region_row["status"] == "changed"
    assert region_row["verdict"] == "same"
    assert judge.calls == 0


async def test_a_residual_intent_gets_one_model_call_and_passes_when_equal(
    tmp_path: Path,
) -> None:
    expected = CASES[1]["expect"]["intent"]
    observed = dict(expected)
    observed["Energy saving region"] = "OtherRegion"
    judge = _StubJudge(_intent_judge_answer(expected, observed, equal=True))
    _clear_intent_judge_cache()

    item = await _run_item_with_server_intent(tmp_path, observed, judge=judge)

    assert item.stages["intent"].status == STAGE_PASSED
    assert isinstance(item.intent_verdict, Mapping)
    assert item.intent_verdict["method"] == "model"
    assert item.intent_verdict["status"] == "pass"
    assert judge.calls == 1


async def test_a_residual_intent_fails_when_the_model_says_not_equal(tmp_path: Path) -> None:
    expected = CASES[1]["expect"]["intent"]
    observed = dict(expected)
    observed["Energy saving region"] = "OtherRegion"
    judge = _StubJudge(_intent_judge_answer(expected, observed, equal=False))
    _clear_intent_judge_cache()

    item = await _run_item_with_server_intent(tmp_path, observed, judge=judge)

    assert item.stages["intent"].status == STAGE_FAILED
    assert item.status == "failed"
    assert isinstance(item.intent_verdict, Mapping)
    assert item.intent_verdict["method"] == "model"
    assert item.intent_verdict["status"] == "mismatch"
    assert judge.calls == 1


async def test_a_residual_intent_without_a_judge_is_recorded_not_failed(tmp_path: Path) -> None:
    """An unavailable judge is "unjudged", never a fabricated mismatch."""
    expected = CASES[1]["expect"]["intent"]
    observed = dict(expected)
    observed["Energy saving region"] = "OtherRegion"
    _clear_intent_judge_cache()

    item = await _run_item_with_server_intent(tmp_path, observed)

    assert item.stages["intent"].status == STAGE_RECORDED
    assert item.status == "partial"
    assert item.needs_review is True
    assert isinstance(item.intent_verdict, Mapping)
    assert item.intent_verdict["method"] == "unjudged"
    assert item.intent_verdict["status"] == "unjudged"


async def test_an_unavailable_intent_judge_is_recorded_not_failed(tmp_path: Path) -> None:
    from shared.judge_llm import JudgeUnavailable

    expected = CASES[1]["expect"]["intent"]
    observed = dict(expected)
    observed["Energy saving region"] = "OtherRegion"
    judge = _StubJudge(JudgeUnavailable("judge endpoint is down"))
    _clear_intent_judge_cache()

    item = await _run_item_with_server_intent(tmp_path, observed, judge=judge)

    assert item.stages["intent"].status == STAGE_RECORDED
    assert item.status == "partial"
    assert isinstance(item.intent_verdict, Mapping)
    assert item.intent_verdict["status"] == "unjudged"


async def test_an_invalid_intent_judge_answer_is_recorded_not_failed(tmp_path: Path) -> None:
    expected = CASES[1]["expect"]["intent"]
    observed = dict(expected)
    observed["Energy saving region"] = "OtherRegion"
    judge = _StubJudge({"all_equal": True})  # missing the per-field verdicts
    _clear_intent_judge_cache()

    item = await _run_item_with_server_intent(tmp_path, observed, judge=judge)

    assert item.stages["intent"].status == STAGE_RECORDED
    assert item.status == "partial"
    assert isinstance(item.intent_verdict, Mapping)
    assert item.intent_verdict["status"] == "unjudged"


async def test_an_assertion_off_intent_makes_no_judge_call(tmp_path: Path) -> None:
    """A disabled intent assertion stays record-only and must not spend a model call."""
    expected = CASES[1]["expect"]["intent"]
    observed = dict(expected)
    observed["Energy saving region"] = "OtherRegion"
    judge = _StubJudge(_intent_judge_answer(expected, observed, equal=True))
    _clear_intent_judge_cache()

    def builder(run: RunState) -> None:
        _finish(run)
        run.server_intent = observed

    manager = _manager(tmp_path, _FakeRuns(builder), model_mode="live", judge=judge)
    manager._catalog.get(1)["assert"]["intent"] = False
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
    assert item.stages["intent"].status == STAGE_RECORDED
    assert judge.calls == 0


async def test_the_intent_verdict_is_persisted_in_the_snapshot(tmp_path: Path) -> None:
    expected = CASES[1]["expect"]["intent"]
    observed = dict(expected)
    observed["Energy saving region"] = "OtherRegion"
    judge = _StubJudge(_intent_judge_answer(expected, observed, equal=True))
    _clear_intent_judge_cache()

    def builder(run: RunState) -> None:
        _finish(run)
        run.server_intent = dict(observed)

    manager = _manager(tmp_path, _FakeRuns(builder), judge=judge)
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=None,
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    finished = await _wait_status(manager, batch_id, "finished")

    item = manager._batches[batch_id].items[0]
    assert isinstance(item.intent_verdict, Mapping)
    assert item.to_dict()["intentVerdict"] == item.intent_verdict
    assert finished["items"][0]["intentVerdict"] == item.intent_verdict

    reloaded = _manager(tmp_path, _FakeRuns(_finish), judge=judge).get(batch_id)
    assert reloaded is not None
    assert reloaded["items"][0]["intentVerdict"] == item.intent_verdict


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
        # The new attempt re-reads the case metadata immediately (a full flow has no preflight
        # await any more); the previous attempt's values must be gone, not the field left empty.
        assert item.refs.get("caseName") != "old"
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


async def test_the_sdk_stages_build_their_client_from_the_ui_env_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preflight and the run must read the same .env, or they resolve different models."""
    seen: list[Path | None] = []

    def fake_build_client(env_path: Path | None = None, *, model_mode: str | None = None) -> Any:
        seen.append(env_path)
        return _PromptClient(), tmp_path / "sdk-env"

    monkeypatch.setattr(service, "build_client_with_env", fake_build_client)
    env_path = tmp_path / "ui.env"
    env_path.write_text("A2AT_SAMPLE_MODEL_MODE=live\n", encoding="utf-8")
    manager = EvalBatchManager(
        runs=_FakeRuns(_finish),  # type: ignore[arg-type]
        catalog=CaseCatalog(),
        protocol_defaults=PROTOCOL_DEFAULTS,
        server_url=None,
        server_auth_token=None,
        runs_dir=tmp_path,
        language="en-US",
        model_mode="live",
        env_path=env_path,
    )
    created = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="validate",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    await _wait_status(manager, str(created["batchId"]), "finished")

    assert seen == [env_path]


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


async def test_a_blocked_case_runs_the_original_and_corrects_inside_the_run(
    tmp_path: Path,
) -> None:
    """The gate is a run phase: the run starts with the operator request and dispatches the
    replacement only after its own block. The replacement never becomes the run's request."""
    runs = _FakeRuns(_finish, gate="block_then_pass")
    manager = _manager(tmp_path, runs)
    _inject_followup(manager, 22)
    original = str(CASES[22]["user_input"])
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
    assert runs.calls[0]["userRequest"] == original
    assert runs.calls[0]["correction"] == FOLLOWUP_INPUT
    item = _only_item(finished)
    interception = item["interception"]
    assert isinstance(interception, Mapping)
    assert interception["input"] == original
    assert interception["followupInput"] == FOLLOWUP_INPUT
    intercepted_stages = interception["stages"]
    assert isinstance(intercepted_stages, Mapping)
    assert intercepted_stages["validate"]["actual"] == "blocked"
    assert intercepted_stages["validate"]["status"] == STAGE_PASSED
    # The run's own validate stage judges the request it actually dispatched.
    stages = item["stages"]
    assert isinstance(stages, Mapping)
    assert stages["validate"]["expected"] == "pass"
    assert stages["validate"]["status"] == STAGE_PASSED
    assert stages["send"]["expected"] is True
    assert stages["send"]["status"] == STAGE_PASSED
    assert stages["final"]["expected"] == "task_completed"
    assert stages["final"]["status"] == STAGE_PASSED
    assert item["status"] == CASE_PASSED
    refs = item["refs"]
    assert isinstance(refs, Mapping)
    assert refs["userInput"] == original
    assert refs["dispatchedRequest"] == FOLLOWUP_INPUT
    assert refs["expectedFinalState"] == "task_completed"


async def test_a_blocked_modify_runs_the_original_then_corrects_to_the_followup(
    tmp_path: Path,
) -> None:
    """A blocked Modify seeds its baseline Create, then runs the original with a correction.

    The Modify run keeps the operator's original text and reaches the replacement through its own
    gate; the replacement never replaces the run's request.
    """
    runs = _FakeRuns(_seed_then_modify(TASK_STATE_SUBMITTED))
    manager = _manager(tmp_path, runs)
    case = manager._catalog.get(63)
    assert case is not None
    assert case.get("operation") == "Modify"
    original = str(case["user_input"])
    supplement = followup_input(case)
    assert supplement
    created = manager.create(
        case_ids=[63],
        protocols=[PROTOCOL_A2A_T],
        stop_after="send",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    await _wait_status(manager, str(created["batchId"]), "finished")

    # Call 1 is the paired Create baseline; call 2 is the case's Modify run.
    assert len(runs.calls) == 2
    assert runs.calls[0]["userRequest"] != supplement
    assert runs.calls[1]["userRequest"] == original
    assert runs.calls[1]["correction"] == supplement
    assert runs.calls[1]["target_task_id"] == "task-1"
    assert runs.calls[1]["target_context_id"] == "ctx-1"


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
    # The expected interception and the rejected replacement must not read the same way.
    assert "blocked_followup.input" in stages["validate"]["message"]
    assert "仍被拦截" in stages["validate"]["message"]
    assert "blocked_followup.input" in item["error"]
    interception = item["interception"]
    assert isinstance(interception, Mapping)
    assert interception["followupBlocked"] is True


async def test_a_full_flow_followup_that_is_still_blocked_says_so(tmp_path: Path) -> None:
    """The run dispatches the replacement request; blocking it is not the expected interception."""
    manager = _manager(tmp_path, _FakeRuns(_blocked_run, gate="block_then_block"))
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

    item = _only_item(finished)
    stages = item["stages"]
    assert isinstance(stages, Mapping)
    assert stages["validate"]["status"] == STAGE_FAILED
    assert "补充请求" in stages["validate"]["message"]
    assert "blocked_followup.input" in item["error"]
    interception = item["interception"]
    assert isinstance(interception, Mapping)
    assert interception["followupBlocked"] is True


async def test_an_interception_that_did_not_fire_skips_the_followup(tmp_path: Path) -> None:
    """A gate that does not hold fails the case and never dispatches the replacement."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    _inject_followup(manager, 22)
    client = manager._prompt_client
    assert isinstance(client, _PromptClient)
    client.unblocked_texts.add(str(CASES[22]["user_input"]))
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
    # The gate never fired, so the interception is recorded and the case passes.
    assert item["status"] == CASE_PASSED
    assert item["error"] is None
    interception = item["interception"]
    assert isinstance(interception, Mapping)
    assert interception["followupSkipped"] is True
    blocked = interception["stages"]
    assert isinstance(blocked, Mapping)
    assert blocked["validate"]["status"] == STAGE_RECORDED
    assert blocked["validate"]["actual"] == "pass"
    # The replacement's stage columns say it never ran.
    stages = item["stages"]
    assert isinstance(stages, Mapping)
    assert {status["status"] for status in stages.values()} == {STAGE_SKIPPED}
    assert all("原请求未被拦截" in str(status["message"]) for status in stages.values())
    # The frozen evidence names the request that actually ran.
    refs = item["refs"]
    assert isinstance(refs, Mapping)
    assert refs["userInput"] == str(CASES[22]["user_input"])


async def test_a_full_flow_case_whose_interception_did_not_fire_dispatches_the_original(
    tmp_path: Path,
) -> None:
    """The original request goes downstream; the replacement is not sent."""
    runs = _FakeRuns(_finish, gate="pass")
    manager = _manager(tmp_path, runs)
    _inject_followup(manager, 22)
    original = str(CASES[22]["user_input"])
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
    # The original request went downstream and the case is recorded as passed.
    assert item["status"] == CASE_PASSED
    assert item["error"] is None
    assert len(runs.calls) == 1
    assert runs.calls[0]["userRequest"] == original
    assert runs.calls[0]["correction"] == FOLLOWUP_INPUT
    interception = item["interception"]
    assert isinstance(interception, Mapping)
    assert interception["followupSkipped"] is True
    assert interception["originalDispatched"] is True
    assert interception["stages"]["validate"]["status"] == STAGE_RECORDED
    assert "已改按原请求下发" in interception["stages"]["validate"]["message"]
    # The run's evidence describes the original request, so it is recorded, never judged against
    # the corpus expectations of the replacement.
    stages = item["stages"]
    assert isinstance(stages, Mapping)
    assert stages["send"]["status"] == STAGE_RECORDED
    assert stages["final"]["status"] == STAGE_RECORDED
    refs = item["refs"]
    assert isinstance(refs, Mapping)
    assert refs["userInput"] == original


async def test_a_rerun_after_a_process_restart_rebuilds_the_correction(tmp_path: Path) -> None:
    """A restarted tester loads the persisted batch and reruns the item from the catalog.

    The correction is an input of the run, not persisted run state, so the new process derives
    it again: the rerun starts with the original request and the same replacement.
    """
    first_runs = _FakeRuns(_finish, gate="block_then_pass")
    first = _manager(tmp_path, first_runs)
    case = first._catalog.get(22)
    assert case is not None
    original = str(case["user_input"])
    replacement = followup_input(case)
    assert replacement
    created = first.create(
        case_ids=[22],
        protocols=[PROTOCOL_A2A_T],
        stop_after="final",
        approval=None,
        timeout_seconds=2,
    )
    await first.start(str(created["batchId"]))
    await _wait_status(first, str(created["batchId"]), "finished")

    # A new process: fresh catalog, fresh run manager, the persisted batch document.
    second_runs = _FakeRuns(_finish, gate="block_then_pass")
    second = _manager(tmp_path, second_runs)
    batch_id = str(created["batchId"])
    assert second.get(batch_id) is not None

    await second.rerun_item(batch_id, 0)
    finished = await _wait_status(second, batch_id, "finished")

    assert len(second_runs.calls) == 1
    assert second_runs.calls[0]["userRequest"] == original
    assert second_runs.calls[0]["correction"] == replacement
    item = _only_item(finished)
    assert item["status"] == CASE_PASSED
    refs = item["refs"]
    assert isinstance(refs, Mapping)
    assert refs["userInput"] == original
    assert refs["dispatchedRequest"] == replacement
    attempts = item["attempts"]
    assert isinstance(attempts, list)
    assert len(attempts) == 1


async def test_a_full_flow_blocked_modify_that_did_not_fire_falls_back_to_the_original(
    tmp_path: Path,
) -> None:
    """A Modify still needs its paired baseline, then continues with the original request."""
    runs = _FakeRuns(_seed_then_modify(TASK_STATE_SUBMITTED), gate="pass")
    manager = _manager(tmp_path, runs)
    case = manager._catalog.get(63)
    assert case is not None
    original = str(case["user_input"])
    created = manager.create(
        case_ids=[63],
        protocols=[PROTOCOL_A2A_T],
        stop_after="send",
        approval=None,
        timeout_seconds=2,
    )

    await manager.start(str(created["batchId"]))
    finished = await _wait_status(manager, str(created["batchId"]), "finished")

    item = _only_item(finished)
    assert item["status"] == CASE_PASSED
    assert item["error"] is None
    # Call 1 is the paired Create baseline; call 2 is the original request, not the replacement.
    assert len(runs.calls) == 2
    assert runs.calls[0]["userRequest"] != original
    assert runs.calls[1]["userRequest"] == original
    interception = item["interception"]
    assert isinstance(interception, Mapping)
    assert interception["followupSkipped"] is True
    assert interception["originalDispatched"] is True
    assert "已改按原请求下发" in interception["stages"]["validate"]["message"]


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


def test_a_paired_batch_runs_blocked_cases_under_both_protocols(tmp_path: Path) -> None:
    """Every selected case runs; the console must not drop interception cases in paired mode."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    case = manager._catalog.get(22)
    assert case is not None and is_blocked_case(case)

    created = manager.create(
        case_ids=[1, 22],
        protocols=[PROTOCOL_A2A_T, PROTOCOL_A2A],
        stop_after="send",
        approval=None,
        timeout_seconds=2,
    )

    assert created["excludedCaseIds"] == []
    rows = [(item["caseId"], item["protocol"]) for item in created["items"]]
    assert rows == [
        (1, PROTOCOL_A2A_T),
        (1, PROTOCOL_A2A),
        (22, PROTOCOL_A2A_T),
        (22, PROTOCOL_A2A),
    ]


def test_a_single_protocol_batch_keeps_blocked_cases(tmp_path: Path) -> None:
    """Blocked cases are the point of an A2A-T interception run."""
    manager = _manager(tmp_path, _FakeRuns(_finish))

    created = manager.create(
        case_ids=[22],
        protocols=[PROTOCOL_A2A_T],
        stop_after="validate",
        approval=None,
        timeout_seconds=2,
    )

    assert created["excludedCaseIds"] == []
    assert [item["caseId"] for item in created["items"]] == [22]


# ---------------------------------------------------------------------------
# Concurrent batches and per-item cancellation
# ---------------------------------------------------------------------------


class _GatedRuns(_FakeRuns):
    """RunManager double whose runs stay unfinished until the test releases them.

    The concurrency tests need items that overlap in time; the plain double finishes every run
    immediately, which would make an N-item batch look sequential.
    """

    def __init__(self) -> None:
        super().__init__(_hang)
        self.in_flight = 0
        self.max_in_flight = 0
        self.gates: dict[str, asyncio.Event] = {}

    async def start(self, *args: object, **kwargs: object) -> RunState:
        run = await super().start(*args, **kwargs)  # type: ignore[misc]
        gate = asyncio.Event()
        self.gates[run.run_id] = gate
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        asyncio.create_task(self._release_when_asked(run, gate))
        return run

    async def _release_when_asked(self, run: RunState, gate: asyncio.Event) -> None:
        await gate.wait()
        self.in_flight -= 1
        if run.status == "running":
            _finish(run)

    async def cancel(self, run_id: str) -> bool:
        self.cancelled.append(run_id)
        run = self.runs.get(run_id)
        if run is not None and not run.finished():
            # A real ``RunManager.cancel`` ends the run; the waiter must observe that.
            run.status = "cancelled"
        gate = self.gates.get(run_id)
        if gate is not None:
            gate.set()
        return True

    def release(self, run_id: str) -> None:
        self.gates[run_id].set()

    def release_all(self) -> None:
        for gate in self.gates.values():
            gate.set()


async def _wait_item_status(
    manager: EvalBatchManager,
    batch_id: str,
    index: int,
    expected: str,
    timeout: float = 10.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        snapshot = manager.get(batch_id)
        assert snapshot is not None
        if snapshot["items"][index]["status"] == expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"item {index} of {batch_id} did not reach {expected}")


async def _wait_in_flight(runs: _GatedRuns, expected: int, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if runs.in_flight >= expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"only {runs.in_flight} runs were in flight, expected {expected}")


def test_concurrency_defaults_to_one_and_rejects_out_of_range(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = manager.create(case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="validate")
    assert created["concurrency"] == DEFAULT_CONCURRENCY == 1

    maximum = manager.create(
        case_ids=[1],
        protocols=[PROTOCOL_A2A_T],
        stop_after="validate",
        concurrency=MAX_CONCURRENCY,
    )
    assert maximum["concurrency"] == MAX_CONCURRENCY

    for value in (0, MAX_CONCURRENCY + 1, True, 2.0, "3"):
        with pytest.raises(ValueError):
            manager.create(
                case_ids=[1],
                protocols=[PROTOCOL_A2A_T],
                stop_after="validate",
                concurrency=value,  # type: ignore[arg-type]
            )


def test_a_persisted_batch_without_concurrency_loads_as_one(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = manager.create(
        case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="validate", concurrency=4
    )
    batch_id = str(created["batchId"])
    path = tmp_path / "tester" / f"{batch_id}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document.pop("concurrency", None)
    path.write_text(json.dumps(document), encoding="utf-8")

    reloaded = _manager(tmp_path, _FakeRuns(_finish))
    snapshot = reloaded.get(batch_id)
    assert snapshot is not None
    assert snapshot["concurrency"] == 1


async def test_a_sequential_batch_still_runs_one_item_at_a_time(tmp_path: Path) -> None:
    runs = _GatedRuns()
    manager = _manager(tmp_path, runs)
    created = manager.create(case_ids=[1, 2], protocols=[PROTOCOL_A2A_T], stop_after="send")
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    await _wait_in_flight(runs, 1)

    snapshot = manager.get(batch_id)
    assert snapshot is not None
    assert snapshot["concurrency"] == 1
    assert snapshot["activeIndices"] == [0]
    assert snapshot["currentIndex"] == 0
    assert len(runs.calls) == 1

    runs.release(next(iter(runs.gates)))
    deadline = asyncio.get_running_loop().time() + 10.0
    while len(runs.calls) < 2 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    assert len(runs.calls) == 2

    runs.release_all()
    finished = await _wait_status(manager, batch_id, "finished")
    assert runs.max_in_flight == 1
    assert finished["summary"]["passed"] == 2
    assert finished["activeIndices"] == []
    assert finished["currentIndex"] is None


async def test_a_concurrent_batch_runs_three_items_at_once(tmp_path: Path) -> None:
    runs = _GatedRuns()
    manager = _manager(tmp_path, runs)
    created = manager.create(
        case_ids=[1, 2, 3],
        protocols=[PROTOCOL_A2A_T],
        stop_after="send",
        concurrency=3,
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    await _wait_in_flight(runs, 3)

    snapshot = manager.get(batch_id)
    assert snapshot is not None
    assert snapshot["concurrency"] == 3
    assert sorted(snapshot["activeIndices"]) == [0, 1, 2]

    runs.release_all()
    finished = await _wait_status(manager, batch_id, "finished")
    assert runs.max_in_flight == 3
    assert finished["summary"]["passed"] == 3
    assert finished["activeIndices"] == []


async def test_cancelling_one_running_item_leaves_the_others_running(tmp_path: Path) -> None:
    runs = _GatedRuns()
    manager = _manager(tmp_path, runs)
    created = manager.create(
        case_ids=[1, 2],
        protocols=[PROTOCOL_A2A_T],
        stop_after="send",
        concurrency=2,
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    await _wait_in_flight(runs, 2)

    await manager.cancel_item(batch_id, 0)
    await _wait_item_status(manager, batch_id, 0, CASE_CANCELLED)

    snapshot = manager.get(batch_id)
    assert snapshot is not None
    assert snapshot["status"] == "running"
    assert snapshot["items"][0]["status"] == CASE_CANCELLED
    assert snapshot["items"][1]["status"] == CASE_RUNNING

    runs.release_all()
    finished = await _wait_status(manager, batch_id, "finished")
    assert finished["summary"]["cancelled"] == 1
    assert finished["summary"]["passed"] == 1


async def test_cancelling_a_queued_item_skips_it(tmp_path: Path) -> None:
    runs = _GatedRuns()
    manager = _manager(tmp_path, runs)
    created = manager.create(case_ids=[1, 2], protocols=[PROTOCOL_A2A_T], stop_after="send")
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    await _wait_in_flight(runs, 1)

    await manager.cancel_item(batch_id, 1)
    snapshot = manager.get(batch_id)
    assert snapshot is not None
    assert snapshot["items"][1]["status"] == CASE_CANCELLED

    runs.release_all()
    finished = await _wait_status(manager, batch_id, "finished")
    assert len(runs.calls) == 1
    assert finished["summary"]["passed"] == 1
    assert finished["summary"]["cancelled"] == 1


async def test_cancelling_a_batch_cancels_every_active_item(tmp_path: Path) -> None:
    runs = _GatedRuns()
    manager = _manager(tmp_path, runs)
    created = manager.create(
        case_ids=[1, 2, 3],
        protocols=[PROTOCOL_A2A_T],
        stop_after="send",
        concurrency=3,
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    await _wait_in_flight(runs, 3)

    assert await manager.cancel(batch_id) is True
    cancelled = await _wait_status(manager, batch_id, "cancelled")
    assert sorted(runs.cancelled) == ["run-1", "run-2", "run-3"]
    assert {item["status"] for item in cancelled["items"]} == {CASE_CANCELLED}


async def test_a_cancel_during_the_sdk_stage_never_starts_a_run(tmp_path: Path) -> None:
    runs = _FakeRuns(_finish)
    manager = _manager(tmp_path, runs)
    created = manager.create(case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="send")
    batch = manager._batches[str(created["batchId"])]
    item = batch.items[0]
    item.cancel_requested = True

    await manager._run_item(batch, 0, item)

    assert runs.calls == []
    assert item.status == CASE_CANCELLED


async def test_a_cancel_arriving_while_the_run_starts_stops_the_run(tmp_path: Path) -> None:
    """The cancel can land inside ``RunManager.start``; the new run must still be stopped."""
    runs = _FakeRuns(_finish)
    manager = _manager(tmp_path, runs)
    created = manager.create(case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="send")
    batch = manager._batches[str(created["batchId"])]
    item = batch.items[0]

    original_start = runs.start

    async def start_and_cancel(*args: object, **kwargs: object) -> RunState:
        run = await original_start(*args, **kwargs)  # type: ignore[misc]
        item.cancel_requested = True
        return run

    runs.start = start_and_cancel  # type: ignore[method-assign]

    await manager._run_item(batch, 0, item)

    assert runs.cancelled == ["run-1"]
    assert item.status == CASE_CANCELLED


async def test_a_rerun_resets_the_cancel_flag_and_the_active_set(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = manager.create(
        case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="validate", concurrency=2
    )
    batch_id = str(created["batchId"])
    batch = manager._batches[batch_id]
    batch.items[0].status = CASE_CANCELLED
    batch.items[0].cancel_requested = True
    batch.active_indices = [0]

    await manager.rerun_item(batch_id, 0)
    finished = await _wait_status(manager, batch_id, "finished")

    assert finished["items"][0]["status"] == CASE_PASSED
    assert batch.items[0].cancel_requested is False
    assert batch.active_indices == []
    # A single-item rerun is sequential even when the batch is configured for more.
    assert batch.active_concurrency == 1


async def test_retry_keeps_the_batch_concurrency(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = manager.create(
        case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="validate", concurrency=4
    )
    batch_id = str(created["batchId"])
    manager._batches[batch_id].items[0].status = CASE_FAILED

    retried = await manager.retry(batch_id)

    assert retried["concurrency"] == 4


async def test_a_one_item_batch_ignores_the_concurrency_setting(tmp_path: Path) -> None:
    runs = _FakeRuns(_finish)
    manager = _manager(tmp_path, runs)
    created = manager.create(
        case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="validate", concurrency=4
    )
    batch = manager._batches[str(created["batchId"])]

    await manager.start(str(created["batchId"]))
    await _wait_status(manager, str(created["batchId"]), "finished")

    # One item has nothing to overlap with, so it keeps the historical (sequential) behaviour.
    assert batch.active_concurrency == 1


async def test_cancelling_a_modify_item_marks_its_baseline_cancelled(tmp_path: Path) -> None:
    runs = _FakeRuns(_finish)
    manager = _manager(tmp_path, runs)
    item = CaseItem(case_id=1, name="modify", protocol=PROTOCOL_A2A_T)
    item.run_id = "run-main"
    item.baseline = {"runId": "run-base", "status": "running"}

    await manager._cancel_item_runs(item)

    assert runs.cancelled == ["run-main", "run-base"]
    assert item.baseline["status"] == "cancelled"


async def test_a_cancelled_sdk_checkout_does_not_reuse_its_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling the item cannot stop the thread; its client must not go back to the pool."""
    manager = EvalBatchManager(
        runs=_FakeRuns(_finish),  # type: ignore[arg-type]
        catalog=CaseCatalog(),
        protocol_defaults=PROTOCOL_DEFAULTS,
        server_url=None,
        server_auth_token=None,
        runs_dir=tmp_path,
        language="en-US",
        model_mode="offline",
        prompt_client=None,
    )
    built: list[Path] = []
    started = threading.Event()
    release = threading.Event()
    real_run_case = service.run_case

    def fake_build(
        env_path: Path | None = None, *, model_mode: str | None = None
    ) -> tuple[Any, Path]:
        del env_path, model_mode
        directory = tmp_path / f"env-{len(built) + 1}"
        directory.mkdir()
        built.append(directory)
        return _PromptClient(), directory

    def blocking_run_case(
        case: Mapping[str, Any], client: Any, *, stop_after: str = "validate"
    ) -> Any:
        started.set()
        release.wait(timeout=10)
        return real_run_case(case, client, stop_after=stop_after)

    monkeypatch.setattr(service, "build_client_with_env", fake_build)
    monkeypatch.setattr(service, "run_case", blocking_run_case)

    task = asyncio.create_task(
        manager._run_case_with_client(CASES[1], "validate", None, concurrent=True)
    )
    deadline = asyncio.get_running_loop().time() + 10.0
    while not started.is_set() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    assert started.is_set()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The retired client is not idle in the pool, so the worker cannot be shared.
    assert manager._prompt_pool["offline"].empty() is True

    release.set()
    deadline = asyncio.get_running_loop().time() + 10.0
    second = asyncio.create_task(
        manager._run_case_with_client(CASES[1], "validate", None, concurrent=True)
    )
    await asyncio.wait_for(second, timeout=10.0)

    assert len(built) == 2
    assert manager._prompt_pool["offline"].qsize() == 1


async def test_the_concurrent_sdk_stage_pools_clients_per_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent SDK stages must not share one ``A2ATClient`` and must build serially."""
    manager = EvalBatchManager(
        runs=_FakeRuns(_finish),  # type: ignore[arg-type]
        catalog=CaseCatalog(),
        protocol_defaults=PROTOCOL_DEFAULTS,
        server_url=None,
        server_auth_token=None,
        runs_dir=tmp_path,
        language="en-US",
        model_mode="offline",
        prompt_client=None,
    )
    built: list[Path] = []
    building = 0
    max_building = 0
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    def fake_build(
        env_path: Path | None = None, *, model_mode: str | None = None
    ) -> tuple[Any, Path]:
        del env_path, model_mode
        nonlocal building, max_building
        with lock:
            building += 1
            max_building = max(max_building, building)
        directory = tmp_path / f"env-{len(built) + 1}"
        directory.mkdir()
        built.append(directory)
        with lock:
            building -= 1
        return _PromptClient(), directory

    real_run_case = service.run_case

    def blocking_run_case(
        case: Mapping[str, Any], client: Any, *, stop_after: str = "validate"
    ) -> Any:
        barrier.wait(timeout=10)
        return real_run_case(case, client, stop_after=stop_after)

    monkeypatch.setattr(service, "build_client_with_env", fake_build)
    monkeypatch.setattr(service, "run_case", blocking_run_case)

    case = CASES[1]
    outcomes = await asyncio.gather(
        *(
            manager._run_case_with_client(case, "validate", None, concurrent=True)
            for _ in range(4)
        )
    )
    assert all(outcome.error is None for outcome in outcomes)
    assert len(built) == 4
    assert max_building == 1
    assert len(manager._prompt_env_dirs) == 4

    # The next four calls reuse the idle clients instead of building again.
    barrier.reset()
    await asyncio.gather(
        *(
            manager._run_case_with_client(case, "validate", None, concurrent=True)
            for _ in range(4)
        )
    )
    assert len(built) == 4

    manager._close_prompt_pool()
    assert manager._prompt_env_dirs == []
    assert not any(directory.exists() for directory in built)


def _wide(document: dict[str, object]) -> dict[str, Any]:
    """Return a batch document as an untyped mapping so nested assertions stay readable."""
    return cast(dict[str, Any], document)


def _required(document: dict[str, object] | None) -> dict[str, Any]:
    """Return one batch snapshot, failing the test when it is missing."""
    assert document is not None
    return _wide(document)


def test_rounds_default_to_one_and_reject_out_of_range(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = _wide(
        manager.create(case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="validate")
    )
    assert created["rounds"] == DEFAULT_ROUNDS == 1
    assert [item["round"] for item in created["items"]] == [1]

    maximum = _wide(
        manager.create(
            case_ids=[1],
            protocols=[PROTOCOL_A2A_T],
            stop_after="validate",
            rounds=MAX_ROUNDS,
        )
    )
    assert maximum["rounds"] == MAX_ROUNDS == 8
    assert [item["round"] for item in maximum["items"]] == list(range(1, MAX_ROUNDS + 1))

    for value in (0, MAX_ROUNDS + 1, True, 2.0, "3"):
        with pytest.raises(ValueError):
            manager.create(
                case_ids=[1],
                protocols=[PROTOCOL_A2A_T],
                stop_after="validate",
                rounds=cast(Any, value),
            )


def test_a_multi_round_batch_cannot_keep_its_tasks(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))

    with pytest.raises(ValueError):
        manager.create(
            case_ids=[1],
            protocols=[PROTOCOL_A2A_T],
            stop_after="validate",
            rounds=2,
            keep_tasks=True,
        )

    single = _wide(
        manager.create(
            case_ids=[1],
            protocols=[PROTOCOL_A2A_T],
            stop_after="validate",
            keep_tasks=True,
        )
    )
    assert single["keepTasks"] is True


def test_rounds_expand_round_major_and_keep_the_single_pass_order(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    single = _wide(
        manager.create(case_ids=[1, 2], protocols=[PROTOCOL_A2A_T], stop_after="send")
    )
    assert [(item["caseId"], item["protocol"]) for item in single["items"]] == [
        (1, PROTOCOL_A2A_T),
        (2, PROTOCOL_A2A_T),
    ]

    multi = _wide(
        manager.create(
            case_ids=[1, 2], protocols=[PROTOCOL_A2A_T], stop_after="send", rounds=2
        )
    )
    assert [(item["round"], item["caseId"], item["protocol"]) for item in multi["items"]] == [
        (1, 1, PROTOCOL_A2A_T),
        (1, 2, PROTOCOL_A2A_T),
        (2, 1, PROTOCOL_A2A_T),
        (2, 2, PROTOCOL_A2A_T),
    ]


async def test_rounds_run_with_a_barrier_between_passes(tmp_path: Path) -> None:
    runs = _GatedRuns()
    manager = _manager(tmp_path, runs)
    created = _wide(
        manager.create(
            case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="send", rounds=2
        )
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    await _wait_in_flight(runs, 1)

    snapshot = _required(manager.get(batch_id))
    assert snapshot["currentRound"] == 1
    assert snapshot["items"][0]["status"] == CASE_RUNNING
    assert snapshot["items"][1]["status"] == CASE_PENDING
    assert len(runs.calls) == 1

    runs.release(next(iter(runs.gates)))
    deadline = asyncio.get_running_loop().time() + 10.0
    while len(runs.calls) < 2 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    assert len(runs.calls) == 2

    snapshot = _required(manager.get(batch_id))
    assert snapshot["currentRound"] == 2
    assert snapshot["items"][0]["status"] == CASE_PASSED
    assert snapshot["items"][1]["status"] == CASE_RUNNING

    runs.release_all()
    finished = _wide(await _wait_status(manager, batch_id, "finished"))
    assert finished["summary"]["passed"] == 2
    assert [entry["round"] for entry in finished["roundSummaries"]] == [1, 2]
    assert all(
        entry["summary"]["passed"] == 1 and entry["summary"]["total"] == 1
        for entry in finished["roundSummaries"]
    )
    assert finished["crossRound"] == {"stable": 1, "total": 1}


async def test_cancelling_one_round_leaves_the_other_rounds_running(tmp_path: Path) -> None:
    runs = _GatedRuns()
    manager = _manager(tmp_path, runs)
    created = _wide(
        manager.create(
            case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="send", rounds=2
        )
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    await _wait_in_flight(runs, 1)

    await manager.cancel_item(batch_id, 1)
    snapshot = _required(manager.get(batch_id))
    assert snapshot["items"][1]["status"] == CASE_CANCELLED

    runs.release_all()
    finished = _wide(await _wait_status(manager, batch_id, "finished"))
    assert len(runs.calls) == 1
    assert finished["summary"]["passed"] == 1
    assert finished["summary"]["cancelled"] == 1


async def test_a_rerun_touches_only_its_own_round_row(tmp_path: Path) -> None:
    runs = _FakeRuns(_finish)
    manager = _manager(tmp_path, runs)
    created = _wide(
        manager.create(
            case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="send", rounds=2
        )
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    finished = _wide(await _wait_status(manager, batch_id, "finished"))
    first_run = finished["items"][0]["runId"]
    second_run = finished["items"][1]["runId"]
    assert finished["items"][1]["round"] == 2

    await manager.rerun_item(batch_id, 1)
    rerun = _wide(await _wait_status(manager, batch_id, "finished"))
    assert rerun["items"][0]["runId"] == first_run
    assert rerun["items"][1]["runId"] != second_run
    assert rerun["items"][1]["round"] == 2


async def test_cancelling_a_multi_round_batch_cancels_every_round(tmp_path: Path) -> None:
    runs = _GatedRuns()
    manager = _manager(tmp_path, runs)
    created = _wide(
        manager.create(
            case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="send", rounds=3
        )
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    await _wait_in_flight(runs, 1)

    await manager.cancel(batch_id)
    finished = _wide(await _wait_status(manager, batch_id, "cancelled"))
    assert len(runs.calls) == 1
    assert all(item["status"] == CASE_CANCELLED for item in finished["items"])


async def test_retry_keeps_the_round_count_and_retries_only_failed_cases(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = _wide(
        manager.create(
            case_ids=[1, 2], protocols=[PROTOCOL_A2A_T], stop_after="validate", rounds=2
        )
    )
    batch_id = str(created["batchId"])
    batch = manager._batches[batch_id]
    batch.items[0].status = CASE_FAILED
    batch.items[3].status = CASE_ERROR

    retried = _wide(await manager.retry(batch_id))

    assert retried["rounds"] == 2
    assert retried["keepTasks"] is False
    assert [(item["round"], item["caseId"]) for item in retried["items"]] == [
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 2),
    ]
    assert retried["caseIds"] == [1, 2]


def test_the_round_summaries_count_each_round_and_the_stable_cases(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = _wide(
        manager.create(
            case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="validate", rounds=2
        )
    )
    batch_id = str(created["batchId"])
    batch = manager._batches[batch_id]
    batch.items[0].status = CASE_PASSED
    batch.items[1].status = CASE_FAILED

    snapshot = _required(manager.get(batch_id))
    assert snapshot["roundSummaries"][0]["summary"]["passed"] == 1
    assert snapshot["roundSummaries"][1]["summary"]["failed"] == 1
    assert snapshot["crossRound"] == {"stable": 0, "total": 1}

    batch.items[1].status = CASE_PASSED
    snapshot = _required(manager.get(batch_id))
    assert snapshot["crossRound"] == {"stable": 1, "total": 1}


def test_a_single_round_batch_reports_no_cross_round_stability(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = _wide(
        manager.create(case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="validate")
    )

    assert created["rounds"] == 1
    assert created["currentRound"] == 1
    assert created["roundSummaries"][0]["round"] == 1
    assert created["crossRound"] == {"stable": 0, "total": 0}


def test_a_persisted_multi_round_batch_round_trips(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = _wide(
        manager.create(
            case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="validate", rounds=2
        )
    )
    batch_id = str(created["batchId"])

    reloaded = _manager(tmp_path, _FakeRuns(_finish))
    snapshot = _required(reloaded.get(batch_id))
    assert snapshot["rounds"] == 2
    assert snapshot["currentRound"] == 1
    assert [item["round"] for item in snapshot["items"]] == [1, 2]
    assert [entry["round"] for entry in snapshot["roundSummaries"]] == [1, 2]


async def test_a_full_flow_item_does_not_run_the_sdk_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full-flow batch lets the run generate and validate the Task-T; no second SDK pass."""
    calls: list[str] = []

    def forbidden_run_case(*args: object, **kwargs: object) -> Any:
        calls.append("run_case")
        raise AssertionError("a full-flow item must not run the SDK preflight")

    monkeypatch.setattr(service, "run_case", forbidden_run_case)
    runs = _FakeRuns(_finish)
    manager = _manager(tmp_path, runs)
    created = _wide(
        manager.create(case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="send")
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    snapshot = await _wait_status(manager, batch_id, "finished")

    assert calls == []
    assert runs.calls, "the full flow must still start the run"
    # The intent stage is judged from the downstream payload, not from a preflight extraction;
    # the SDK-only task_t/validate stages never exist for a full-flow item.
    items = cast(list[dict[str, Any]], snapshot["items"])
    assert set(items[0]["stages"]) == {"intent", "send"}


async def test_a_validate_only_item_still_runs_the_sdk_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An SDK-only batch starts no run, so the tester must run the three SDK stages itself."""
    calls: list[str] = []
    real_run_case = service.run_case

    def recording_run_case(
        case: Mapping[str, Any], client: Any, *, stop_after: str = "validate"
    ) -> Any:
        calls.append(stop_after)
        return real_run_case(case, client, stop_after=stop_after)

    monkeypatch.setattr(service, "run_case", recording_run_case)
    runs = _FakeRuns(_finish)
    manager = _manager(tmp_path, runs)
    created = _wide(
        manager.create(case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="validate")
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    snapshot = await _wait_status(manager, batch_id, "finished")

    assert calls == ["validate"]
    assert runs.calls == []
    items = cast(list[dict[str, Any]], snapshot["items"])
    assert {"intent", "task_t", "validate"} <= set(items[0]["stages"])


async def test_the_item_snapshot_exposes_the_langfuse_warning(tmp_path: Path) -> None:
    runs = _FakeRuns(_finish)
    manager = _manager(tmp_path, runs)
    created = _wide(
        manager.create(case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="send")
    )
    batch_id = str(created["batchId"])
    await manager.start(batch_id)
    await _wait_status(manager, batch_id, "finished")

    run = next(iter(runs.runs.values()))
    run.langfuse = {
        "enabled": True,
        "warning": "Langfuse ingestion rejected (HTTP 403): suspended",
    }
    snapshot = _required(manager.get(batch_id))
    assert "HTTP 403" in snapshot["items"][0]["langfuseWarning"]


_NEGOTIATION_MESSAGE = """## Feasibility Negotiation
Reduce the guaranteed throughput target to 2Mbps for 08:00-18:00.
"""


def _message_case() -> dict[str, Any]:
    """Return a negotiation case whose expected server message is pinned."""
    case = copy.deepcopy(CASES[26])
    negotiation = case["expect"]["protocols"]["a2a-t"]["negotiation"]
    negotiation["expected"] = True
    negotiation["expected_rounds"] = 1
    negotiation["kinds"] = ["feasibility"]
    negotiation["messages"] = [[_NEGOTIATION_MESSAGE]]
    return case


def _negotiation_batch(item: CaseItem) -> _Batch:
    return _Batch(
        batch_id="batch",
        case_ids=[item.case_id],
        protocols=[PROTOCOL_A2A_T],
        stop_after="negotiate",
        approval=None,
        timeout_seconds=1.0,
        items=[item],
    )


async def _run_negotiate(
    tmp_path: Path,
    *,
    content: str,
    case: Mapping[str, Any] | None = None,
) -> StageResult:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.status = "running"
    run.proposals.append(  # type: ignore[arg-type]
        SimpleNamespace(round=1, kind="feasibility", content=content)
    )
    item = CaseItem(case_id=26, name="case", protocol=PROTOCOL_A2A_T)
    await manager._wait_for_stage(
        run,
        item,
        _negotiation_batch(item),
        ("negotiate",),
        case=case if case is not None else _message_case(),
        timeout_seconds=1.0,
        working_timeout_seconds=1.0,
    )
    return item.stages["negotiate"]


async def test_negotiate_stage_judges_the_expected_server_message(tmp_path: Path) -> None:
    """The case's expected Negotiation-T is compared with the observed proposal content."""
    paraphrased = (
        "## Feasibility Negotiation\n"
        "Please reduce the guaranteed throughput target to 2Mbps between 08:00 and 18:00.\n"
    )

    result = await _run_negotiate(tmp_path, content=paraphrased)

    assert result.status == STAGE_PASSED
    assert result.actual["messages"] == [
        {"round": 1, "kind": "feasibility", "content": paraphrased}
    ]
    assert result.detail is not None
    assert result.detail["messageMatch"]["matched"] is True


async def test_negotiate_stage_fails_on_a_different_server_message(tmp_path: Path) -> None:
    """A message stating another rate fails and points at the diverging round."""
    result = await _run_negotiate(
        tmp_path,
        content="## Feasibility Negotiation\nReduce the target to 4Mbps.\n",
    )

    assert result.status == STAGE_FAILED
    assert [diff["path"] for diff in result.diffs] == ["negotiate.messages[1]"]
    assert "rate:2" in result.diffs[0]["reason"]


async def test_negotiate_stage_keeps_the_round_count_contract(tmp_path: Path) -> None:
    """Without a message expectation only the round count and the kinds are judged."""
    result = await _run_negotiate(tmp_path, content="anything", case=copy.deepcopy(CASES[26]))

    assert "messages" not in result.actual
    assert result.status == STAGE_PASSED


def _blocked_validation_run(slot_code: str) -> RunState:
    run = RunState(run_id="run", scenario="night", mode=MODE_SCRIPTED, transport="sse")
    run.status = "finished"
    run.traces.append(
        {
            "kind": "task_validation",
            "detail": {
                "verdict": "blocked",
                "reason": slot_code,
                "errors": [
                    {
                        "slotName": "Gain target",
                        "code": slot_code,
                        "message": "the value is not allowed",
                        "facts": {"slot_label": "Gain target", "actual": "20% - 30%"},
                    }
                ],
            },
        }
    )
    return run


def test_validate_stage_judges_the_expected_slot_errors(tmp_path: Path) -> None:
    """The full-flow validation compares the observed slot errors with the case's expectation."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    case = copy.deepcopy(CASES[16])
    case["expect"]["protocols"]["a2a-t"]["slot_errors"] = {
        "mode": "any",
        "expect": [{"code": "slot.constraint_violated", "contains": ["gain"]}],
    }
    item = CaseItem(case_id=16, name="case", protocol=PROTOCOL_A2A_T)

    manager._judge_run_validation(
        item, _blocked_validation_run("slot.constraint_violated"), case
    )

    result = item.stages["validate"]
    assert result.status == STAGE_PASSED
    assert result.detail is not None
    assert result.detail["slotErrorMatch"]["matched"] is True


def test_validate_stage_fails_on_an_unexpected_slot_error(tmp_path: Path) -> None:
    """A block reported for another reason is a mismatch, not a pass on the verdict alone."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    case = copy.deepcopy(CASES[16])
    case["expect"]["protocols"]["a2a-t"]["slot_errors"] = {
        "mode": "cover",
        "expect": [{"code": "slot.constraint_violated"}],
    }
    item = CaseItem(case_id=16, name="case", protocol=PROTOCOL_A2A_T)

    manager._judge_run_validation(
        item, _blocked_validation_run("slot.rule_violation"), case
    )

    result = item.stages["validate"]
    assert result.status == STAGE_FAILED
    assert [diff["path"] for diff in result.diffs] == ["validate.slotErrors[0]"]


def test_validate_stage_records_slot_errors_without_an_expectation(tmp_path: Path) -> None:
    """A case without the block keeps judging the verdict only."""
    manager = _manager(tmp_path, _FakeRuns(_finish))
    item = CaseItem(case_id=16, name="case", protocol=PROTOCOL_A2A_T)

    manager._judge_run_validation(
        item, _blocked_validation_run("slot.rule_violation"), copy.deepcopy(CASES[16])
    )

    result = item.stages["validate"]
    assert result.status == STAGE_PASSED
    assert result.diffs == []
    assert result.detail is not None
    assert "expectedSlotErrors" not in result.detail


def test_an_old_v3_batch_loads_as_a_single_round(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeRuns(_finish))
    created = _wide(
        manager.create(case_ids=[1], protocols=[PROTOCOL_A2A_T], stop_after="validate")
    )
    batch_id = str(created["batchId"])
    path = tmp_path / "tester" / f"{batch_id}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema"] = "osp-tester/v3"
    for key in ("rounds", "currentRound", "roundSummaries", "crossRound"):
        document.pop(key, None)
    for item in document["items"]:
        item.pop("round", None)
    path.write_text(json.dumps(document), encoding="utf-8")

    reloaded = _manager(tmp_path, _FakeRuns(_finish))
    snapshot = _required(reloaded.get(batch_id))
    assert snapshot["rounds"] == 1
    assert snapshot["currentRound"] == 1
    assert [item["round"] for item in snapshot["items"]] == [1]
