"""Process-level batch runner of the tester console.

A batch owns the imported cases and runs them one at a time. It deliberately reuses
:class:`~protocol.client_service.RunManager` instead of re-implementing the client flow, so a
tester case behaves exactly like a run started from the main page.

Only one batch may execute at a time: the webhook receiver and its queue are process-wide, and a
second webhook batch would consume the first batch's pushes.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import httpx
from protocol.client_runtime import MODE_AUTO
from protocol.client_service import STATUS_FAILED, RunManager, RunState
from protocol.run_metrics import build_run_metrics
from shared.run_constants import PROTOCOL_A2A_T, PROTOCOL_PURE

from demo.web.tester.excel import APPROVAL_APPROVE, APPROVAL_AUTO, TestCase

_LOGGER = logging.getLogger("ran_energy_saving.tester")

#: Batch states.
BATCH_DRAFT: Final[str] = "draft"
BATCH_RUNNING: Final[str] = "running"
BATCH_FINISHED: Final[str] = "finished"
BATCH_CANCELLED: Final[str] = "cancelled"

#: Case result states.
CASE_PENDING: Final[str] = "pending"
CASE_RUNNING: Final[str] = "running"
CASE_PASSED: Final[str] = "passed"
CASE_FAILED: Final[str] = "failed"
CASE_SKIPPED: Final[str] = "skipped"
CASE_INVALID: Final[str] = "invalid"
CASE_CANCELLED: Final[str] = "cancelled"
CASE_ERROR: Final[str] = "error"

#: Polling interval of the batch runner.
_POLL_SECONDS: Final[float] = 0.2

#: How long the runner waits for a cancelled run to publish its terminal state.
_CANCEL_GRACE_SECONDS: Final[float] = 5.0


def _now_iso() -> str:
    """Return the current UTC time in ISO 8601 form."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _pure_negotiation(run: RunState) -> tuple[str, int]:
    """Return the negotiation kind and highest round observed on a standard-A2A run."""
    result = run.result
    events = result.events if result is not None else []
    kinds: list[str] = []
    rounds: list[int] = []
    for event in events:
        metadata = event.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        pure = metadata.get("pure")
        if not isinstance(pure, Mapping) or pure.get("kind") != "negotiation":
            continue
        phase = pure.get("phase")
        if isinstance(phase, str) and phase:
            kinds.append(phase)
        round_value = pure.get("round")
        if isinstance(round_value, int):
            rounds.append(round_value)
    return (kinds[0] if kinds else "none", max(rounds, default=0))


def _a2at_negotiation(run: RunState) -> tuple[str, int]:
    """Return the negotiation kind and highest round observed on an A2A-T run."""
    proposals = run.proposals
    if not proposals:
        return "none", 0
    return proposals[0].kind, max(proposal.round for proposal in proposals)


def _actual_negotiation(run: RunState) -> tuple[str, int]:
    """Return the actual negotiation kind/round of a run, whatever request form it used."""
    if run.protocol == PROTOCOL_PURE:
        return _pure_negotiation(run)
    return _a2at_negotiation(run)


def _expectation_failure(case: TestCase, run: RunState) -> str | None:
    """Return a human-readable failure message, or ``None`` when the case passed."""
    final_state = run.result.final_state if run.result is not None else ""
    if final_state != case.expected_final_state:
        return f"预期最终状态 {case.expected_final_state}，实际 {final_state or '空'}。"
    actual_kind, actual_rounds = _actual_negotiation(run)
    if case.expected_kind:
        if case.expected_kind == "none":
            if actual_kind != "none":
                return f"预期不协商，实际协商类型为 {actual_kind}。"
        elif actual_kind != case.expected_kind:
            return f"预期协商类型 {case.expected_kind}，实际 {actual_kind}。"
    if case.expected_rounds is not None and actual_rounds != case.expected_rounds:
        return f"预期协商轮次 {case.expected_rounds}，实际 {actual_rounds}。"
    return None


def _case_from_dict(document: Mapping[str, object]) -> TestCase | None:
    """Rebuild one parsed case from its persisted snapshot; ``None`` for a malformed row."""
    errors = document.get("errors")
    rounds = document.get("expectedRounds")
    try:
        return TestCase(
            case_id=str(document["caseId"]),
            name=str(document.get("name") or ""),
            enabled=bool(document.get("enabled", True)),
            protocol=str(document["protocol"]),
            scenario=str(document["scenario"]),
            scenario_explicit=bool(document.get("scenarioExplicit", True)),
            language=str(document.get("language") or "zh-CN"),
            user_request=str(document.get("userRequest") or ""),
            expected_final_state=str(document.get("expectedFinalState") or ""),
            expected_kind=str(document.get("expectedKind") or ""),
            expected_rounds=rounds if isinstance(rounds, int) else None,
            approval=str(document.get("approval") or "approve"),
            timeout_seconds=float(document.get("timeoutSeconds") or 300.0),
            note=str(document.get("note") or ""),
            errors=[str(item) for item in errors] if isinstance(errors, list) else [],
        )
    except (KeyError, TypeError, ValueError):
        return None


@dataclass
class _Batch:
    """Mutable state of one imported workbook."""

    batch_id: str
    cases: list[TestCase]
    results: list[dict[str, object]]
    status: str = BATCH_DRAFT
    source_name: str = ""
    created_at: str = field(default_factory=_now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    current_index: int | None = None
    error: str | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    cancel_requested: bool = False


class TestBatchManager:
    """Own the tester batches of one Web UI process.

    Args:
        runs: process-level run manager used to execute each case.
        protocol_defaults: per-protocol hop (``transport`` / ``discovery``) selected by the page.
        server_url: A2A server base URL, used to answer the A2A-T approval gate.
        server_auth_token: bearer token forwarded to the A2A server.
        mie_url: MIE Agent base URL, used to answer the standard-A2A approval gate.
        runs_dir: transcript directory; tester batches are persisted below ``runs/tester`` so a
            page reload or a UI restart can still show the last results.
        language: process-level language recorded in the per-case metrics.
        model_mode: ``live`` or ``offline``; offline runs report scripted token usage.
        model_label: display label of the configured model.
        metrics_grace_seconds: wait after a run finishes so the last batched server spans can
            arrive before the per-case token and latency figures are built.
    """

    def __init__(
        self,
        *,
        runs: RunManager,
        protocol_defaults: Mapping[str, Mapping[str, str]],
        server_url: str | None,
        server_auth_token: str | None,
        mie_url: str | None,
        runs_dir: Path | None = None,
        language: str = "",
        model_mode: str = "",
        model_label: str = "",
        metrics_grace_seconds: float = 0.0,
    ) -> None:
        self._runs = runs
        self._protocol_defaults = protocol_defaults
        self._server_url = (server_url or "").rstrip("/")
        self._server_auth_token = server_auth_token
        self._mie_url = (mie_url or "").rstrip("/")
        self._history_dir = (Path(runs_dir) / "tester") if runs_dir is not None else None
        self._language = language
        self._model_mode = model_mode
        self._model_label = model_label
        self._metrics_grace_seconds = max(0.0, metrics_grace_seconds)
        self._batches: dict[str, _Batch] = {}
        self._active_batch: str | None = None

    def create(
        self,
        cases: Sequence[TestCase],
        *,
        source_name: str = "",
    ) -> dict[str, object]:
        """Register one parsed workbook as a draft batch and return its snapshot."""
        batch_id = f"batch-{uuid.uuid4().hex[:12]}"
        results: list[dict[str, object]] = []
        for case in cases:
            if case.errors:
                status = CASE_INVALID
            elif not case.enabled:
                status = CASE_SKIPPED
            else:
                status = CASE_PENDING
            results.append(
                {
                    "caseId": case.case_id,
                    "status": status,
                    "runId": None,
                    "finalState": None,
                    "actualKind": None,
                    "actualRounds": None,
                    "durationMs": None,
                    "error": "；".join(case.errors) if case.errors else None,
                }
            )
        batch = _Batch(
            batch_id=batch_id,
            cases=list(cases),
            results=results,
            source_name=source_name,
        )
        self._batches[batch_id] = batch
        self._persist(batch)
        return self._snapshot(batch)

    def get(self, batch_id: str) -> dict[str, object] | None:
        """Return one batch snapshot, or ``None`` when unknown."""
        batch = self._batches.get(batch_id)
        if batch is None:
            batch = self._load(batch_id)
        return None if batch is None else self._snapshot(batch)

    def cases(self, batch_id: str) -> list[TestCase] | None:
        """Return one batch's parsed cases for the result export."""
        batch = self._batches.get(batch_id) or self._load(batch_id)
        return None if batch is None else batch.cases

    def list(self, *, limit: int = 20) -> list[dict[str, object]]:
        """Return the most recent persisted batches, newest first."""
        if self._history_dir is None or not self._history_dir.is_dir():
            batches = [self._snapshot(batch) for batch in self._batches.values()]
            return sorted(batches, key=lambda item: str(item.get("createdAt")), reverse=True)[:limit]
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
            if isinstance(document, Mapping):
                summaries.append(
                    {
                        "batchId": document.get("batchId"),
                        "status": document.get("status"),
                        "sourceName": document.get("sourceName", ""),
                        "createdAt": document.get("createdAt"),
                        "startedAt": document.get("startedAt"),
                        "finishedAt": document.get("finishedAt"),
                        "summary": document.get("summary", {}),
                    }
                )
        return summaries

    def retry(self, batch_id: str) -> dict[str, object]:
        """Create a new draft batch from the failed, errored or cancelled cases of one batch.

        Raises:
            KeyError: when the source batch is unknown.
            ValueError: when there is nothing to retry or another batch is running.
        """
        batch = self._batches.get(batch_id) or self._load(batch_id)
        if batch is None:
            raise KeyError(batch_id)
        if self._active_batch is not None:
            raise ValueError("已有测试批次正在执行，请等待完成或取消。")
        selected = [
            (case, result)
            for case, result in zip(batch.cases, batch.results, strict=False)
            if result.get("status") in (CASE_FAILED, CASE_ERROR, CASE_CANCELLED)
        ]
        if not selected:
            raise ValueError("没有失败、错误或取消的用例可以重跑。")
        cases = [case for case, _ in selected]
        results: list[dict[str, object]] = []
        for case, _ in selected:
            if case.errors:
                status = CASE_INVALID
            elif not case.enabled:
                status = CASE_SKIPPED
            else:
                status = CASE_PENDING
            results.append(
                {
                    "caseId": case.case_id,
                    "status": status,
                    "runId": None,
                    "finalState": None,
                    "actualKind": None,
                    "actualRounds": None,
                    "durationMs": None,
                    "error": "；".join(case.errors) if case.errors else None,
                }
            )
        retried = _Batch(
            batch_id=f"batch-{uuid.uuid4().hex[:12]}",
            cases=cases,
            results=results,
            source_name=batch.source_name,
        )
        self._batches[retried.batch_id] = retried
        self._persist(retried)
        return self._snapshot(retried)

    async def start(self, batch_id: str) -> dict[str, object]:
        """Start one draft batch.

        Raises:
            KeyError: when the batch is unknown.
            ValueError: when the batch already ran or another batch is active.
        """
        batch = self._batches.get(batch_id) or self._load(batch_id)
        if batch is None:
            raise KeyError(batch_id)
        if batch.status != BATCH_DRAFT:
            raise ValueError("该批次已经执行过。")
        if self._active_batch is not None:
            raise ValueError("已有测试批次正在执行，请等待完成或取消。")
        batch.status = BATCH_RUNNING
        batch.started_at = _now_iso()
        self._active_batch = batch_id
        self._persist(batch)
        batch.task = asyncio.create_task(self._run_batch(batch), name=f"tester-{batch_id}")
        await asyncio.sleep(0)
        return self._snapshot(batch)

    async def cancel(self, batch_id: str) -> bool:
        """Cancel one running batch and its active run; return whether it was running."""
        batch = self._batches.get(batch_id) or self._load(batch_id)
        if batch is None or batch.status != BATCH_RUNNING:
            return False
        batch.cancel_requested = True
        if batch.current_index is not None:
            result = batch.results[batch.current_index]
            run_id = result.get("runId")
            if isinstance(run_id, str) and run_id:
                await self._runs.cancel(run_id)
        if batch.task is not None and not batch.task.done():
            batch.task.cancel()
        self._persist(batch)
        return True

    async def shutdown(self) -> None:
        """Cancel every running batch (process shutdown)."""
        for batch_id, batch in self._batches.items():
            if batch.status == BATCH_RUNNING:
                await self.cancel(batch_id)

    async def _run_batch(self, batch: _Batch) -> None:
        """Execute the runnable cases sequentially, updating each result in place."""
        try:
            for index, case in enumerate(batch.cases):
                if batch.cancel_requested:
                    break
                if batch.results[index]["status"] != CASE_PENDING:
                    continue
                batch.current_index = index
                await self._run_case(batch, index, case)
        except asyncio.CancelledError:
            self._mark_pending_cancelled(batch)
            batch.status = BATCH_CANCELLED
            batch.finished_at = _now_iso()
            self._active_batch = None
            raise
        else:
            if batch.cancel_requested:
                self._mark_pending_cancelled(batch)
                batch.status = BATCH_CANCELLED
            else:
                pending = any(result.get("status") in (CASE_PENDING, CASE_RUNNING) for result in batch.results)
                batch.status = BATCH_RUNNING if pending else BATCH_FINISHED
            batch.finished_at = _now_iso()
            self._active_batch = None
        finally:
            batch.current_index = None
            self._persist(batch)

    def _mark_pending_cancelled(self, batch: _Batch) -> None:
        """Mark queued and interrupted cases as cancelled, leaving completed results untouched."""
        for result in batch.results:
            if result.get("status") in (CASE_PENDING, CASE_RUNNING):
                result["status"] = CASE_CANCELLED
                result["error"] = "批次已取消。"

    async def _run_case(self, batch: _Batch, index: int, case: TestCase) -> None:
        """Run one case, handle its approval gate, and record the verdict."""
        result = batch.results[index]
        result["status"] = CASE_RUNNING
        result["startedAt"] = _now_iso()
        started = time.monotonic()
        defaults = self._protocol_defaults.get(case.protocol, {})
        transport = defaults.get("transport", "sse")
        discovery = defaults.get("discovery", "direct")
        run: RunState | None = None
        approval_task: asyncio.Task[None] | None = None
        done = asyncio.Event()
        try:
            run = await self._runs.start(
                case.scenario,
                MODE_AUTO,
                transport,
                discovery,
                case.user_request or None,
                protocol=case.protocol,
                conversation_id=f"tester-{batch.batch_id}-{index}",
                user_id="tester",
                idempotency_key=f"tester-{batch.batch_id}-{index}",
            )
            result["runId"] = run.run_id
            result["links"] = {
                "transcript": f"/api/runs/{run.run_id}/transcript.json",
                "wire": f"/api/runs/{run.run_id}/wire",
                "trace": f"/api/runs/{run.run_id}/trace.json",
            }
            self._persist(batch)
            approval_task = asyncio.create_task(
                self._answer_approval_gate(case, run, done),
                name=f"tester-approval-{run.run_id}",
            )
            timed_out = await self._wait_for_run(run, case.timeout_seconds)
            if timed_out:
                await self._runs.cancel(run.run_id)
                await self._wait_for_run(run, _CANCEL_GRACE_SECONDS)
                result["status"] = CASE_ERROR
                result["error"] = f"用例超时（{case.timeout_seconds:g}s），已取消。"
            elif batch.cancel_requested:
                result["status"] = CASE_CANCELLED
                result["error"] = "批次已取消。"
            else:
                self._record_verdict(result, case, run)
        except Exception as exc:  # noqa: BLE001 - one bad case must not stop the batch
            _LOGGER.warning("tester case %s failed", case.case_id, exc_info=True)
            result["status"] = CASE_ERROR
            result["error"] = str(exc)
        finally:
            done.set()
            if approval_task is not None:
                approval_task.cancel()
                await asyncio.gather(approval_task, return_exceptions=True)
            result["durationMs"] = int(round((time.monotonic() - started) * 1000))
            result["finishedAt"] = _now_iso()
            if run is not None and not batch.cancel_requested:
                await self._collect_metrics(result, run)
            self._persist(batch)

    async def _collect_metrics(self, result: dict[str, object], run: RunState) -> None:
        """Attach the run's token usage and latency breakdown to one case result."""
        if self._metrics_grace_seconds > 0:
            # The server trace emitter batches spans; wait briefly so its final LLM spans are
            # included in the token totals.
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
        except Exception:  # noqa: BLE001 - report data must never fail the case
            _LOGGER.warning("tester metrics failed for run %s", run.run_id, exc_info=True)
            return
        tokens = metrics.get("tokens")
        latency = metrics.get("latency")
        result["metrics"] = {
            "tokens": dict(tokens) if isinstance(tokens, Mapping) else {},
            "latency": dict(latency) if isinstance(latency, Mapping) else {},
            "modelMode": self._model_mode,
            "modelLabel": self._model_label,
        }

    async def _wait_for_run(self, run: RunState, timeout_seconds: float) -> bool:
        """Wait until a run finishes; return whether the budget expired."""
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while not run.finished():
            if time.monotonic() >= deadline:
                return True
            await asyncio.sleep(_POLL_SECONDS)
        return False

    def _record_verdict(
        self,
        result: dict[str, object],
        case: TestCase,
        run: RunState,
    ) -> None:
        """Fill the actual values and the pass/fail verdict of one finished run."""
        final_state = run.result.final_state if run.result is not None else None
        actual_kind, actual_rounds = _actual_negotiation(run)
        result["finalState"] = final_state
        result["actualKind"] = actual_kind
        result["actualRounds"] = actual_rounds
        if run.status == STATUS_FAILED:
            result["status"] = CASE_FAILED
            result["error"] = run.error or "运行失败。"
            return
        failure = _expectation_failure(case, run)
        if failure is not None:
            result["status"] = CASE_FAILED
            result["error"] = failure
            return
        result["status"] = CASE_PASSED
        result["error"] = None

    async def _answer_approval_gate(
        self,
        case: TestCase,
        run: RunState,
        done: asyncio.Event,
    ) -> None:
        """Answer every approval gate of the case according to the ``approval`` column.

        ``approve`` (the default when the cell is empty) and ``reject`` answer a paused gate, so an
        unattended batch does not wait for the approval window. ``auto`` is the explicit opt-out:
        the runner leaves the gate to a human or to its timeout.
        """
        if case.approval == APPROVAL_AUTO:
            return
        approved = case.approval == APPROVAL_APPROVE
        watchers: list[asyncio.Task[None]] = []
        if run.protocol == PROTOCOL_A2A_T and self._server_url:
            watchers.append(asyncio.create_task(self._watch_server_gate(run, approved, done)))
        if run.protocol == PROTOCOL_PURE and self._mie_url:
            watchers.append(asyncio.create_task(self._watch_mie_gate(approved, done)))
        if watchers:
            await asyncio.gather(*watchers, return_exceptions=True)

    async def _watch_server_gate(
        self,
        run: RunState,
        approved: bool,
        done: asyncio.Event,
    ) -> None:
        """Forward the tester's decision each time the A2A-T server asks for approval."""
        snapshot, queue = run.subscribe()
        handled: set[str] = set()
        headers = {"authorization": f"Bearer {self._server_auth_token}"} if self._server_auth_token else {}
        try:
            for message in snapshot:
                await self._handle_server_gate_message(message, run, approved, handled, headers)
            while not done.is_set():
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=0.5)
                except TimeoutError:
                    continue
                await self._handle_server_gate_message(message, run, approved, handled, headers)
        finally:
            run.unsubscribe(queue)

    async def _handle_server_gate_message(
        self,
        message: dict[str, object],
        run: RunState,
        approved: bool,
        handled: set[str],
        headers: Mapping[str, str],
    ) -> None:
        """Post one decision when a new server approval request frame arrives."""
        if message.get("type") != "trace":
            return
        span = message.get("span")
        if not isinstance(span, Mapping):
            return
        detail = span.get("detail")
        if not isinstance(detail, Mapping):
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

    async def _watch_mie_gate(self, approved: bool, done: asyncio.Event) -> None:
        """Answer standard-A2A MIE approval requests while the case runs."""
        handled: set[str] = set()
        try:
            async with httpx.AsyncClient(timeout=None, trust_env=False) as client:
                async with client.stream("GET", f"{self._mie_url}/approvals/events") as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if done.is_set():
                            return
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

    def _persist(self, batch: _Batch) -> None:
        """Write one batch below ``runs/tester`` so a reload can show it again."""
        if self._history_dir is None:
            return
        try:
            self._history_dir.mkdir(parents=True, exist_ok=True)
            document = self._snapshot(batch)
            target = self._history_dir / f"{batch.batch_id}.json"
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(document, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(target)
        except OSError as exc:
            _LOGGER.warning("tester batch %s could not be persisted: %s", batch.batch_id, exc)

    def _load(self, batch_id: str) -> _Batch | None:
        """Load one persisted batch, marking a run left by a previous process as cancelled."""
        if self._history_dir is None:
            return None
        path = self._history_dir / f"{batch_id}.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(document, Mapping):
            return None
        raw_cases = document.get("cases")
        raw_results = document.get("results")
        if not isinstance(raw_cases, list) or not isinstance(raw_results, list):
            return None
        cases = [
            case
            for case in (_case_from_dict(item) for item in raw_cases if isinstance(item, Mapping))
            if case is not None
        ]
        results = [dict(item) for item in raw_results if isinstance(item, Mapping)]
        if len(results) != len(cases):
            return None
        batch = _Batch(
            batch_id=str(document.get("batchId") or batch_id),
            cases=cases,
            results=results,
            status=str(document.get("status") or BATCH_DRAFT),
            source_name=str(document.get("sourceName") or ""),
            created_at=str(document.get("createdAt") or _now_iso()),
            started_at=document.get("startedAt") if isinstance(document.get("startedAt"), str) else None,
            finished_at=document.get("finishedAt") if isinstance(document.get("finishedAt"), str) else None,
            error=document.get("error") if isinstance(document.get("error"), str) else None,
        )
        if batch.status in (BATCH_RUNNING, BATCH_DRAFT):
            # A draft needs no recovery; a running batch lost its task with the previous process.
            if batch.status == BATCH_RUNNING:
                for result in batch.results:
                    if result.get("status") in (CASE_PENDING, CASE_RUNNING):
                        result["status"] = CASE_CANCELLED
                        result["error"] = "UI 进程已重启，批次未执行完。"
                batch.status = BATCH_CANCELLED
                batch.finished_at = _now_iso()
        self._batches[batch_id] = batch
        return batch

    def _snapshot(self, batch: _Batch) -> dict[str, object]:
        """Return the JSON document of one batch."""
        cases = [case.to_dict() for case in batch.cases]
        return {
            "batchId": batch.batch_id,
            "status": batch.status,
            "sourceName": batch.source_name,
            "createdAt": batch.created_at,
            "startedAt": batch.started_at,
            "finishedAt": batch.finished_at,
            "currentIndex": batch.current_index,
            "error": batch.error,
            "cases": cases,
            "results": copy.deepcopy(batch.results),
            "summary": _summary(cases, batch.results),
        }


def _summary(
    cases: Sequence[Mapping[str, object]],
    results: Sequence[Mapping[str, object]],
) -> dict[str, int]:
    """Count the states the tester page shows."""
    counts = {
        "total": len(cases),
        "runnable": sum(1 for case in cases if case.get("runnable") is True),
        "invalid": 0,
        "skipped": 0,
        "pending": 0,
        "running": 0,
        "passed": 0,
        "failed": 0,
        "error": 0,
        "cancelled": 0,
    }
    duration_ms = 0
    tokens_total = 0
    tokens_available = False
    for result in results:
        status = str(result.get("status") or "")
        if status in counts:
            counts[status] += 1
        duration = result.get("durationMs")
        if isinstance(duration, (int, float)):
            duration_ms += int(duration)
        metrics = result.get("metrics")
        tokens = metrics.get("tokens") if isinstance(metrics, Mapping) else None
        if isinstance(tokens, Mapping) and tokens.get("available") is True:
            tokens_available = True
            total = tokens.get("total")
            if isinstance(total, int):
                tokens_total += total
    counts["durationMs"] = duration_ms
    counts["tokens"] = tokens_total if tokens_available else 0
    counts["tokensAvailable"] = 1 if tokens_available else 0
    return counts


__all__ = [
    "BATCH_CANCELLED",
    "BATCH_DRAFT",
    "BATCH_FINISHED",
    "BATCH_RUNNING",
    "CASE_CANCELLED",
    "CASE_ERROR",
    "CASE_FAILED",
    "CASE_INVALID",
    "CASE_PASSED",
    "CASE_PENDING",
    "CASE_RUNNING",
    "CASE_SKIPPED",
    "TestBatchManager",
]
