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
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx
from eval.osp.catalog import PROTOCOL_A2A, PROTOCOL_A2A_T, CaseCatalog
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
from shared.run_constants import (
    PROTOCOL_A2A_T as RUNTIME_A2A_T,
)
from shared.run_constants import (
    PROTOCOL_PURE,
)

from demo.web.tester.decisions import a2at_decisions, pure_answers

_LOGGER = logging.getLogger("ran_energy_saving.tester")

#: Every stage the console can run.
ALL_STAGES: Final[tuple[str, ...]] = (
    "intent",
    "task_t",
    "validate",
    "send",
    "negotiate",
    "final",
)

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

#: Persisted batch schema. Older Excel batches are ignored on load.
SCHEMA: Final[str] = "osp-tester/v1"

#: Scenario used to seed a Modify target task; it is a complete request that creates a task.
BASELINE_SCENARIO: Final[str] = "night"

#: Complete baseline request (the scenario's own request still ends at 23:59, which the live
#: server now negotiates because 24:00 is the end of the day).
BASELINE_REQUEST: Final[str] = (
    "Create an energy saving task for Region A with these guaranteed throughputs: "
    "{00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~24:00,10Mbps}."
)

#: Polling interval of the batch runner.
_POLL_SECONDS: Final[float] = 0.2

#: Grace after a stop-after stage before the run is cancelled.
_CANCEL_GRACE_SECONDS: Final[float] = 1.0


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
        }


@dataclass
class _Batch:
    """Mutable state of one tester batch."""

    batch_id: str
    case_ids: list[int]
    protocols: list[str]
    stop_after: str
    approval: str
    timeout_seconds: float
    items: list[CaseItem]
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
        mie_url: str | None,
        runs_dir: Path | None = None,
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
        self._mie_url = (mie_url or "").rstrip("/")
        self._history_dir = (Path(runs_dir) / "tester") if runs_dir is not None else None
        self._language = language
        self._model_mode = model_mode
        self._model_label = model_label
        self._metrics_grace_seconds = max(0.0, metrics_grace_seconds)
        self._prompt_client = prompt_client
        self._prompt_error: str | None = None
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
        approval: str,
        timeout_seconds: float,
    ) -> dict[str, object]:
        """Register one draft batch from the selected cases and protocols."""
        if stop_after not in ALL_STAGES:
            raise ValueError(f"unknown stop_after {stop_after!r}")
        if approval not in {"approve", "reject", "auto"}:
            raise ValueError(f"unknown approval {approval!r}")
        selected_protocols = [protocol for protocol in protocols if protocol in {PROTOCOL_A2A_T, PROTOCOL_A2A}]
        if not selected_protocols:
            raise ValueError("至少选择一个协议。")
        items: list[CaseItem] = []
        for case_id in case_ids:
            case = self._catalog.get(case_id)
            if case is None:
                continue
            for protocol in selected_protocols:
                items.append(CaseItem(case_id=case_id, name=str(case["name"]), protocol=protocol))
        if not items:
            raise ValueError("没有可执行的用例。")
        batch = _Batch(
            batch_id=f"batch-{uuid.uuid4().hex[:12]}",
            case_ids=list(case_ids),
            protocols=selected_protocols,
            stop_after=stop_after,
            approval=approval,
            timeout_seconds=max(1.0, float(timeout_seconds)),
            items=items,
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
        timeout_seconds: float = 300.0,
    ) -> dict[str, object]:
        """Create a one-item batch for an ad-hoc request (full flow only)."""
        text = user_input.strip()
        if not text:
            raise ValueError("临时请求不能为空。")
        if protocol not in {PROTOCOL_A2A_T, PROTOCOL_A2A}:
            raise ValueError(f"unknown protocol {protocol!r}")
        item = CaseItem(case_id=0, name="临时请求", protocol=protocol, scratch=text)
        batch = _Batch(
            batch_id=f"batch-{uuid.uuid4().hex[:12]}",
            case_ids=[0],
            protocols=[protocol],
            stop_after="final",
            approval=approval,
            timeout_seconds=max(1.0, float(timeout_seconds)),
            items=[item],
        )
        self._batches[batch.batch_id] = batch
        self._persist(batch)
        return self._snapshot(batch)

    def get(self, batch_id: str) -> dict[str, object] | None:
        """Return one batch snapshot, or ``None`` when unknown."""
        batch = self._batches.get(batch_id) or self._load(batch_id)
        return None if batch is None else self._snapshot(batch)

    async def start(self, batch_id: str) -> dict[str, object]:
        """Start one draft batch."""
        batch = self._batches.get(batch_id) or self._load(batch_id)
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

    async def cancel(self, batch_id: str) -> bool:
        """Cancel one running batch and its active run."""
        batch = self._batches.get(batch_id) or self._load(batch_id)
        if batch is None or batch.status != BATCH_RUNNING:
            return False
        batch.cancel_requested = True
        if batch.current_index is not None:
            item = batch.items[batch.current_index]
            if item.run_id:
                await self._runs.cancel(item.run_id)
        if batch.task is not None and not batch.task.done():
            batch.task.cancel()
        self._persist(batch)
        return True

    async def shutdown(self) -> None:
        """Cancel every running batch."""
        for batch_id, batch in list(self._batches.items()):
            if batch.status == BATCH_RUNNING:
                await self.cancel(batch_id)

    def retry(self, batch_id: str) -> dict[str, object]:
        """Create a new draft batch from the failed, errored or cancelled items."""
        batch = self._batches.get(batch_id) or self._load(batch_id)
        if batch is None:
            raise KeyError(batch_id)
        selected = [
            item
            for item in batch.items
            if item.status in {CASE_FAILED, CASE_ERROR, CASE_CANCELLED}
        ]
        if not selected:
            raise ValueError("没有失败、错误或取消的用例可以重跑。")
        retried = _Batch(
            batch_id=f"batch-{uuid.uuid4().hex[:12]}",
            case_ids=batch.case_ids,
            protocols=batch.protocols,
            stop_after=batch.stop_after,
            approval=batch.approval,
            timeout_seconds=batch.timeout_seconds,
            items=[
                CaseItem(
                    case_id=item.case_id,
                    name=item.name,
                    protocol=item.protocol,
                    scratch=item.scratch,
                )
                for item in selected
            ],
        )
        self._batches[retried.batch_id] = retried
        self._persist(retried)
        return self._snapshot(retried)

    def list_batches(self, *, limit: int = 20) -> list[dict[str, object]]:
        """Return the most recent persisted batches, newest first."""
        if self._history_dir is None or not self._history_dir.is_dir():
            return [
                self._snapshot(batch)
                for batch in sorted(
                    self._batches.values(), key=lambda item: item.created_at, reverse=True
                )
            ][:limit]
        summaries: list[dict[str, object]] = []
        for path in sorted(
            self._history_dir.glob("*.json"),
            key=lambda candidate: candidate.stat().st_mtime,
            reverse=True,
        )[:limit]:
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(document, Mapping) or document.get("schema") != SCHEMA:
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
                }
            )
        return summaries

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _run_batch(self, batch: _Batch) -> None:
        try:
            for index, item in enumerate(batch.items):
                if batch.cancel_requested:
                    break
                batch.current_index = index
                await self._run_item(batch, index, item)
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
        except Exception as exc:  # noqa: BLE001 - one bad item must not stop the batch
            _LOGGER.warning("tester item %s/%s failed", item.case_id, item.protocol, exc_info=True)
            item.status = CASE_ERROR
            item.error = f"{type(exc).__name__}: {exc}"
        finally:
            item.duration_ms = int(round((time.monotonic() - started) * 1000))
            if item.status == CASE_RUNNING:
                item.status = CASE_PASSED
            self._persist(batch)

    async def _run_case_item(self, batch: _Batch, index: int, item: CaseItem) -> None:
        case = self._catalog.get(item.case_id)
        if case is None:
            item.status = CASE_ERROR
            item.error = f"未知用例 {item.case_id}"
            return
        stages = list(ALL_STAGES[: ALL_STAGES.index(batch.stop_after) + 1])
        if item.protocol == PROTOCOL_A2A and batch.stop_after in SDK_STAGE_ORDER:
            item.status = CASE_ERROR
            item.error = "A2A 协议没有 SDK 阶段；请把执行到设为 send / negotiate / final。"
            return
        sdk_stages = [stage for stage in stages if stage in SDK_STAGE_ORDER]
        if sdk_stages:
            await self._run_sdk_stages(case, item, sdk_stages[-1])
        if batch.stop_after in SDK_STAGE_ORDER:
            item.status = self._item_status(item)
            return
        await self._run_full_flow(batch, index, item, case, stages)
        item.status = self._item_status(item)

    async def _run_sdk_stages(self, case: Mapping[str, Any], item: CaseItem, stop_after: str) -> None:
        async with self._sdk_lock:
            client = await self._ensure_prompt_client()
            outcome = await asyncio.to_thread(run_case, case, client, stop_after=stop_after)
        for stage in outcome.stages:
            item.stages[stage.stage] = StageResult(
                stage=stage.stage,
                status=stage.status,
                message=stage.message,
                expected=stage.expected,
                actual=stage.actual,
                diffs=[diff.to_dict() for diff in stage.diffs],
            )
        if outcome.error:
            item.error = outcome.error

    async def _run_full_flow(
        self,
        batch: _Batch,
        index: int,
        item: CaseItem,
        case: Mapping[str, Any],
        stages: Sequence[str],
    ) -> None:
        runtime_protocol = _runtime_protocol(item.protocol)
        target_task_id: str | None = None
        target_context_id: str | None = None
        if runtime_protocol == RUNTIME_A2A_T and case.get("operation") == "Modify":
            baseline = await self._seed_baseline(batch, index, item)
            if baseline is None:
                for stage in ("send", "negotiate", "final"):
                    if stage in stages:
                        item.stages[stage] = StageResult(
                            stage, STAGE_ERROR, "Modify 基线任务创建失败。"
                        )
                item.status = CASE_ERROR
                return
            target_task_id = str(baseline["taskId"])
            target_context_id = str(baseline["contextId"])

        decisions = a2at_decisions(case) if runtime_protocol == RUNTIME_A2A_T else []
        answers = pure_answers(case) if runtime_protocol == PROTOCOL_PURE else []
        defaults = self._protocol_defaults.get(runtime_protocol, {})
        transport = defaults.get("transport", "sse")
        discovery = defaults.get("discovery", "direct")
        mode = MODE_SCRIPTED if runtime_protocol == RUNTIME_A2A_T else MODE_AUTO
        run = await self._runs.start(
            "night",
            mode,
            transport,
            discovery,
            str(case["user_input"]),
            protocol=runtime_protocol,
            conversation_id=f"tester-{batch.batch_id}-{index}",
            user_id="tester",
            idempotency_key=f"tester-{batch.batch_id}-{index}",
            source={
                "kind": "tester",
                "channel": "tester-console",
                "batchId": batch.batch_id,
                "caseId": item.case_id,
                "caseName": item.name,
            },
            target_task_id=target_task_id,
            target_context_id=target_context_id,
            scripted_decisions=decisions or None,
            pure_answers=answers or None,
            validate_before_send=True,
        )
        item.run_id = run.run_id
        self._persist(batch)
        approval_task = asyncio.create_task(
            self._answer_approval_gate(batch.approval, item.protocol, run),
            name=f"tester-approval-{run.run_id}",
        )
        try:
            await self._wait_for_stage(run, item, batch, stages)
        finally:
            approval_task.cancel()
            await asyncio.gather(approval_task, return_exceptions=True)
            await self._collect_metrics(item, run)
            if run.status == "running":
                await self._runs.cancel(run.run_id)
                await self._wait_for_run(run, _CANCEL_GRACE_SECONDS)

    async def _run_scratch(self, batch: _Batch, index: int, item: CaseItem) -> None:
        """Run an ad-hoc request through the full flow; no expectations are asserted."""
        if item.scratch is None:
            return
        runtime_protocol = _runtime_protocol(item.protocol)
        defaults = self._protocol_defaults.get(runtime_protocol, {})
        mode = MODE_SCRIPTED if runtime_protocol == RUNTIME_A2A_T else MODE_AUTO
        run = await self._runs.start(
            "night",
            mode,
            defaults.get("transport", "sse"),
            defaults.get("discovery", "direct"),
            item.scratch,
            protocol=runtime_protocol,
            conversation_id=f"tester-{batch.batch_id}-{index}",
            user_id="tester",
            idempotency_key=f"tester-{batch.batch_id}-{index}",
            source={"kind": "tester", "channel": "tester-scratch", "batchId": batch.batch_id},
            scripted_decisions=(),
            validate_before_send=True,
        )
        item.run_id = run.run_id
        self._persist(batch)
        timed_out = await self._wait_for_run(run, batch.timeout_seconds)
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

    async def _seed_baseline(self, batch: _Batch, index: int, item: CaseItem) -> dict[str, object] | None:
        """Create one baseline task for a Modify A2A-T item."""
        defaults = self._protocol_defaults.get(PROTOCOL_A2A_T, {})
        run = await self._runs.start(
            BASELINE_SCENARIO,
            MODE_SCRIPTED,
            defaults.get("transport", "sse"),
            defaults.get("discovery", "direct"),
            BASELINE_REQUEST,
            protocol=PROTOCOL_A2A_T,
            conversation_id=f"tester-{batch.batch_id}-{index}-baseline",
            user_id="tester",
            idempotency_key=f"tester-{batch.batch_id}-{index}-baseline",
            source={"kind": "tester", "channel": "tester-baseline", "batchId": batch.batch_id},
            scripted_decisions=(),
            validate_before_send=True,
        )
        timed_out = await self._wait_for_run(run, batch.timeout_seconds)
        if timed_out or run.result is None or not run.result.task_id or not run.result.context_id:
            item.baseline = {
                "runId": run.run_id,
                "status": "failed",
                "finalState": run.result.final_state if run.result is not None else None,
                "error": run.error,
            }
            item.error = run.error or "Modify 基线任务创建失败。"
            return None
        baseline = {
            "runId": run.run_id,
            "taskId": run.result.task_id,
            "contextId": run.result.context_id,
            "finalState": run.result.final_state,
        }
        item.baseline = baseline
        return baseline

    async def _wait_for_stage(
        self,
        run: RunState,
        item: CaseItem,
        batch: _Batch,
        stages: Sequence[str],
    ) -> None:
        """Wait for the last requested runtime stage and record its assertion."""
        expected_send = True
        expected_rounds = 0
        expected_kinds: list[str] | None = None
        expected_final: object = None
        case = self._catalog.get(item.case_id)
        if case is not None:
            protocol_block = case["expect"]["protocols"][item.protocol]
            if item.protocol == PROTOCOL_A2A_T:
                expected_send = bool(protocol_block.get("send"))
                expected_final = protocol_block.get("final_state")
            negotiation = protocol_block["negotiation"]
            expected_rounds = int(negotiation.get("expected_rounds", 0))
            expected_kinds = negotiation.get("kinds")

        started = time.monotonic()
        if "send" in stages:
            sent = await self._wait_for_span(run, "message_sent", batch.timeout_seconds)
            status = STAGE_PASSED if sent == expected_send else STAGE_FAILED
            item.stages["send"] = StageResult(
                "send",
                status,
                "" if status == STAGE_PASSED else "预期发送状态与观察结果不一致。",
                expected=expected_send,
                actual=sent,
                duration_ms=int(round((time.monotonic() - started) * 1000)),
            )
            if batch.stop_after == "send":
                return

        started = time.monotonic()
        if "negotiate" in stages:
            await self._wait_for_negotiation(run, expected_rounds, batch.timeout_seconds)
            actual_rounds = len(run.proposals)
            actual_kinds = [proposal.kind for proposal in run.proposals]
            rounds_ok = actual_rounds == expected_rounds
            kinds_ok = expected_kinds is None or all(
                kind in actual_kinds for kind in expected_kinds
            )
            status = STAGE_PASSED if rounds_ok and kinds_ok else STAGE_FAILED
            item.stages["negotiate"] = StageResult(
                "negotiate",
                status,
                "" if status == STAGE_PASSED else "协商轮次或类型与预期不一致。",
                expected={"rounds": expected_rounds, "kinds": expected_kinds},
                actual={"rounds": actual_rounds, "kinds": actual_kinds},
                duration_ms=int(round((time.monotonic() - started) * 1000)),
            )
            if batch.stop_after == "negotiate":
                return

        started = time.monotonic()
        if "final" in stages:
            await self._wait_for_run(run, batch.timeout_seconds)
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
            item.stages["final"] = StageResult(
                "final",
                status,
                message,
                expected=expected_final,
                actual=actual_final,
                duration_ms=int(round((time.monotonic() - started) * 1000)),
            )

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
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            if expected_rounds > 0 and len(run.proposals) >= expected_rounds:
                return
            if run.finished():
                return
            await asyncio.sleep(_POLL_SECONDS)

    async def _collect_metrics(self, item: CaseItem, run: RunState) -> None:
        if self._metrics_grace_seconds:
            await asyncio.sleep(self._metrics_grace_seconds)
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

    async def _ensure_prompt_client(self) -> Any:
        if self._prompt_client is not None:
            return self._prompt_client
        if self._prompt_error is not None:
            raise RuntimeError(self._prompt_error)
        try:
            self._prompt_client = await asyncio.to_thread(build_client)
        except Exception as exc:  # noqa: BLE001 - reported per case
            self._prompt_error = f"{type(exc).__name__}: {exc}"
            raise
        return self._prompt_client

    def _item_status(self, item: CaseItem) -> str:
        statuses = [result.status for result in item.stages.values()]
        if any(status == STAGE_ERROR for status in statuses):
            return CASE_ERROR
        if any(status == STAGE_FAILED for status in statuses):
            return CASE_FAILED
        return CASE_PASSED

    # ------------------------------------------------------------------
    # Approval automation
    # ------------------------------------------------------------------

    async def _answer_approval_gate(self, approval: str, protocol: str, run: RunState) -> None:
        if approval == "auto":
            return
        approved = approval == "approve"
        watchers: list[asyncio.Task[None]] = []
        if protocol == PROTOCOL_A2A_T and self._server_url:
            watchers.append(asyncio.create_task(self._watch_server_gate(run, approved)))
        if protocol == PROTOCOL_A2A and self._mie_url:
            watchers.append(asyncio.create_task(self._watch_mie_gate(approved)))
        if watchers:
            await asyncio.gather(*watchers, return_exceptions=True)

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

    async def _watch_mie_gate(self, approved: bool) -> None:
        handled: set[str] = set()
        try:
            async with httpx.AsyncClient(timeout=None, trust_env=False) as client:
                async with client.stream("GET", f"{self._mie_url}/approvals/events") as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            payload = json.loads(line[5:].strip())
                        except ValueError:
                            continue
                        if not isinstance(payload, Mapping):
                            continue
                        if payload.get("type") != "approval_requested":
                            continue
                        task_id = str(payload.get("taskId") or "")
                        if not task_id or task_id in handled:
                            continue
                        handled.add(task_id)
                        try:
                            await client.post(
                                f"{self._mie_url}/approvals/{task_id}",
                                json={"approved": approved},
                            )
                        except httpx.HTTPError as exc:
                            _LOGGER.warning("tester MIE decision failed: %s", exc)
        except httpx.HTTPError as exc:
            _LOGGER.warning("tester MIE event stream stopped: %s", exc)

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

    def _load(self, batch_id: str) -> _Batch | None:
        if self._history_dir is None:
            return None
        path = self._history_dir / f"{batch_id}.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(document, Mapping) or document.get("schema") != SCHEMA:
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
                run_id=item.get("runId") if isinstance(item.get("runId"), str) else None,
                duration_ms=item.get("durationMs") if isinstance(item.get("durationMs"), int) else None,
                tokens=dict(item.get("tokens") or {}),
                error=item.get("error") if isinstance(item.get("error"), str) else None,
                baseline=dict(item.get("baseline") or {}) if isinstance(item.get("baseline"), Mapping) else None,
                scratch=item.get("scratch") if isinstance(item.get("scratch"), str) else None,
            )
            for item in raw_items
            if isinstance(item, Mapping)
        ]
        batch = _Batch(
            batch_id=str(document.get("batchId") or batch_id),
            case_ids=[int(value) for value in document.get("caseIds", []) if isinstance(value, int)],
            protocols=[str(value) for value in document.get("protocols", [])],
            stop_after=str(document.get("stopAfter") or "final"),
            approval=str(document.get("approval") or "approve"),
            timeout_seconds=float(document.get("timeoutSeconds") or 300.0),
            items=items,
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
        "durationMs": 0,
        "stagePassed": 0,
        "stageFailed": 0,
        "stageRecorded": 0,
        "stageError": 0,
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
    return counts


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
    "CASE_PASSED",
    "CASE_PENDING",
    "CASE_RUNNING",
    "EvalBatchManager",
    "SCHEMA",
]
