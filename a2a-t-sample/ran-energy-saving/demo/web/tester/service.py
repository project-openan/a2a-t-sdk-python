"""Batch service of the redesigned tester console.

A batch is a list of ``(case, protocol)`` items. Each item runs the SDK-owned stages
(``intent`` / ``task_t`` / ``validate``) in-process, then the protocol runtime stages
(``send`` / ``negotiate`` / ``final``) through the shared :class:`RunManager`. The case YAML is the
source of truth; the Excel workbook is no longer an input.

Negotiation is scripted per case:

* A2A-T replays the Q-column Task-T replies as ``NegotiationDecision`` objects.
* A2A replays the P-column OSS answers as the pure ``INPUT_REQUIRED`` answers.

A Modify A2A-T item first seeds a baseline Create task, then runs the Modify request against it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx
from eval.osp.catalog import PROTOCOL_A2A, PROTOCOL_A2A_T, CaseCatalog
from eval.osp.compare import compare_intent
from eval.osp.followup import followup_case, followup_input, has_followup, is_blocked_case
from eval.osp.runner import (
    STAGE_ERROR,
    STAGE_FAILED,
    STAGE_PASSED,
    STAGE_RECORDED,
    build_client,
    run_case,
)
from eval.osp.runner import (
    STAGE_ORDER as SDK_STAGE_ORDER,
)
from protocol.client_runtime import MODE_AUTO, MODE_SCRIPTED
from protocol.client_service import STATUS_FAILED, RunManager, RunState
from protocol.run_metrics import build_run_metrics
from shared.config import MODEL_MODE_LIVE, MODEL_MODE_OFFLINE
from shared.run_constants import (
    FINAL_STATE_DECISION_UNGROUNDED,
    FINAL_STATE_INPUT_INVALID,
    FINAL_STATE_INTENT_NOT_CONFIRMED,
    FINAL_STATE_TASK_FAILED,
    FINAL_STATE_TASK_REJECTED,
    PROTOCOL_PURE,
    TASK_STATE_SUBMITTED,
)
from shared.run_constants import (
    PROTOCOL_A2A_T as RUNTIME_A2A_T,
)

from demo.web.tester.decisions import a2at_decisions, pure_answers
from demo.web.tester.export_view import build_answer, read_traceparent

_LOGGER = logging.getLogger("ran_energy_saving.tester")

#: Every stage the console can run, in execution order.
ALL_STAGES: Final[tuple[str, ...]] = (
    "intent",
    "task_t",
    "validate",
    "send",
    "negotiate",
    "accepted",
    "working",
    "final",
)

#: Stage status used by ``working`` when the Task never left ``SUBMITTED``: yellow, not red.
STAGE_WAITING: Final[str] = "waiting"
#: Stage status used when the case expects no send, so the stage cannot be judged.
STAGE_SKIPPED: Final[str] = "skipped"

#: Batch / item states.
BATCH_DRAFT: Final[str] = "draft"
BATCH_RUNNING: Final[str] = "running"
BATCH_FINISHED: Final[str] = "finished"
BATCH_CANCELLED: Final[str] = "cancelled"

CASE_PENDING: Final[str] = "pending"
CASE_RUNNING: Final[str] = "running"
CASE_PASSED: Final[str] = "passed"
CASE_FAILED: Final[str] = "failed"
CASE_CANCELLED: Final[str] = "cancelled"
CASE_ERROR: Final[str] = "error"
#: At least one stage is ``waiting`` but none failed: the request is not proven either way.
CASE_PARTIAL: Final[str] = "partial"

#: Decision modes of a batch.
DECISION_MODE_SCRIPTED: Final[str] = "scripted"
DECISION_MODE_LIVE: Final[str] = "live"
DECISION_MODES: Final[tuple[str, ...]] = (DECISION_MODE_SCRIPTED, DECISION_MODE_LIVE)

#: Batch decision mode used when the caller does not choose one. An online model should exercise
#: the model decisions by default; an offline process can only run the scripted mode.
DEFAULT_DECISION_MODE: Final[str] = DECISION_MODE_LIVE


def default_decision_mode(model_mode: str) -> str:
    """Return the default decision mode of a tester process in one model mode."""
    return DECISION_MODE_SCRIPTED if model_mode == MODEL_MODE_OFFLINE else DEFAULT_DECISION_MODE

#: Persisted batch schema; v1/v2 batches are still readable.
SCHEMA: Final[str] = "osp-tester/v3"
SCHEMA_V1: Final[str] = "osp-tester/v1"
SCHEMA_V2: Final[str] = "osp-tester/v2"
_SCHEMAS: Final[frozenset[str]] = frozenset({SCHEMA, SCHEMA_V2, SCHEMA_V1})

#: Task states that end the item in red.
_FAILED_TASK_STATES: Final[frozenset[str]] = frozenset(
    {"TASK_STATE_FAILED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED"}
)
#: The A2A state a pure outer Task reports once the OSS Agent accepted the Create. It is also the
#: only non-terminal state a pure Modify can continue: ``COMPLETED`` rejects the continuation and
#: ``INPUT_REQUIRED`` would route the Modify text as an answer to the pending question.
TASK_STATE_WORKING: Final[str] = "TASK_STATE_WORKING"
#: Task states that prove the Task moved past SUBMITTED.
_WORKING_TASK_STATES: Final[frozenset[str]] = frozenset(
    {TASK_STATE_WORKING, "TASK_STATE_COMPLETED"}
)
#: Task states that prove the downstream accepted the Create/Modify request. A2A-T reports
#: :data:`TASK_STATE_SUBMITTED` (the machine-readable success signal); pure A2A starts its outer
#: Task at ``TASK_STATE_WORKING``. ``INPUT_REQUIRED`` is not acceptance: the negotiation is still
#: in progress, so the item keeps observing.
_ACCEPTED_TASK_STATES: Final[frozenset[str]] = frozenset(
    {TASK_STATE_SUBMITTED}
) | _WORKING_TASK_STATES
#: Client outcomes that prove the request ended before any Task was sent.
_PRE_SEND_TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {
        FINAL_STATE_INPUT_INVALID,
        FINAL_STATE_DECISION_UNGROUNDED,
        FINAL_STATE_INTENT_NOT_CONFIRMED,
    }
)

#: Message of a stage whose assertion the case disabled.
_ASSERTION_OFF_MESSAGE: Final[str] = "用例关闭了该断言。"

#: Message of a negotiation case's Task-T stage: the expectation is the post-negotiation value.
_NEGOTIATED_TASK_T_MESSAGE: Final[str] = (
    "期望 Task-T 是协商后的结果；SDK runner 只看到初始请求，因此只记录。"
)

#: Message of a pure-A2A negotiation stage run against the simulator.
_SIM_PURE_NEGOTIATION_MESSAGE: Final[str] = (
    "模拟器不重现厂商纯 A2A 对话轮次；真实轮次需在真机验证，因此只记录。"
)

#: Message of the intent stage when the downstream accepts a Task without returning its reading.
_NO_SERVER_INTENT_MESSAGE: Final[str] = (
    "下游的 TASK_STATE_SUBMITTED 报文没有结构化意图，意图阶段只记录。"
)

#: Message of the intent stage against the simulator: its payload key set is not the vendor's.
_SIM_SERVER_INTENT_MESSAGE: Final[str] = (
    "模拟器返回的意图键集与厂商不同，只记录，不据此判定。"
)

#: Message of the intent stage when the case carries no expectation to compare with.
_NO_EXPECTED_INTENT_MESSAGE: Final[str] = "用例没有期望意图，只记录。"

#: Message of a failed intent stage: the case asks what the server understood.
_SERVER_INTENT_MISMATCH_MESSAGE: Final[str] = "服务端意图理解结果与期望不一致。"

#: Scenario used to seed a Modify target task; it is a complete request that creates a task.
BASELINE_SCENARIO: Final[str] = "night"

#: Region placeholder of the baseline request; ``_baseline_request`` replaces it with the region
#: the configured NMS (or simulator) actually serves.
BASELINE_REGION: Final[str] = "A2ATestRegion"

#: Default activation window of the simulator. Only used by the fallback baseline: a configured
#: window that differs from it replaces the day plan with one segment, because two segments
#: covering the same hours would describe an ambiguous prior task.
BASELINE_WINDOW: Final[str] = "00:00~06:00"

#: One-day rate plan of the shared fallback baseline: the Task-T the testers use as the manual
#: baseline (``00:00-07:00 2Mbps``, ``07:00-18:00 10Mbps``, ``18:00-24:00 20Mbps``). It is a
#: complete Create the vendor accepts without a pre-task negotiation, so baselines reuse it instead
#: of inventing a per-site plan. The plan is written in prose: an explicit
#: ``{HH:MM~HH:MM,NMbps}`` list is rewritten by the scripted provider into the Task Context and
#: then fails semantic validation on the offline path. Every Modify case ships its own
#: ``baseline.request`` (see eval/osp/README.md 3.5); this plan is the fallback.
BASELINE_SCHEDULE: Final[str] = (
    "guarantee 2Mbps from 00:00 to 07:00, 10Mbps from 07:00 to 18:00 "
    "and 20Mbps from 18:00 to 24:00"
)

#: Fallback baseline request used when a Modify case declares no ``baseline.request``.
BASELINE_REQUEST: Final[str] = (
    f"Create an energy saving task for {BASELINE_REGION}: {BASELINE_SCHEDULE}."
)

#: Polling interval of the batch runner.
_POLL_SECONDS: Final[float] = 0.2

#: Default observation window of the ``working`` stage when the batch does not override it.
DEFAULT_WORKING_TIMEOUT_SECONDS: Final[float] = 60.0

#: Grace after a stop-after stage before the run is cancelled.
_CANCEL_GRACE_SECONDS: Final[float] = 1.0

#: How long the pure-A2A negotiate stage waits for the simulator's generic question before it is
#: recorded. The vendor dialogue and round count come from the OSP P column and cannot be
#: reproduced against nms-sim, so waiting for the workbook's round count would only stall the batch.
_SIM_PURE_SETTLE_SECONDS: Final[float] = 30.0


def _now_iso() -> str:
    """Return the current UTC time in ISO 8601 form."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class StageResult:
    """Result of one stage of one item."""

    stage: str
    status: str
    message: str = ""
    expected: object = None
    actual: object = None
    diffs: list[dict[str, object]] = field(default_factory=list)
    duration_ms: int = 0

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation."""
        return {
            "stage": self.stage,
            "status": self.status,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
            "diffs": self.diffs,
            "durationMs": self.duration_ms,
        }


@dataclass
class CaseItem:
    """One runnable ``(case, protocol)`` pair."""

    case_id: int
    name: str
    protocol: str
    status: str = CASE_PENDING
    stages: dict[str, StageResult] = field(default_factory=dict)
    run_id: str | None = None
    duration_ms: int | None = None
    tokens: dict[str, object] = field(default_factory=dict)
    error: str | None = None
    baseline: dict[str, object] | None = None
    scratch: str | None = None
    #: Previous executions of this item (a rerun appends before it resets the row).
    attempts: list[dict[str, object]] = field(default_factory=list)
    #: Vendor-facing trace id of the run (``run.trace_id``, 32 lowercase hex digits).
    trace_id: str = ""
    #: OSP 11-key structured intent extracted by the tester's SDK ``intent`` stage.
    intent: dict[str, object] | None = None
    #: Structured Task payload the downstream returned with ``TASK_STATE_SUBMITTED``: the
    #: downstream's own reading of the Task-T, and the oracle the arbitration sheet compares with.
    server_intent: dict[str, object] | None = None
    #: Raw/normalized operator request and how the intent was produced.
    intent_meta: dict[str, object] = field(default_factory=dict)
    #: Case metadata and expectations frozen when the item was collected.
    refs: dict[str, object] = field(default_factory=dict)
    #: Reference-workbook answer/origin object (columns 76 and 87).
    answer: dict[str, object] | None = None
    #: The interception the case expects before its follow-up request (``blocked_followup``).
    interception: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation."""
        return {
            "caseId": self.case_id,
            "name": self.name,
            "protocol": self.protocol,
            "status": self.status,
            "stages": {stage: result.to_dict() for stage, result in self.stages.items()},
            "runId": self.run_id,
            "durationMs": self.duration_ms,
            "tokens": dict(self.tokens),
            "error": self.error,
            "baseline": self.baseline,
            "scratch": self.scratch,
            "attempts": [dict(attempt) for attempt in self.attempts],
            "traceId": self.trace_id,
            "intent": self.intent,
            "serverIntent": self.server_intent,
            "intentMeta": dict(self.intent_meta),
            "refs": dict(self.refs),
            "answer": self.answer,
            "interception": self.interception,
        }


@dataclass
class _Batch:
    """Mutable state of one tester batch."""

    batch_id: str
    case_ids: list[int]
    protocols: list[str]
    stop_after: str
    approval: str | None
    timeout_seconds: float | None
    items: list[CaseItem]
    working_timeout_seconds: float | None = None
    decision_mode: str = DECISION_MODE_SCRIPTED
    #: Keep every Task this batch created alive after its item ends. Off by default: a finished
    #: tester item releases its remote Task so a long acceptance run does not exhaust the NMS quota.
    keep_tasks: bool = False
    transport: str = "sse"
    discovery: str = "direct"
    owner_id: str = ""
    status: str = BATCH_DRAFT
    created_at: str = field(default_factory=_now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    current_index: int | None = None
    cancel_requested: bool = False
    error: str | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)


class EvalBatchManager:
    """Own the tester batches of one Web UI process."""

    def __init__(
        self,
        *,
        runs: RunManager,
        catalog: CaseCatalog,
        protocol_defaults: Mapping[str, Mapping[str, str]],
        server_url: str | None,
        server_auth_token: str | None,
        runs_dir: Path | None = None,
        nms_sim: bool = False,
        language: str = "",
        model_mode: str = "",
        model_label: str = "",
        metrics_grace_seconds: float = 0.0,
        prompt_client: Any | None = None,
    ) -> None:
        self._runs = runs
        self._catalog = catalog
        self._protocol_defaults = protocol_defaults
        self._server_url = (server_url or "").rstrip("/")
        self._server_auth_token = server_auth_token
        self._history_dir = (Path(runs_dir) / "tester") if runs_dir is not None else None
        self._nms_sim = nms_sim
        self._language = language
        self._model_mode = model_mode
        self._model_label = model_label
        self._metrics_grace_seconds = max(0.0, metrics_grace_seconds)
        self._prompt_client = prompt_client
        #: One SDK client per model mode: a live batch must not reuse the offline scripted client.
        self._prompt_clients: dict[str, Any] = {}
        self._prompt_errors: dict[str, str] = {}
        self._baseline_request = _baseline_request()
        self._sdk_lock = asyncio.Lock()
        self._batches: dict[str, _Batch] = {}

    # ------------------------------------------------------------------
    # Catalog and batch lifecycle
    # ------------------------------------------------------------------

    def catalog(self) -> list[dict[str, object]]:
        """Return every catalog row."""
        return [summary.to_dict() for summary in self._catalog.summaries()]

    def create(
        self,
        *,
        case_ids: Sequence[int],
        protocols: Sequence[str],
        stop_after: str,
        approval: str | None = None,
        timeout_seconds: float | None = None,
        working_timeout_seconds: float | None = None,
        decision_mode: str | None = None,
        keep_tasks: bool = False,
        owner: str = "",
    ) -> dict[str, object]:
        """Register one draft batch from the selected cases and protocols.

        ``approval``/``timeout_seconds``/``working_timeout_seconds`` may be ``None``: the batch then
        uses each case's own ``runner`` block (or the process defaults for a scratch request).
        ``decision_mode`` defaults to the process mode: live for an online model, scripted offline.
        ``keep_tasks`` keeps every Task alive after its item ends (long-task verification); by
        default each item releases its remote Task once its evidence is collected.
        """
        if stop_after not in ALL_STAGES:
            raise ValueError(f"unknown stop_after {stop_after!r}")
        resolved_approval = _validate_approval(approval)
        resolved_decision_mode = self._validate_decision_mode(
            decision_mode or default_decision_mode(self._model_mode)
        )
        selected_protocols = [protocol for protocol in protocols if protocol in {PROTOCOL_A2A_T, PROTOCOL_A2A}]
        if not selected_protocols:
            raise ValueError("至少选择一个协议。")
        if PROTOCOL_A2A in selected_protocols and stop_after in SDK_STAGE_ORDER:
            raise ValueError(
                "A2A 协议没有 SDK 阶段；请把执行到设为 send / negotiate / accepted / working / final。"
            )
        items: list[CaseItem] = []
        for case_id in case_ids:
            case = self._catalog.get(case_id)
            if case is None:
                continue
            for protocol in selected_protocols:
                items.append(CaseItem(case_id=case_id, name=str(case["name"]), protocol=protocol))
        if not items:
            raise ValueError("没有可执行的用例。")
        defaults = self._protocol_defaults.get(PROTOCOL_A2A_T, {})
        batch = _Batch(
            batch_id=f"batch-{uuid.uuid4().hex[:12]}",
            case_ids=list(case_ids),
            protocols=selected_protocols,
            stop_after=stop_after,
            approval=resolved_approval,
            timeout_seconds=_positive_or_none(timeout_seconds),
            working_timeout_seconds=_positive_or_none(working_timeout_seconds),
            decision_mode=resolved_decision_mode,
            keep_tasks=keep_tasks,
            transport=defaults.get("transport", "sse"),
            discovery=defaults.get("discovery", "direct"),
            items=items,
            owner_id=owner,
        )
        self._batches[batch.batch_id] = batch
        self._persist(batch)
        return self._snapshot(batch)

    def create_scratch(
        self,
        *,
        user_input: str,
        protocol: str,
        approval: str = "approve",
        timeout_seconds: float | None = 300.0,
        decision_mode: str | None = None,
        keep_tasks: bool = False,
        owner: str = "",
    ) -> dict[str, object]:
        """Create a one-item batch for an ad-hoc request (full flow only)."""
        text = user_input.strip()
        if not text:
            raise ValueError("临时请求不能为空。")
        if protocol not in {PROTOCOL_A2A_T, PROTOCOL_A2A}:
            raise ValueError(f"unknown protocol {protocol!r}")
        resolved_approval = _validate_approval(approval) or "approve"
        resolved_decision_mode = self._validate_decision_mode(
            decision_mode or default_decision_mode(self._model_mode)
        )
        defaults = self._protocol_defaults.get(_runtime_protocol(protocol), {})
        item = CaseItem(case_id=0, name="临时请求", protocol=protocol, scratch=text)
        batch = _Batch(
            batch_id=f"batch-{uuid.uuid4().hex[:12]}",
            case_ids=[0],
            protocols=[protocol],
            stop_after="final",
            approval=resolved_approval,
            timeout_seconds=_positive_or_none(timeout_seconds) or 300.0,
            decision_mode=resolved_decision_mode,
            keep_tasks=keep_tasks,
            transport=defaults.get("transport", "sse"),
            discovery=defaults.get("discovery", "direct"),
            items=[item],
            owner_id=owner,
        )
        self._batches[batch.batch_id] = batch
        self._persist(batch)
        return self._snapshot(batch)

    def _validate_decision_mode(self, decision_mode: str) -> str:
        """Validate one batch decision mode; a live batch requires an online model."""
        if decision_mode not in DECISION_MODES:
            raise ValueError(f"unknown decision_mode {decision_mode!r}")
        if decision_mode == DECISION_MODE_LIVE and self._model_mode == "offline":
            raise ValueError("live 决策需要在线模型。")
        return decision_mode

    def get(self, batch_id: str, *, owner: str = "") -> dict[str, object] | None:
        """Return one batch snapshot, or ``None`` when unknown or owned by someone else."""
        batch = self._owned(batch_id, owner)
        return None if batch is None else self._snapshot(batch)

    async def start(self, batch_id: str, *, owner: str = "") -> dict[str, object]:
        """Start one draft batch."""
        batch = self._owned(batch_id, owner)
        if batch is None:
            raise KeyError(batch_id)
        if batch.status != BATCH_DRAFT:
            raise ValueError("该批次已经执行过。")
        batch.status = BATCH_RUNNING
        batch.started_at = _now_iso()
        self._persist(batch)
        batch.task = asyncio.create_task(self._run_batch(batch), name=f"tester-{batch_id}")
        await asyncio.sleep(0)
        return self._snapshot(batch)

    async def cancel(self, batch_id: str, *, owner: str | None = None) -> bool:
        """Cancel one running batch and its active run.

        ``owner=None`` is the internal shutdown path; HTTP callers pass their browser identity.
        """
        batch = self._owned(batch_id, owner)
        if batch is None or batch.status != BATCH_RUNNING:
            return False
        batch.cancel_requested = True
        if batch.current_index is not None:
            item = batch.items[batch.current_index]
            if item.run_id:
                await self._runs.cancel(item.run_id)
            # A Modify item's baseline Task is not ``item.run_id``; release it too, otherwise a
            # cancelled batch leaves the Create Task WORKING.
            baseline = _baseline_run_id(item)
            if baseline is not None and baseline != item.run_id:
                await self._runs.cancel(baseline)
        if batch.task is not None and not batch.task.done():
            batch.task.cancel()
        self._persist(batch)
        return True

    async def shutdown(self) -> None:
        """Cancel every running batch and release the Tasks its items still own."""
        for batch_id, batch in list(self._batches.items()):
            if batch.status == BATCH_RUNNING:
                await self.cancel(batch_id)
        for batch in list(self._batches.values()):
            if batch.keep_tasks:
                continue
            for item in batch.items:
                await self._release_item_runs(item)

    def _owned(self, batch_id: str, owner: str | None) -> _Batch | None:
        """Return one batch, or ``None`` when it belongs to a different browser.

        ``owner=None`` bypasses the check for in-process callers such as ``shutdown``.
        """
        batch = self._batches.get(batch_id) or self._load(batch_id)
        if batch is None:
            return None
        if owner is not None and batch.owner_id != owner:
            return None
        return batch

    async def retry(self, batch_id: str, *, owner: str = "") -> dict[str, object]:
        """Create a new draft batch from the failed, errored or cancelled items.

        The previous attempt may still hold a remote Task (a failed item keeps tracking it);
        retrying releases those Tasks first so both attempts never occupy quota at once.
        """
        batch = self._owned(batch_id, owner)
        if batch is None:
            raise KeyError(batch_id)
        selected = [
            item
            for item in batch.items
            if item.status in {CASE_FAILED, CASE_ERROR, CASE_CANCELLED}
        ]
        if not selected:
            raise ValueError("没有失败、错误或取消的用例可以重跑。")
        for item in selected:
            await self._release_item_runs(item)
        retried = _Batch(
            batch_id=f"batch-{uuid.uuid4().hex[:12]}",
            case_ids=batch.case_ids,
            protocols=batch.protocols,
            stop_after=batch.stop_after,
            approval=batch.approval,
            timeout_seconds=batch.timeout_seconds,
            working_timeout_seconds=batch.working_timeout_seconds,
            decision_mode=batch.decision_mode,
            keep_tasks=batch.keep_tasks,
            transport=batch.transport,
            discovery=batch.discovery,
            items=[
                CaseItem(
                    case_id=item.case_id,
                    name=item.name,
                    protocol=item.protocol,
                    scratch=item.scratch,
                )
                for item in selected
            ],
            owner_id=batch.owner_id,
        )
        self._batches[retried.batch_id] = retried
        self._persist(retried)
        return self._snapshot(retried)

    def list_batches(self, *, owner: str = "", limit: int = 20) -> list[dict[str, object]]:
        """Return the most recent batches of one browser identity, newest first."""
        if self._history_dir is None or not self._history_dir.is_dir():
            return [
                self._snapshot(batch)
                for batch in sorted(
                    self._batches.values(), key=lambda item: item.created_at, reverse=True
                )
                if batch.owner_id == owner
            ][:limit]
        summaries: list[dict[str, object]] = []
        for path in sorted(
            self._history_dir.glob("*.json"),
            key=lambda candidate: candidate.stat().st_mtime,
            reverse=True,
        ):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(document, Mapping) or document.get("schema") not in _SCHEMAS:
                continue
            if str(document.get("ownerId") or "") != owner:
                continue
            summaries.append(
                {
                    "batchId": document.get("batchId"),
                    "status": document.get("status"),
                    "createdAt": document.get("createdAt"),
                    "startedAt": document.get("startedAt"),
                    "finishedAt": document.get("finishedAt"),
                    "summary": document.get("summary", {}),
                    "stopAfter": document.get("stopAfter"),
                    "decisionMode": document.get("decisionMode"),
                    "keepTasks": bool(document.get("keepTasks")),
                    "transport": document.get("transport"),
                    "discovery": document.get("discovery"),
                }
            )
            if len(summaries) >= limit:
                break
        return summaries

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def rerun_item(
        self, batch_id: str, index: int, *, owner: str = ""
    ) -> dict[str, object]:
        """Rerun one item in place with a fresh run identity.

        Only a batch that is not running can be rerun. The previous attempt is kept in
        ``item.attempts`` so the drawer can still open its wire log.
        """
        batch = self._owned(batch_id, owner)
        if batch is None:
            raise KeyError(batch_id)
        if batch.status == BATCH_RUNNING:
            raise ValueError("该批次仍在执行中。")
        if index < 0 or index >= len(batch.items):
            raise ValueError("用例下标无效。")
        item = batch.items[index]
        # The previous attempt may still hold a remote Task; release it before the new identity
        # starts, so a rerun never doubles the quota the batch consumes.
        await self._release_item_runs(item)
        item.attempts.append(
            {
                "runId": item.run_id,
                "decisionMode": batch.decision_mode,
                "status": item.status,
                "finishedAt": _now_iso(),
            }
        )
        item.status = CASE_PENDING
        item.stages = {}
        item.run_id = None
        item.duration_ms = None
        item.tokens = {}
        item.error = None
        item.baseline = None
        # The arbitration evidence belongs to the attempt that produced it; a rerun must not
        # export the previous run's intent/trace/answer when the new attempt fails early.
        item.trace_id = ""
        item.intent = None
        item.server_intent = None
        item.intent_meta = {}
        item.refs = {}
        item.answer = None
        item.interception = None
        batch.status = BATCH_RUNNING
        batch.cancel_requested = False
        batch.finished_at = None
        batch.error = None
        self._persist(batch)
        batch.task = asyncio.create_task(
            self._run_batch(batch, [index]), name=f"tester-{batch_id}-rerun-{index}"
        )
        await asyncio.sleep(0)
        return self._snapshot(batch)

    async def _run_batch(
        self, batch: _Batch, indices: Sequence[int] | None = None
    ) -> None:
        selected = list(range(len(batch.items))) if indices is None else list(indices)
        try:
            for index in selected:
                if batch.cancel_requested:
                    break
                batch.current_index = index
                await self._run_item(batch, index, batch.items[index])
        except asyncio.CancelledError:
            self._mark_pending_cancelled(batch)
            batch.status = BATCH_CANCELLED
            batch.finished_at = _now_iso()
            raise
        else:
            if batch.cancel_requested:
                self._mark_pending_cancelled(batch)
                batch.status = BATCH_CANCELLED
            else:
                batch.status = BATCH_FINISHED
            batch.finished_at = _now_iso()
        finally:
            batch.current_index = None
            self._persist(batch)

    def _mark_pending_cancelled(self, batch: _Batch) -> None:
        for item in batch.items:
            if item.status in {CASE_PENDING, CASE_RUNNING}:
                item.status = CASE_CANCELLED
                item.error = "批次已取消。"

    async def _run_item(self, batch: _Batch, index: int, item: CaseItem) -> None:
        item.status = CASE_RUNNING
        started = time.monotonic()
        try:
            if item.scratch is not None:
                await self._run_scratch(batch, index, item)
            else:
                await self._run_case_item(batch, index, item)
        except asyncio.CancelledError:
            item.status = CASE_CANCELLED
            item.error = "批次已取消。"
            raise
        except Exception as exc:  # noqa: BLE001 - one bad item must not stop the batch
            _LOGGER.warning("tester item %s/%s failed", item.case_id, item.protocol, exc_info=True)
            item.status = CASE_ERROR
            item.error = f"{type(exc).__name__}: {exc}"
        finally:
            item.duration_ms = int(round((time.monotonic() - started) * 1000))
            if item.status == CASE_RUNNING and not batch.cancel_requested:
                # A scratch item never calls ``_run_case_item``'s status mapping; derive the
                # status from its stages so a failed or timed-out scratch is not counted as a pass.
                item.status = self._item_status(item)
            self._persist(batch)

    async def _run_case_item(self, batch: _Batch, index: int, item: CaseItem) -> None:
        case = self._catalog.get(item.case_id)
        if case is None:
            item.status = CASE_ERROR
            item.error = f"未知用例 {item.case_id}"
            return
        stages = list(ALL_STAGES[: ALL_STAGES.index(batch.stop_after) + 1])
        effective_case = case
        if item.protocol == PROTOCOL_A2A:
            # OSP defines no SDK stages for pure A2A; only the runtime chain applies.
            stages = [stage for stage in stages if stage not in SDK_STAGE_ORDER]
        else:
            sdk_stages = [stage for stage in stages if stage in SDK_STAGE_ORDER]
            if sdk_stages:
                blocked_followup = is_blocked_case(case) and has_followup(case)
                if blocked_followup:
                    effective_case = followup_case(case)
                # The SDK runner owns the two-segment split of a blocked case: it returns the
                # follow-up request's stages as the outcome and keeps the intercepted original as
                # ``outcome.interception``. The full flow below dispatches the replacement request.
                await self._run_sdk_stages(
                    case,
                    item,
                    sdk_stages[-1],
                    batch.decision_mode,
                    outcome_case=effective_case if blocked_followup else None,
                )
        self._collect_reference(item, effective_case)
        if batch.stop_after in SDK_STAGE_ORDER:
            item.status = self._item_status(item)
            return
        await self._run_full_flow(batch, index, item, effective_case, stages)
        item.status = self._item_status(item)

    async def _run_sdk_stages(
        self,
        case: Mapping[str, Any],
        item: CaseItem,
        stop_after: str,
        decision_mode: str = DECISION_MODE_SCRIPTED,
        *,
        outcome_case: Mapping[str, Any] | None = None,
    ) -> None:
        """Run the SDK stages of one case and record its interception segment, when split.

        ``outcome_case`` is the derived replacement case whose expectations judge the returned
        stages; the runner already judged the intercepted original, so those stages are recorded
        verbatim (the runner derived their assertion flags).
        """
        results, interception, error = await self._sdk_stage_results(
            case, stop_after, decision_mode, outcome_case=outcome_case
        )
        item.stages.update(results)
        if interception is not None:
            record: dict[str, object] = {
                "input": str(case.get("user_input") or ""),
                "followupInput": followup_input(case) or "",
                "stages": {stage: result.to_dict() for stage, result in interception.items()},
            }
            if error:
                record["error"] = error
            item.interception = record
        if error:
            item.error = error

    async def _sdk_stage_results(
        self,
        case: Mapping[str, Any],
        stop_after: str,
        decision_mode: str,
        *,
        outcome_case: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, StageResult], dict[str, StageResult] | None, str | None]:
        """Run the SDK-owned stages of one case and return stages, interception and error.

        A blocked case with a replacement request makes the runner execute two segments; the
        follow-up segment is the returned outcome and the original is ``outcome.interception``.
        """
        judge = outcome_case if outcome_case is not None else case
        model_mode = MODEL_MODE_LIVE if decision_mode == DECISION_MODE_LIVE else None
        async with self._sdk_lock:
            client = await self._ensure_prompt_client(model_mode)
            outcome = await asyncio.to_thread(run_case, case, client, stop_after=stop_after)
        negotiation_expected = _negotiation_expected(judge, PROTOCOL_A2A_T)
        results: dict[str, StageResult] = {}
        for stage in outcome.stages:
            result = StageResult(
                stage=stage.stage,
                status=stage.status,
                message=stage.message,
                expected=stage.expected,
                actual=stage.actual,
                diffs=[diff.to_dict() for diff in stage.diffs],
            )
            protocol = "" if stage.stage == "intent" else PROTOCOL_A2A_T
            result = _apply_assertion(judge, protocol, stage.stage, result)
            if stage.stage == "task_t" and negotiation_expected:
                # The case's Task-T is the post-negotiation one; the SDK runner only sees the
                # initial request, so comparing them would be a false failure.
                result.status = STAGE_RECORDED
                result.message = _NEGOTIATED_TASK_T_MESSAGE
            results[stage.stage] = result
        interception: dict[str, StageResult] | None = None
        if outcome.interception is not None:
            interception = {
                stage.stage: StageResult(
                    stage=stage.stage,
                    status=stage.status,
                    message=stage.message,
                    expected=stage.expected,
                    actual=stage.actual,
                    diffs=[diff.to_dict() for diff in stage.diffs],
                )
                for stage in outcome.interception
            }
        return results, interception, outcome.error

    async def _run_full_flow(
        self,
        batch: _Batch,
        index: int,
        item: CaseItem,
        case: Mapping[str, Any],
        stages: Sequence[str],
    ) -> None:
        runtime_protocol = _runtime_protocol(item.protocol)
        runner = _case_runner(case)
        scenario = str(runner.get("scenario") or "night")
        language = runner.get("language")
        resolved_language = language if isinstance(language, str) and language else None
        resolved_approval = self._resolved_approval(batch, case)
        resolved_timeout = self._resolved_timeout(batch, case)
        target_task_id: str | None = None
        target_context_id: str | None = None
        target_server_task_id: str | None = None
        target_server_context_id: str | None = None
        # A blocked A2A-T request never reaches the NMS, so it has nothing to continue; without a
        # follow-up, seeding would only create a Task that the case then refuses to modify.
        blocked_without_followup = (
            runtime_protocol == RUNTIME_A2A_T
            and is_blocked_case(case)
            and not has_followup(case)
        )
        if case.get("operation") == "Modify" and not blocked_without_followup:
            # A Modify continues an existing Task. The corpus says nothing about where that Task
            # came from, so the tester pairs every Modify row with one explicit baseline Create
            # (see ``_baseline_request``) and continues the Task it produced. A2A-T continues the
            # vendor Task directly; pure A2A continues the OSS Agent's outer Task, which is the
            # Task id that hop owns.
            baseline = await self._seed_baseline(batch, index, item, runtime_protocol, case)
            if baseline is None:
                for stage in ("send", "negotiate", "accepted", "working", "final"):
                    if stage in stages:
                        item.stages[stage] = StageResult(
                            stage, STAGE_ERROR, "Modify 基线任务创建失败。"
                        )
                item.status = CASE_ERROR
                return
            if runtime_protocol == RUNTIME_A2A_T:
                target_task_id = str(baseline["taskId"])
                target_context_id = str(baseline["contextId"])
            else:
                target_server_task_id = str(baseline["taskId"])
                target_server_context_id = str(baseline["contextId"])

        decisions = a2at_decisions(case) if runtime_protocol == RUNTIME_A2A_T else []
        answers = pure_answers(case) if runtime_protocol == PROTOCOL_PURE else []
        defaults = self._protocol_defaults.get(runtime_protocol, {})
        transport = defaults.get("transport", "sse")
        discovery = defaults.get("discovery", "direct")
        conversation_id, idempotency_key = self._run_identity(batch, index, item)
        mode = self._mode_for(batch, item.protocol)
        if runtime_protocol == RUNTIME_A2A_T and _negotiation_expected(case, PROTOCOL_A2A_T):
            # The OSP Q column is the expected negotiation dialogue. Replaying it verifies the
            # protocol path deterministically; the SDK intent/Task-T stages still run in live mode.
            mode = MODE_SCRIPTED
        run = await self._runs.start(
            scenario,
            mode,
            transport,
            discovery,
            str(case["user_input"]),
            protocol=runtime_protocol,
            conversation_id=conversation_id,
            user_id="tester",
            idempotency_key=idempotency_key,
            source={
                "kind": "tester",
                "channel": "tester-console",
                "batchId": batch.batch_id,
                "caseId": item.case_id,
                "caseName": item.name,
                # A rate-less site-level negotiation is expected to reach the server, which
                # proposes the missing value; only the corpus' negotiation cases get this pass.
                "negotiationExpected": _negotiation_expected(case, PROTOCOL_A2A_T),
            },
            target_task_id=target_task_id,
            target_context_id=target_context_id,
            target_server_task_id=target_server_task_id,
            target_server_context_id=target_server_context_id,
            scripted_decisions=decisions or None,
            pure_answers=answers or None,
            validate_before_send=_pre_send_validation(runner),
            language=resolved_language,
        )
        item.run_id = run.run_id
        self._persist(batch)
        approval_task = asyncio.create_task(
            self._answer_approval_gate(resolved_approval, item.protocol, run),
            name=f"tester-approval-{run.run_id}",
        )
        try:
            await self._wait_for_stage(
                run,
                item,
                batch,
                stages,
                case=case,
                timeout_seconds=resolved_timeout,
                working_timeout_seconds=self._resolved_working_timeout(batch),
            )
        finally:
            approval_task.cancel()
            await asyncio.gather(approval_task, return_exceptions=True)
            await self._collect_metrics(item, run, case)
            # The evidence is collected; release the remote Task unless this batch keeps Tasks for
            # long-task verification. ``accepted``/``working`` end the observation window, and the
            # tester — unlike the presentation page — never reattaches to that run, so leaving the
            # Task running through a 51-case acceptance batch would exhaust the NMS quota.
            if not batch.keep_tasks:
                await self._release_item_runs(item, run)

    async def _run_scratch(self, batch: _Batch, index: int, item: CaseItem) -> None:
        """Run an ad-hoc request through the full flow; no expectations are asserted."""
        if item.scratch is None:
            return
        runtime_protocol = _runtime_protocol(item.protocol)
        defaults = self._protocol_defaults.get(runtime_protocol, {})
        conversation_id, idempotency_key = self._run_identity(batch, index, item)
        run = await self._runs.start(
            "night",
            self._mode_for(batch, item.protocol),
            defaults.get("transport", "sse"),
            defaults.get("discovery", "direct"),
            item.scratch,
            protocol=runtime_protocol,
            conversation_id=conversation_id,
            user_id="tester",
            idempotency_key=idempotency_key,
            source={"kind": "tester", "channel": "tester-scratch", "batchId": batch.batch_id},
            scripted_decisions=(),
            validate_before_send=True,
        )
        item.run_id = run.run_id
        self._persist(batch)
        timed_out = await self._wait_for_run(run, self._resolved_timeout(batch))
        actual = run.result.final_state if run.result is not None else None
        if run.status == STATUS_FAILED:
            item.stages["final"] = StageResult(
                "final", STAGE_ERROR, run.error or "运行失败。", expected=None, actual=actual
            )
            item.error = run.error or "运行失败。"
        elif timed_out:
            item.stages["final"] = StageResult(
                "final", STAGE_ERROR, "临时请求超时。", expected=None, actual=actual
            )
            item.error = "临时请求超时。"
        else:
            item.stages["final"] = StageResult(
                "final", STAGE_RECORDED, "临时请求只记录结果。", expected=None, actual=actual
            )
        await self._collect_metrics(item, run)
        if not batch.keep_tasks:
            await self._release_item_runs(item, run)

    async def _release_item_runs(self, item: CaseItem, run: RunState | None = None) -> None:
        """Cancel the runs one item created and release their remote Tasks.

        ``run`` is the item's own run when the caller still holds it; it is then awaited so the
        cancellation unwinds before the batch moves on to the next item. A Modify item's baseline
        run is released too: it is normally already stopped by the Task handover, in which case
        ``RunManager.cancel`` is a no-op. Unknown or finished runs are ignored, so the shutdown
        sweep can call this for every item.
        """
        if run is not None and run.status == "running":
            await self._runs.cancel(run.run_id)
            await self._wait_for_run(run, _CANCEL_GRACE_SECONDS)
        elif run is None and item.run_id:
            await self._runs.cancel(item.run_id)
        baseline = _baseline_run_id(item)
        if baseline is not None and baseline != item.run_id:
            await self._runs.cancel(baseline)

    def _run_identity(self, batch: _Batch, index: int, item: CaseItem) -> tuple[str, str]:
        """Return the conversation id and idempotency key of this attempt.

        A rerun must not reuse the previous run: ``RunManager`` deduplicates by idempotency key, and
        a finished conversation would return the old run instead of executing again.
        """
        attempt = len(item.attempts)
        suffix = f"-r{attempt}" if attempt else ""
        base = f"tester-{batch.batch_id}-{index}{suffix}"
        return base, base

    @staticmethod
    def _mode_for(batch: _Batch, protocol: str) -> str:
        """Return the runtime decision mode of one item."""
        if protocol == PROTOCOL_A2A_T and batch.decision_mode != DECISION_MODE_LIVE:
            return MODE_SCRIPTED
        return MODE_AUTO

    @staticmethod
    def _resolved_approval(batch: _Batch, case: Mapping[str, Any] | None = None) -> str:
        """Return the approval behaviour of one item: batch override, else case runner."""
        if batch.approval is not None:
            return batch.approval
        return str(_case_runner(case).get("approval") or "approve")

    @staticmethod
    def _resolved_timeout(batch: _Batch, case: Mapping[str, Any] | None = None) -> float:
        """Return the run timeout of one item: batch override, else case runner, else 300s."""
        if batch.timeout_seconds is not None:
            return batch.timeout_seconds
        value = _case_runner(case).get("timeout_seconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(1.0, float(value))
        return 300.0

    @staticmethod
    def _resolved_working_timeout(batch: _Batch) -> float:
        """Return the ``working`` observation window of one item."""
        if batch.working_timeout_seconds is not None:
            return batch.working_timeout_seconds
        return DEFAULT_WORKING_TIMEOUT_SECONDS

    async def _seed_baseline(
        self,
        batch: _Batch,
        index: int,
        item: CaseItem,
        protocol: str,
        case: Mapping[str, Any],
    ) -> dict[str, object] | None:
        """Create the Task a Modify item continues, and wait until it can be continued.

        The case's ``baseline.request`` is the paired Create (a normal-day task for the same region
        and sites); the fallback fixture is used only when the case declares none. The Modify never
        targets a Task the tester did not just create.
        """
        defaults = self._protocol_defaults.get(protocol, {})
        conversation_id, idempotency_key = self._run_identity(batch, index, item)
        request = _baseline_request(case)
        # The baseline follows the batch's decision mode. A live batch must not seed the Create
        # with ``MODE_SCRIPTED``: its script is empty, so a server question (INPUT_REQUIRED) would
        # raise "The scripted decision sequence is exhausted" and abort the Modify before it runs.
        mode = self._mode_for(batch, protocol)
        run = await self._runs.start(
            BASELINE_SCENARIO,
            mode,
            defaults.get("transport", "sse"),
            defaults.get("discovery", "direct"),
            request,
            protocol=protocol,
            conversation_id=f"{conversation_id}-baseline",
            user_id="tester",
            idempotency_key=f"{idempotency_key}-baseline",
            source={
                "kind": "tester",
                "channel": f"tester-baseline-{protocol}",
                "batchId": batch.batch_id,
                "caseId": item.case_id,
            },
            scripted_decisions=(),
            # Only the A2A-T flow has a Task-T pre-send gate; the pure hop sends raw text.
            validate_before_send=protocol == RUNTIME_A2A_T,
        )
        # Publish the baseline as soon as it starts: the tester UI polls the batch snapshot, and
        # without this the Modify item showed no run and no progress until the Create finished.
        item.baseline = {
            "runId": run.run_id,
            "protocol": protocol,
            "status": "running",
            "state": run.latest_task_state,
            "request": request,
        }
        # A Modify needs the Task accepted and still continuable. It must not wait for the
        # baseline run to reach a terminal state: a real NMS keeps the Create Task WORKING for the
        # whole energy-saving window, which made every Modify item time out as a baseline failure.
        state = await self._wait_for_baseline_acceptance(
            run, protocol, self._resolved_timeout(batch), item=item
        )
        task_id = run.remote_task_id or (run.result.task_id if run.result is not None else None)
        context_id = run.remote_context_id or (
            run.result.context_id if run.result is not None else None
        )
        accepted = state in _baseline_accepted_states(protocol)
        if not task_id or not context_id or not accepted:
            message = run.error or _baseline_failure_message(protocol, state)
            item.baseline = {
                "runId": run.run_id,
                "protocol": protocol,
                "status": "failed",
                "state": state,
                "finalState": run.result.final_state if run.result is not None else None,
                "request": request,
                "error": message,
            }
            item.error = message
            return None
        baseline = {
            "runId": run.run_id,
            "protocol": protocol,
            "status": "accepted",
            "taskId": task_id,
            "contextId": context_id,
            "state": state,
            "request": request,
            "finalState": run.result.final_state if run.result is not None else None,
        }
        item.baseline = baseline
        return baseline

    async def _wait_for_baseline_acceptance(
        self,
        run: RunState,
        protocol: str,
        timeout_seconds: float,
        *,
        item: CaseItem | None = None,
    ) -> str | None:
        """Poll until the baseline Task reaches the state a Modify can continue from.

        A2A-T can continue any non-terminal Task and reports ``SUBMITTED`` on acceptance. Pure
        A2A continues the OSS Agent's outer Task, which must be ``WORKING``: ``COMPLETED`` is
        terminal (the A2A layer rejects the continuation) and ``INPUT_REQUIRED`` would route the
        Modify text as an answer to the pending question.

        ``item`` is updated in place on every poll so the batch snapshot (and therefore the tester
        UI) shows the baseline's current state while the Create is still in flight.
        """
        wanted = _baseline_accepted_states(protocol)
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            state = run.latest_task_state
            if item is not None and isinstance(item.baseline, dict):
                item.baseline["state"] = state
                if run.remote_task_id:
                    item.baseline["taskId"] = run.remote_task_id
                if run.remote_context_id:
                    item.baseline["contextId"] = run.remote_context_id
            if state in _FAILED_TASK_STATES:
                return state
            if run.remote_task_id and run.remote_context_id and state in wanted:
                return state
            if run.finished():
                return state
            await asyncio.sleep(_POLL_SECONDS)
        return run.latest_task_state

    async def _wait_for_stage(
        self,
        run: RunState,
        item: CaseItem,
        batch: _Batch,
        stages: Sequence[str],
        *,
        case: Mapping[str, Any] | None,
        timeout_seconds: float,
        working_timeout_seconds: float,
    ) -> None:
        """Wait for the last requested runtime stage and record its assertion."""
        expected_send = True
        expected_rounds = 0
        expected_kinds: list[str] | None = None
        expected_final: object = None
        if case is not None:
            protocol_block = case["expect"]["protocols"][item.protocol]
            expected_final = protocol_block.get("final_state")
            if item.protocol == PROTOCOL_A2A_T:
                expected_send = bool(protocol_block.get("send"))
            negotiation = protocol_block["negotiation"]
            expected_rounds = int(negotiation.get("expected_rounds", 0))
            expected_kinds = negotiation.get("kinds")

        # ``accepted``/``working`` only make sense when the case expects a send. Disabling the
        # ``send`` assertion does not mean no Task is created, so it must not skip them.
        send_expected = expected_send

        started = time.monotonic()
        if "send" in stages:
            sent = await self._wait_for_span(run, "message_sent", timeout_seconds)
            status = STAGE_PASSED if sent == expected_send else STAGE_FAILED
            send_result = StageResult(
                "send",
                status,
                "" if status == STAGE_PASSED else "预期发送状态与观察结果不一致。",
                expected=expected_send,
                actual=sent,
                duration_ms=int(round((time.monotonic() - started) * 1000)),
            )
            if case is not None:
                send_result = _apply_assertion(case, item.protocol, "send", send_result)
            item.stages["send"] = send_result
            if batch.stop_after == "send":
                return

        started = time.monotonic()
        if "negotiate" in stages:
            sim_pure = item.protocol == PROTOCOL_A2A and self._nms_sim
            await self._wait_for_negotiation(
                run,
                0 if sim_pure else expected_rounds,
                min(timeout_seconds, _SIM_PURE_SETTLE_SECONDS) if sim_pure else timeout_seconds,
            )
            actual_rounds = len(run.proposals)
            actual_kinds = [proposal.kind for proposal in run.proposals]
            rounds_ok = actual_rounds == expected_rounds
            kinds_ok = expected_kinds is None or all(
                kind in actual_kinds for kind in expected_kinds
            )
            status = STAGE_PASSED if rounds_ok and kinds_ok else STAGE_FAILED
            negotiate_result = StageResult(
                "negotiate",
                status,
                "" if status == STAGE_PASSED else "协商轮次或类型与预期不一致。",
                expected={"rounds": expected_rounds, "kinds": expected_kinds},
                actual={"rounds": actual_rounds, "kinds": actual_kinds},
                duration_ms=int(round((time.monotonic() - started) * 1000)),
            )
            if case is not None:
                negotiate_result = _apply_assertion(
                    case, item.protocol, "negotiation", negotiate_result
                )
            if item.protocol == PROTOCOL_A2A and self._nms_sim:
                # The simulator asks a generic plain-A2A question; the vendor's dialogue and
                # round count come from the OSP P column and cannot be reproduced offline.
                negotiate_result.status = STAGE_RECORDED
                negotiate_result.message = _SIM_PURE_NEGOTIATION_MESSAGE
            item.stages["negotiate"] = negotiate_result
            if batch.stop_after == "negotiate":
                return

        if "accepted" in stages:
            item.stages["accepted"] = await self._judge_accepted(
                run, item, case, send_expected, timeout_seconds
            )
            if batch.stop_after == "accepted":
                return

        if "working" in stages:
            item.stages["working"] = await self._judge_working(
                run, item, case, send_expected, working_timeout_seconds
            )
            if batch.stop_after == "working":
                return

        started = time.monotonic()
        if "final" in stages:
            await self._wait_for_run(run, timeout_seconds)
            actual_final = run.result.final_state if run.result is not None else None
            if run.status == STATUS_FAILED:
                status = STAGE_ERROR
                message = run.error or "运行失败。"
                item.error = message
            elif expected_final is None:
                status = STAGE_RECORDED
                message = ""
            else:
                status = STAGE_PASSED if actual_final == expected_final else STAGE_FAILED
                message = "" if status == STAGE_PASSED else "最终状态与预期不一致。"
            final_result = StageResult(
                "final",
                status,
                message,
                expected=expected_final,
                actual=actual_final,
                duration_ms=int(round((time.monotonic() - started) * 1000)),
            )
            if case is not None:
                final_result = _apply_assertion(case, item.protocol, "final_state", final_result)
            item.stages["final"] = final_result

    async def _judge_accepted(
        self,
        run: RunState,
        item: CaseItem,
        case: Mapping[str, Any] | None,
        send_expected: bool,
        timeout_seconds: float,
    ) -> StageResult:
        """Judge whether the remote side accepted the request (the success status arrived).

        ``TASK_STATE_SUBMITTED`` is the A2A-T success signal for Create and Modify alike; a pure
        A2A outer Task starts at ``TASK_STATE_WORKING``. An ``INPUT_REQUIRED`` Task carries an
        identity but the negotiation is not finished, so it must not turn the row green.
        """
        started = time.monotonic()
        actual = _task_observation(run)
        if not send_expected:
            return StageResult(
                "accepted",
                STAGE_SKIPPED,
                "用例期望不发送",
                expected=None,
                actual=actual,
                duration_ms=int(round((time.monotonic() - started) * 1000)),
            )

        await self._wait_for_acceptance_state(run, timeout_seconds)
        actual = _task_observation(run)
        state = run.latest_task_state
        final_state = run.result.final_state if run.result is not None else None
        if final_state in _PRE_SEND_TERMINAL_STATES:
            status = STAGE_FAILED
            message = f"run 在发送前结束：{final_state}"
        elif run.remote_task_id is None or run.remote_context_id is None:
            status = STAGE_FAILED
            message = "run 已结束但没有 Task。" if run.finished() else "等待 Task 超时。"
        elif state in _FAILED_TASK_STATES:
            status = STAGE_FAILED
            message = f"Task 状态为 {state}。"
        elif state in _ACCEPTED_TASK_STATES:
            status = STAGE_PASSED
            message = ""
        elif state == "TASK_STATE_INPUT_REQUIRED":
            status = STAGE_WAITING
            message = "Task 仍在协商（INPUT_REQUIRED），尚未确认受理。"
        else:
            status = STAGE_WAITING
            message = "未观察到受理状态。"
        return StageResult(
            "accepted",
            status,
            message,
            expected={"taskId": "非空", "contextId": "非空", "state": "已受理"},
            actual=actual,
            duration_ms=int(round((time.monotonic() - started) * 1000)),
        )

    async def _judge_working(
        self,
        run: RunState,
        item: CaseItem,
        case: Mapping[str, Any] | None,
        send_expected: bool,
        working_timeout_seconds: float,
    ) -> StageResult:
        """Judge whether the Task was observed moving past ``SUBMITTED``."""
        started = time.monotonic()
        actual = _task_observation(run)
        # Pure A2A has no workbook final-state oracle, but "the outer Task was accepted and is
        # running" is a protocol fact, not a corpus expectation. Recording the stage made every
        # pure item green by construction; judge the outer Task state instead.
        if not send_expected:
            return StageResult(
                "working",
                STAGE_SKIPPED,
                "用例期望不发送",
                expected=None,
                actual=actual,
                duration_ms=int(round((time.monotonic() - started) * 1000)),
            )

        state = await self._wait_for_working_state(run, working_timeout_seconds)
        actual = _task_observation(run)
        final_state = run.result.final_state if run.result is not None else None
        refused_completion = (
            state == "TASK_STATE_COMPLETED"
            and final_state in {FINAL_STATE_TASK_FAILED, FINAL_STATE_TASK_REJECTED}
        )
        if state in _WORKING_TASK_STATES and not refused_completion:
            status = STAGE_PASSED
            message = ""
        elif refused_completion:
            # The vendor reports a refusal as TASK_STATE_COMPLETED; the run layer already
            # downgraded it to task_failed. A green card here would be a false acceptance.
            status = STAGE_FAILED
            message = f"Task 以 TASK_STATE_COMPLETED 结束，但 run 终态为 {final_state}。"
        elif state in _FAILED_TASK_STATES:
            status = STAGE_FAILED
            message = f"Task 状态为 {state}。"
        elif state == TASK_STATE_SUBMITTED:
            status = STAGE_WAITING
            message = f"{working_timeout_seconds:g}s 内 Task 仍停留在 SUBMITTED。"
        elif state == "TASK_STATE_INPUT_REQUIRED":
            status = STAGE_WAITING
            message = "Task 仍在协商（INPUT_REQUIRED）。"
        else:
            final_state = run.result.final_state if run.result is not None else None
            if final_state == "task_completed":
                status = STAGE_PASSED
                message = "通过终态 task_completed 观察到完成。"
            elif final_state in {"task_failed", "task_rejected"}:
                status = STAGE_FAILED
                message = f"run 终态为 {final_state}。"
            else:
                status = STAGE_WAITING
                message = (
                    f"{working_timeout_seconds:g}s 内未观察到 WORKING。"
                    if run.finished()
                    else "未观察到 WORKING。"
                )
        return StageResult(
            "working",
            status,
            message,
            expected="WORKING 或 COMPLETED",
            actual=actual,
            duration_ms=int(round((time.monotonic() - started) * 1000)),
        )

    async def _wait_for_acceptance_state(
        self, run: RunState, timeout_seconds: float
    ) -> str | None:
        """Poll until the Task identity and an accepted or failed state are observed.

        A Task only proves the downstream accepted the request once its status state arrives;
        ``INPUT_REQUIRED`` means the negotiation is still in progress.
        """
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            state = run.latest_task_state
            if state in _FAILED_TASK_STATES:
                return state
            if (
                run.remote_task_id
                and run.remote_context_id
                and state in _ACCEPTED_TASK_STATES
            ):
                return state
            if run.finished():
                return state
            await asyncio.sleep(_POLL_SECONDS)
        return run.latest_task_state

    async def _wait_for_working_state(
        self, run: RunState, timeout_seconds: float
    ) -> str | None:
        """Poll the observed Task state until it leaves SUBMITTED, the run ends or time is up."""
        deadline = time.monotonic() + max(1.0, timeout_seconds)
        while time.monotonic() < deadline:
            state = run.latest_task_state
            if state in _WORKING_TASK_STATES or state in _FAILED_TASK_STATES:
                return state
            if run.finished():
                # A task-carried negotiation can still be INPUT_REQUIRED right after the accept;
                # keep polling rather than reporting a premature "waiting".
                return state
            await asyncio.sleep(_POLL_SECONDS)
        return run.latest_task_state

    async def _wait_for_run(self, run: RunState, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while not run.finished():
            if time.monotonic() >= deadline:
                return True
            await asyncio.sleep(_POLL_SECONDS)
        return False

    async def _wait_for_span(self, run: RunState, kind: str, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            if any(span.get("kind") == kind for span in run.traces):
                return True
            if run.finished():
                return any(span.get("kind") == kind for span in run.traces)
            await asyncio.sleep(_POLL_SECONDS)
        return any(span.get("kind") == kind for span in run.traces)

    async def _wait_for_negotiation(
        self, run: RunState, expected_rounds: int, timeout_seconds: float
    ) -> None:
        """Wait for the expected rounds, or for the first response when none is expected.

        A case that expects no negotiation still has to observe the first response: a proposal
        there is a failure and the Task identity there ends the item. Waiting for the run to
        finish instead is wrong for a long task — the run stays alive while the remote Task
        executes, so the item would sit for the whole timeout.
        """
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            if expected_rounds > 0:
                if len(run.proposals) >= expected_rounds:
                    return
            elif run.proposals or run.remote_task_id or run.remote_context_id:
                return
            if run.finished():
                return
            await asyncio.sleep(_POLL_SECONDS)

    async def _collect_metrics(
        self,
        item: CaseItem,
        run: RunState,
        case: Mapping[str, Any] | None = None,
    ) -> None:
        if self._metrics_grace_seconds:
            await asyncio.sleep(self._metrics_grace_seconds)
        # The export evidence does not depend on the metrics pipeline; collect it even when the
        # metrics builder fails, because a missing answer is worse than missing token counts.
        self._collect_run_evidence(item, run, case)
        try:
            metrics = build_run_metrics(
                run_id=run.run_id,
                conversation_id=run.conversation_id,
                protocol=run.protocol,
                scenario=run.scenario,
                mode=run.mode,
                transport=run.transport,
                language=self._language,
                model_mode=self._model_mode,
                model_label=self._model_label or None,
                status=run.status,
                transcript=run.transcript,
                result=run.result,
                spans=run.traces,
                expected=None,
                error=run.error,
            )
        except Exception:  # noqa: BLE001 - metrics must never fail the item
            _LOGGER.warning("tester metrics failed for run %s", run.run_id, exc_info=True)
            return
        tokens = metrics.get("tokens")
        item.tokens = dict(tokens) if isinstance(tokens, Mapping) else {}

    def _collect_reference(
        self, item: CaseItem, case: Mapping[str, Any] | None = None
    ) -> None:
        """Freeze the case metadata and expectations the arbitration export needs.

        Called twice per item: once right after the SDK stages (a ``validate``-only batch never
        starts a run, but must still export intent and reference values) and once at metrics
        collection time. ``case`` is the effective case: for a blocked case with a follow-up it
        is the replacement request, so the export shows what was actually sent.
        """
        if case is None:
            case = self._catalog.get(item.case_id)
        if case is None:
            return
        expect = case.get("expect")
        protocols = expect.get("protocols") if isinstance(expect, Mapping) else None
        protocol_block = protocols.get(PROTOCOL_A2A_T) if isinstance(protocols, Mapping) else None
        block = protocol_block if isinstance(protocol_block, Mapping) else {}
        negotiation = block.get("negotiation")
        negotiation_block = negotiation if isinstance(negotiation, Mapping) else {}
        raw_intent = expect.get("intent") if isinstance(expect, Mapping) else None
        item.refs = {
            "caseName": str(case.get("name") or ""),
            "operation": str(case.get("operation") or ""),
            "designer": str(case.get("designer") or ""),
            "qaId": f"TC{item.case_id:08d}",
            "uid": str(case.get("uid") or ""),
            "taskT": str(block.get("task_t") or ""),
            "userInput": str(case.get("user_input") or ""),
            "expectedIntent": (
                {str(key): value for key, value in raw_intent.items()}
                if isinstance(raw_intent, Mapping)
                else {}
            ),
            "negotiationExpected": bool(negotiation_block.get("expected")),
            "expectedRounds": int(negotiation_block.get("expected_rounds") or 0),
            "expectedKinds": list(negotiation_block.get("kinds") or []),
            "expectedFinalState": block.get("final_state"),
        }
        stage = item.stages.get("intent")
        if stage is not None and isinstance(stage.actual, Mapping):
            item.intent = {str(key): value for key, value in stage.actual.items()}
        if not item.intent_meta.get("rawRequest"):
            item.intent_meta["rawRequest"] = str(item.refs.get("userInput") or "")
            item.intent_meta["normalizedText"] = item.intent_meta["rawRequest"]
        if not item.intent_meta.get("source"):
            item.intent_meta["source"] = "sdk" if item.intent is not None else ""

    def _collect_run_evidence(
        self,
        item: CaseItem,
        run: RunState,
        case: Mapping[str, Any] | None = None,
    ) -> None:
        """Collect the trace id, the operator request and the reference answer of one run."""
        try:
            item.trace_id = run.trace_id or run.run_id
            self._collect_reference(item, case)
            raw_request = run.raw_request or str(item.refs.get("userInput") or "")
            item.intent_meta["rawRequest"] = raw_request
            item.intent_meta["normalizedText"] = run.normalized_request or raw_request
            # The arbitration intent is what the downstream understood, not our own extraction.
            # ``item.intent`` keeps the SDK document as auxiliary evidence.
            self._judge_server_intent(item, run, case)
            item.intent_meta["source"] = (
                "server"
                if item.server_intent is not None
                else ("sdk" if item.intent is not None else "")
            )
            traceparent, traceparent_source = read_traceparent(
                self._history_dir.parent if self._history_dir is not None else None,
                run.run_id,
                trace_id=item.trace_id,
            )
            item.intent_meta["traceparentSource"] = traceparent_source
            item.answer = build_answer(run, refs=item.refs, traceparent=traceparent)
        except Exception:  # noqa: BLE001 - the export evidence must never fail the item
            _LOGGER.warning("tester export evidence failed for run %s", run.run_id, exc_info=True)

    def _judge_server_intent(
        self,
        item: CaseItem,
        run: RunState,
        case: Mapping[str, Any] | None,
    ) -> None:
        """Re-judge the ``intent`` stage against the downstream's own reading.

        The case asks what the server understood, not what our SDK extracted from the Task-T. The
        stage keeps the SDK extraction until the run starts; once the downstream accepts the Task
        and returns its payload with ``TASK_STATE_SUBMITTED``, that payload becomes the actual and
        the stage is compared with ``expect.intent``. ``item.intent`` is left untouched so the
        auxiliary SDK column still exports the pre-send extraction.
        """
        payload = run.server_intent
        item.server_intent = dict(payload) if isinstance(payload, Mapping) else None
        stage = item.stages.get("intent")
        if stage is None or case is None:
            return
        if item.server_intent is None:
            # Our own RAN Agent accepts a Task without returning its reading; that is evidence
            # missing, not a wrong understanding, so it must not turn the row red.
            stage.status = STAGE_RECORDED
            stage.actual = None
            stage.diffs = []
            stage.message = _NO_SERVER_INTENT_MESSAGE
            return
        stage.actual = dict(item.server_intent)
        stage.diffs = []
        if self._nms_sim:
            stage.status = STAGE_RECORDED
            stage.message = _SIM_SERVER_INTENT_MESSAGE
            return
        expected = item.refs.get("expectedIntent")
        if not isinstance(expected, Mapping) or not expected:
            stage.status = STAGE_RECORDED
            stage.message = _NO_EXPECTED_INTENT_MESSAGE
            return
        diffs = compare_intent(expected, item.server_intent)
        stage.diffs = [diff.to_dict() for diff in diffs]
        if not _assertion_enabled(case, "", "intent"):
            stage.status = STAGE_RECORDED
            stage.message = _ASSERTION_OFF_MESSAGE
        elif diffs:
            stage.status = STAGE_FAILED
            stage.message = _SERVER_INTENT_MISMATCH_MESSAGE
        else:
            stage.status = STAGE_PASSED
            stage.message = ""

    async def _ensure_prompt_client(self, model_mode: str | None = None) -> Any:
        """Return the SDK prompt client of one model mode (offline scripted by default)."""
        if self._prompt_client is not None:
            return self._prompt_client
        key = model_mode or self._model_mode or MODEL_MODE_OFFLINE
        if key in self._prompt_errors:
            raise RuntimeError(self._prompt_errors[key])
        cached = self._prompt_clients.get(key)
        if cached is not None:
            return cached
        try:
            client = await asyncio.to_thread(build_client, model_mode=model_mode)
        except Exception as exc:  # noqa: BLE001 - reported per case
            self._prompt_errors[key] = f"{type(exc).__name__}: {exc}"
            raise
        self._prompt_clients[key] = client
        return client

    def _item_status(self, item: CaseItem) -> str:
        statuses = [result.status for result in item.stages.values()]
        if item.interception is not None:
            statuses.extend(_interception_statuses(item.interception))
        if any(status == STAGE_ERROR for status in statuses):
            return CASE_ERROR
        if any(status == STAGE_FAILED for status in statuses):
            return CASE_FAILED
        if any(status == STAGE_WAITING for status in statuses):
            return CASE_PARTIAL
        if item.error:
            # The run failed, but its only red stage was recorded (assertion off / simulator
            # dialogue). A green row would overstate what the batch proved.
            return CASE_PARTIAL
        return CASE_PASSED

    # ------------------------------------------------------------------
    # Approval automation
    # ------------------------------------------------------------------

    async def _answer_approval_gate(self, approval: str, protocol: str, run: RunState) -> None:
        """Auto-answer the A2A-T server approval gate; ``auto`` waits for the timeout.

        MIE approval is out of scope for the tester; only the A2A-T server gate is handled.
        """
        if approval == "auto":
            return
        if protocol != PROTOCOL_A2A_T or not self._server_url:
            return
        await self._watch_server_gate(run, approval == "approve")

    async def _watch_server_gate(self, run: RunState, approved: bool) -> None:
        snapshot, queue = run.subscribe()
        handled: set[str] = set()
        headers = {"authorization": f"Bearer {self._server_auth_token}"} if self._server_auth_token else {}
        try:
            for message in snapshot:
                await self._handle_server_gate(message, run, approved, handled, headers)
            while not run.finished():
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=0.5)
                except TimeoutError:
                    continue
                await self._handle_server_gate(message, run, approved, handled, headers)
        finally:
            run.unsubscribe(queue)

    async def _handle_server_gate(
        self,
        message: dict[str, object],
        run: RunState,
        approved: bool,
        handled: set[str],
        headers: Mapping[str, str],
    ) -> None:
        if message.get("type") != "trace":
            return
        span = message.get("span")
        detail = span.get("detail") if isinstance(span, Mapping) else None
        if not isinstance(span, Mapping) or not isinstance(detail, Mapping):
            return
        if span.get("kind") != "approval" or detail.get("approvalState") != "requested":
            return
        marker = str(span.get("spanId") or message.get("seq") or "")
        if marker in handled:
            return
        handled.add(marker)
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
                response = await client.post(
                    f"{self._server_url}/approvals",
                    json={"runId": run.run_id, "approved": approved},
                    headers=dict(headers),
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            _LOGGER.warning("tester approval forward failed: %s", exc)

    # ------------------------------------------------------------------
    # Persistence and snapshots
    # ------------------------------------------------------------------

    def _persist(self, batch: _Batch) -> None:
        if self._history_dir is None:
            return
        try:
            self._history_dir.mkdir(parents=True, exist_ok=True)
            document = self._snapshot(batch)
            target = self._history_dir / f"{batch.batch_id}.json"
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(target)
        except OSError as exc:
            _LOGGER.warning("tester batch %s could not be persisted: %s", batch.batch_id, exc)
            batch.error = f"批次持久化失败：{exc}"

    def _load(self, batch_id: str) -> _Batch | None:
        if self._history_dir is None:
            return None
        path = self._history_dir / f"{batch_id}.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(document, Mapping) or document.get("schema") not in _SCHEMAS:
            return None
        raw_items = document.get("items")
        if not isinstance(raw_items, list):
            return None
        items = [
            CaseItem(
                case_id=int(item.get("caseId") or 0),
                name=str(item.get("name") or ""),
                protocol=str(item.get("protocol") or PROTOCOL_A2A_T),
                status=str(item.get("status") or CASE_PENDING),
                stages=_load_stages(item.get("stages")),
                run_id=item.get("runId") if isinstance(item.get("runId"), str) else None,
                duration_ms=item.get("durationMs") if isinstance(item.get("durationMs"), int) else None,
                tokens=dict(item.get("tokens") or {}),
                error=item.get("error") if isinstance(item.get("error"), str) else None,
                baseline=dict(item.get("baseline") or {}) if isinstance(item.get("baseline"), Mapping) else None,
                scratch=item.get("scratch") if isinstance(item.get("scratch"), str) else None,
                attempts=_load_attempts(item.get("attempts")),
                trace_id=item.get("traceId") if isinstance(item.get("traceId"), str) else "",
                intent=_optional_mapping(item.get("intent")),
                server_intent=_optional_mapping(item.get("serverIntent")),
                intent_meta=_mapping_or_empty(item.get("intentMeta")),
                refs=_mapping_or_empty(item.get("refs")),
                answer=_optional_mapping(item.get("answer")),
                interception=_optional_mapping(item.get("interception")),
            )
            for item in raw_items
            if isinstance(item, Mapping)
        ]
        defaults = self._protocol_defaults.get(PROTOCOL_A2A_T, {})
        batch = _Batch(
            batch_id=str(document.get("batchId") or batch_id),
            case_ids=[int(value) for value in document.get("caseIds", []) if isinstance(value, int)],
            protocols=[str(value) for value in document.get("protocols", [])],
            stop_after=str(document.get("stopAfter") or "final"),
            approval=document.get("approval") if isinstance(document.get("approval"), str) else None,
            timeout_seconds=_optional_float(document.get("timeoutSeconds")),
            working_timeout_seconds=_optional_float(document.get("workingTimeoutSeconds")),
            decision_mode=str(document.get("decisionMode") or DECISION_MODE_SCRIPTED),
            keep_tasks=bool(document.get("keepTasks")),
            transport=str(document.get("transport") or defaults.get("transport", "sse")),
            discovery=str(document.get("discovery") or defaults.get("discovery", "direct")),
            items=items,
            owner_id=str(document.get("ownerId") or ""),
            status=str(document.get("status") or BATCH_DRAFT),
            created_at=str(document.get("createdAt") or _now_iso()),
            started_at=document.get("startedAt") if isinstance(document.get("startedAt"), str) else None,
            finished_at=document.get("finishedAt") if isinstance(document.get("finishedAt"), str) else None,
        )
        if batch.status == BATCH_RUNNING:
            for item in batch.items:
                if item.status in {CASE_PENDING, CASE_RUNNING}:
                    item.status = CASE_CANCELLED
                    item.error = "UI 进程已重启，批次未执行完。"
            batch.status = BATCH_CANCELLED
            batch.finished_at = _now_iso()
        self._batches[batch_id] = batch
        return batch

    def _snapshot(self, batch: _Batch) -> dict[str, object]:
        items = [item.to_dict() for item in batch.items]
        return {
            "schema": SCHEMA,
            "batchId": batch.batch_id,
            "status": batch.status,
            "caseIds": list(batch.case_ids),
            "protocols": list(batch.protocols),
            "stopAfter": batch.stop_after,
            "approval": batch.approval,
            "timeoutSeconds": batch.timeout_seconds,
            "workingTimeoutSeconds": batch.working_timeout_seconds,
            "decisionMode": batch.decision_mode,
            "keepTasks": batch.keep_tasks,
            "transport": batch.transport,
            "discovery": batch.discovery,
            "resolvedRunner": {
                "approval": batch.approval,
                "timeoutSeconds": batch.timeout_seconds,
                "workingTimeoutSeconds": batch.working_timeout_seconds,
            },
            "ownerId": batch.owner_id,
            "createdAt": batch.created_at,
            "startedAt": batch.started_at,
            "finishedAt": batch.finished_at,
            "currentIndex": batch.current_index,
            "error": batch.error,
            "items": items,
            "summary": _summary(batch.items),
        }


def _summary(items: Sequence[CaseItem]) -> dict[str, int]:
    counts = {
        "total": len(items),
        "pending": 0,
        "running": 0,
        "passed": 0,
        "failed": 0,
        "error": 0,
        "cancelled": 0,
        "partial": 0,
        "durationMs": 0,
        "stagePassed": 0,
        "stageFailed": 0,
        "stageRecorded": 0,
        "stageError": 0,
        "stageWaiting": 0,
    }
    for item in items:
        if item.status in counts:
            counts[item.status] += 1
        if isinstance(item.duration_ms, int):
            counts["durationMs"] += item.duration_ms
        for stage in item.stages.values():
            if stage.status == STAGE_PASSED:
                counts["stagePassed"] += 1
            elif stage.status == STAGE_FAILED:
                counts["stageFailed"] += 1
            elif stage.status == STAGE_RECORDED:
                counts["stageRecorded"] += 1
            elif stage.status == STAGE_ERROR:
                counts["stageError"] += 1
            elif stage.status == STAGE_WAITING:
                counts["stageWaiting"] += 1
    return counts


def _baseline_request(case: Mapping[str, Any] | None = None) -> str:
    """Return the Create request paired with one Modify case.

    Every Modify case ships a ``baseline.request``: the manual-acceptance Create Task-T for
    ``A2ATestRegion`` (three day segments, no outage), which the vendor accepts without opening a
    negotiation (see eval/osp/README.md 3.5). This function returns it. A case without one falls
    back to the shared fixture below, which keeps the previous behaviour for hand-written rows.

    The demo's ``Region A`` placeholder is not a served region: the real NMS and the simulator
    answer it with an information negotiation. A Modify baseline must therefore name the
    configured ``A2AT_SAMPLE_NMS_REGION`` (the simulator's region in a ``--nms-sim`` run).
    """
    override = _case_baseline_request(case)
    if override is not None:
        return override
    region = os.environ.get("A2AT_SAMPLE_NMS_REGION", "").strip()
    if not region:
        try:
            from shared.config import load_config

            region = (load_config().nms_region or "").strip()
        except Exception:  # noqa: BLE001 - the baseline falls back to the test region
            region = ""
    region = region or BASELINE_REGION
    window = os.environ.get("A2AT_SAMPLE_NMS_SIM_WINDOW", "").strip() or BASELINE_WINDOW
    # A single-segment plan when the served window differs, otherwise the day plan would carry two
    # overlapping segments for the same hours. Prose again: the scripted provider must not rewrite
    # the canned Task Context.
    if window == BASELINE_WINDOW:
        schedule = BASELINE_SCHEDULE
    else:
        start, end = (part.strip() for part in window.split("~", 1))
        schedule = f"guarantee 10Mbps from {start} to {end}"
    return BASELINE_REQUEST.replace(BASELINE_REGION, region).replace(BASELINE_SCHEDULE, schedule)


def _case_baseline_request(case: Mapping[str, Any] | None) -> str | None:
    """Return a case's own ``baseline.request``, when it overrides the shared fixture."""
    if not isinstance(case, Mapping):
        return None
    block = case.get("baseline")
    if not isinstance(block, Mapping):
        return None
    request = block.get("request")
    return request.strip() if isinstance(request, str) and request.strip() else None


def _baseline_accepted_states(protocol: str) -> frozenset[str]:
    """Return the states a Modify can continue from, for one protocol.

    ``COMPLETED`` is excluded on purpose even though it is an acceptance signal for a Create:
    the A2A layer rejects a message addressed to a terminal Task.
    """
    if protocol == RUNTIME_A2A_T:
        return frozenset({TASK_STATE_SUBMITTED, TASK_STATE_WORKING})
    return frozenset({TASK_STATE_WORKING})


def _baseline_failure_message(protocol: str, state: str | None) -> str:
    """Explain why the paired Create cannot be continued by a Modify."""
    if protocol == RUNTIME_A2A_T:
        if state == "TASK_STATE_INPUT_REQUIRED":
            return (
                "Modify 基线 Create 触发了协商（TASK_STATE_INPUT_REQUIRED），不是可续传状态；"
                "请检查 baseline.request：必须显式使用 A2ATestRegion，并采用手工验收那类"
                "不拦截、不协商的三段日计划。"
            )
        return (
            f"Modify 基线任务未到达可续传状态（当前 {state or '未观察到状态'}）；"
            "任务已是终态时服务端会拒绝 Modify。"
        )
    return (
        f"Modify 基线任务的纯 A2A 外层 Task 未停留在 WORKING（当前 {state or '未观察到状态'}）；"
        "纯 A2A 只能续传非终态 Task，请使用 --nms-sim + A2AT_SAMPLE_NMS_SIM_HOLD_WORKING=1 "
        "或真实 NMS。"
    )


def _negotiation_expected(case: Mapping[str, Any], protocol: str) -> bool:
    """Return whether the case expects a negotiation round on one protocol."""
    expect = case.get("expect")
    protocols = expect.get("protocols") if isinstance(expect, Mapping) else None
    block = protocols.get(protocol) if isinstance(protocols, Mapping) else None
    negotiation = block.get("negotiation") if isinstance(block, Mapping) else None
    return bool(isinstance(negotiation, Mapping) and negotiation.get("expected"))


def _case_runner(case: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Return the ``runner`` block of a case, empty when absent."""
    if not isinstance(case, Mapping):
        return {}
    runner = case.get("runner")
    return runner if isinstance(runner, Mapping) else {}


def _pre_send_validation(runner: Mapping[str, Any]) -> bool | None:
    """Map ``runner.pre_send_validation`` to the run override (``None`` keeps the process rule)."""
    value = runner.get("pre_send_validation")
    if value == "always":
        return True
    if value == "off":
        return False
    return None


def _assertion_enabled(case: Mapping[str, Any], protocol: str, key: str) -> bool:
    """Return whether ``case.assert`` enables one assertion.

    ``protocol=""`` reads the top-level key (``intent``); otherwise the protocol block is used
    (``a2a-t.task_t`` / ``a2a.negotiation``). A missing flag means enabled.
    """
    block = case.get("assert")
    if not isinstance(block, Mapping):
        return True
    container: object = block.get(protocol) if protocol else block
    if not isinstance(container, Mapping):
        return True
    value = container.get(key)
    return True if value is None else bool(value)


def _apply_assertion(
    case: Mapping[str, Any], protocol: str, key: str, result: StageResult
) -> StageResult:
    """Keep the observed values but record (never judge) a stage whose assertion is disabled."""
    if _assertion_enabled(case, protocol, key):
        return result
    result.status = STAGE_RECORDED
    result.message = _ASSERTION_OFF_MESSAGE
    return result


def _interception_statuses(record: Mapping[str, object]) -> list[str]:
    """Return the stage statuses of one interception record (empty when malformed)."""
    stages = record.get("stages")
    if not isinstance(stages, Mapping):
        return []
    return [
        str(stage.get("status"))
        for stage in stages.values()
        if isinstance(stage, Mapping) and stage.get("status")
    ]


def _baseline_run_id(item: CaseItem) -> str | None:
    """Return the run that created a Modify item's baseline Task, when one was seeded."""
    baseline = item.baseline
    if not isinstance(baseline, Mapping):
        return None
    value = baseline.get("runId")
    return value if isinstance(value, str) and value else None


def _task_observation(run: RunState) -> dict[str, object]:
    """Return the Task identity/state the run last observed."""
    return {
        "taskId": run.remote_task_id,
        "contextId": run.remote_context_id,
        "state": run.latest_task_state,
    }


def _load_stages(raw: object) -> dict[str, StageResult]:
    """Restore the persisted stage results of one item (v1 batches have none)."""
    if not isinstance(raw, Mapping):
        return {}
    stages: dict[str, StageResult] = {}
    for name, value in raw.items():
        if not isinstance(value, Mapping):
            continue
        raw_diffs = value.get("diffs")
        stages[str(name)] = StageResult(
            stage=str(value.get("stage") or name),
            status=str(value.get("status") or STAGE_ERROR),
            message=str(value.get("message") or ""),
            expected=value.get("expected"),
            actual=value.get("actual"),
            diffs=[
                dict(diff) for diff in raw_diffs if isinstance(diff, Mapping)
            ]
            if isinstance(raw_diffs, list)
            else [],
            duration_ms=int(value.get("durationMs") or 0),
        )
    return stages


def _load_attempts(raw: object) -> list[dict[str, object]]:
    """Restore the previous attempts of one item."""
    if not isinstance(raw, list):
        return []
    return [dict(attempt) for attempt in raw if isinstance(attempt, Mapping)]


def _optional_mapping(raw: object) -> dict[str, object] | None:
    """Restore an optional JSON object persisted in a batch document."""
    return _mapping_or_empty(raw) if isinstance(raw, Mapping) else None


def _mapping_or_empty(raw: object) -> dict[str, object]:
    """Restore a JSON object, empty when the batch predates the field."""
    return {str(key): value for key, value in raw.items()} if isinstance(raw, Mapping) else {}


def _validate_approval(approval: str | None) -> str | None:
    """Validate one optional batch approval value."""
    if approval is None:
        return None
    if approval not in {"approve", "reject", "auto"}:
        raise ValueError(f"unknown approval {approval!r}")
    return approval


def _positive_or_none(value: float | None) -> float | None:
    """Return a positive float, or ``None`` when the caller wants the per-case value."""
    if value is None:
        return None
    return max(1.0, float(value))


def _optional_float(value: object) -> float | None:
    """Return a float persisted in a batch document, or ``None`` when absent/invalid."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _runtime_protocol(protocol: str) -> str:
    """Map the catalog protocol label to the value the run manager expects."""
    return PROTOCOL_PURE if protocol == PROTOCOL_A2A else protocol


__all__ = [
    "ALL_STAGES",
    "BATCH_CANCELLED",
    "BATCH_DRAFT",
    "BATCH_FINISHED",
    "BATCH_RUNNING",
    "CASE_CANCELLED",
    "CASE_ERROR",
    "CASE_FAILED",
    "CASE_PARTIAL",
    "CASE_PASSED",
    "CASE_PENDING",
    "CASE_RUNNING",
    "DECISION_MODES",
    "DECISION_MODE_LIVE",
    "DECISION_MODE_SCRIPTED",
    "DEFAULT_WORKING_TIMEOUT_SECONDS",
    "EvalBatchManager",
    "SCHEMA",
    "STAGE_SKIPPED",
    "STAGE_WAITING",
]
