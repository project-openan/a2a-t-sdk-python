"""Service layer of the acceptance review console.

The console says one thing the tester cannot: *does the stored evidence satisfy the corpus as it is
today, and has a human accepted it?* Evidence comes from the review store; the verdict is computed
on read with :func:`eval.osp.review.judge_protocol`, so a newly annotated expectation applies to
old batches without a re-run.

A protocol task has two independent axes and the page shows both:

* the **automatic verdict** — pass / fail / partial / not run / stale (the evidence was produced
  from a different input);
* the **human decision** — pending / accepted / defect / known issue / recheck, with the runIds it
  was based on. A decision is marked stale when the case fingerprint changes or newer evidence
  arrives, and the page asks for a re-confirmation instead of silently keeping it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from eval.osp.catalog import PROTOCOL_A2A, PROTOCOL_A2A_T, CaseCatalog, CatalogReloadError
from eval.osp.review import (
    DIMENSION_TRACE,
    VERDICT_FAIL,
    VERDICT_MISSING,
    VERDICT_NOT_APPLICABLE,
    VERDICT_NOT_EXPECTED,
    VERDICT_PARTIAL,
    VERDICT_PASS,
    VERDICT_RECORDED,
    ProtocolJudgement,
    case_signature,
    evidence_is_current_input,
    judge_protocol,
)
from eval.osp.tracecheck import (
    TraceApplicability,
    nms_kind,
    original_request,
    trace_applicability,
)
from langfuse_export.config import Config
from shared.config import SampleConfig

from demo.web.review.corpus import case_documents, scan_evidence
from demo.web.review.store import (
    CATEGORIES,
    DECISION_ACCEPTED,
    DECISION_DEFECT,
    DECISION_KNOWN_ISSUE,
    DECISION_PENDING,
    DECISIONS,
    DecisionKey,
    ReviewStore,
    now_iso,
)
from demo.web.review.traces import InvalidTraceIdError, TraceStore

_LOGGER = logging.getLogger("ran_energy_saving.review")

#: Protocol tasks every case carries, in display order.
PROTOCOLS: Final[tuple[str, ...]] = (PROTOCOL_A2A_T, PROTOCOL_A2A)

#: Status of a protocol task with no evidence in the window.
STATUS_NOT_RUN: Final[str] = "not_run"
#: Status of a task whose newest evidence was produced from a different operator request.
STATUS_STALE: Final[str] = "stale"

#: Statuses the protocol list can show (automatic verdicts plus the two evidence states).
STATUSES: Final[tuple[str, ...]] = (
    VERDICT_PASS,
    VERDICT_FAIL,
    VERDICT_PARTIAL,
    VERDICT_RECORDED,
    VERDICT_MISSING,
    VERDICT_NOT_EXPECTED,
    VERDICT_NOT_APPLICABLE,
    STATUS_STALE,
    STATUS_NOT_RUN,
)

#: Export states whose snapshot is complete enough to serve from cache without a request.
_EXPORTED_STATES: Final[frozenset[str]] = frozenset({"exported", "partial"})

#: Cooldown after a Langfuse 429. The public API admits 30 requests/minute; one minute is enough
#: for the bucket to refill and short enough that a reviewer can retry by hand.
FETCH_COOLDOWN_SECONDS: Final[float] = 60.0


class ReviewService:
    """Evidence projection, current-corpus judging and decision storage."""

    def __init__(
        self,
        *,
        store: ReviewStore,
        catalog: CaseCatalog,
        runs_dir: Path,
        client_db_path: Path | None,
        trace_store: TraceStore | None = None,
        trace_config: Config | None = None,
        trace_fetch_enabled: bool = True,
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._runs_dir = Path(runs_dir)
        self._client_db_path = client_db_path
        self._trace_store = trace_store if trace_store is not None else TraceStore(self._runs_dir)
        #: Langfuse credentials for the opt-in fetch; ``None`` means "not configured yet".
        self._trace_config = trace_config
        #: The standalone read-only instance leaves this off: it must not call Langfuse at all.
        self._trace_fetch_enabled = trace_fetch_enabled
        #: One fetch at a time. The public API admits 30 requests/minute; a serial queue plus the
        #: permanent on-disk cache keeps a page that opens several cases from bursting through it.
        self._fetch_lock = threading.Lock()
        #: Wall-clock moment before which no fetch is attempted after a 429.
        self._fetch_cooldown_until = 0.0
        self._last_scan: dict[str, object] | None = None

    @property
    def store(self) -> ReviewStore:
        """Return the review store (routes export from it)."""
        return self._store

    @property
    def catalog(self) -> CaseCatalog:
        """Return the case catalog (routes report its version)."""
        return self._catalog

    @property
    def last_scan(self) -> dict[str, object] | None:
        """Return the last scan summary, for the report header."""
        return self._last_scan

    def reload_catalog(self) -> None:
        """Re-read the corpus and the live annotation overlay before a scan."""
        try:
            self._catalog.reload()
        except CatalogReloadError as exc:
            _LOGGER.warning("catalog reload before a review scan failed: %s", exc)

    def scan(self, *, days: float = 2.0, max_batches: int = 500) -> dict[str, object]:
        """Collect the evidence of the recent tester batches into the store."""
        self.reload_catalog()
        cases = case_documents(self._catalog.documents())
        scan = scan_evidence(
            runs_dir=self._runs_dir,
            client_db_path=self._client_db_path,
            cases=cases,
            days=days,
            max_batches=max_batches,
        )
        inserted = self._store.ingest(scan.observations)
        attached = self._attach_traces(scan.observations)
        summary: dict[str, object] = {
            "inserted": inserted,
            "observations": len(scan.observations),
            "batches": len(scan.batches),
            "traces": attached,
            "windowStart": scan.window_start,
            "windowEnd": scan.window_end,
            "skipped": dict(scan.skipped),
            "truncated": scan.truncated,
        }
        self._last_scan = summary
        return summary

    def _attach_traces(self, observations: Sequence[Mapping[str, object]]) -> int:
        """Attach the trace snapshot already on disk to every judgeable observation.

        No network: ``cached_payload`` without a config only reads the snapshot root. A scan must
        never pull every trace of a window from Langfuse — that is the page's explicit action.
        """
        groups: dict[str, tuple[dict[str, object], list[Mapping[str, object]]]] = {}
        for observation in observations:
            target = _trace_target(observation)
            if target is None:
                continue
            trace_id = str(target["traceId"])
            entry = groups.get(trace_id)
            if entry is None:
                groups[trace_id] = (target, [observation])
            else:
                entry[1].append(observation)
        attached = 0
        stamp = now_iso()
        for trace_id, (target, rows) in groups.items():
            try:
                payload = self._trace_store.cached_payload(
                    trace_id,
                    run_id=str(target["runId"]),
                    nms_kind=_row_nms_kind(rows[0]),
                    local_points=_string_list(target.get("localPoints")),
                    local_known=_string_list(target.get("localKnown")),
                )
            except (InvalidTraceIdError, OSError):
                continue
            if str(payload.get("exportState") or "") not in {"exported", "partial"}:
                continue
            for observation in rows:
                attached += self._store.set_trace(
                    case_id=_as_int(observation.get("caseId")),
                    protocol=str(observation.get("protocol") or ""),
                    batch_id=str(observation.get("batchId") or ""),
                    source_round=_as_int(observation.get("sourceRound"), 1),
                    run_id=str(observation.get("runId") or ""),
                    trace=payload,
                    fetched_at=stamp,
                )
        return attached

    def fetch_trace(
        self,
        case_id: int,
        protocol: str,
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
        batch_id: str | None = None,
        source_round: int | None = None,
        force: bool = False,
    ) -> dict[str, object]:
        """Fetch one round's trace snapshot (explicit opt-in) and attach it.

        The snapshot is a **permanent cache**: without ``force`` an existing export is reused
        without touching Langfuse. Opening a case therefore costs at most one request per run, and
        the reviewer's "重新抓取" is the only way to spend quota again.

        Fetches are serialized because the public API admits 30 requests/minute. A 429 puts the
        service into a short cooldown instead of retrying; the page shows the remaining seconds.
        """
        if not self._trace_fetch_enabled:
            raise ValueError("该实例是只读模式，未启用轨迹抓取（启动时加 --fetch-traces）。")
        case = self._catalog.get(case_id)
        if case is None:
            raise ValueError("未知用例。")
        if protocol not in PROTOCOLS:
            raise ValueError(f"protocol 只能是 {', '.join(PROTOCOLS)}。")
        rows = self._store.observations(case_id, protocol)
        if not rows:
            raise ValueError("该协议任务没有证据。")
        observation = _select_round(
            rows,
            trace_id=trace_id,
            run_id=run_id,
            batch_id=batch_id,
            source_round=source_round,
        )
        if observation is None:
            raise ValueError("找不到对应的运行证据，请先重新扫描。")
        target = _trace_target(observation)
        if target is None:
            raise ValueError(_row_applicability(observation).text or "该运行没有可抓取的轨迹。")
        resolved_trace = str(target["traceId"])
        if not force:
            cached = self._trace_store.load(resolved_trace)
            if cached.export_state in _EXPORTED_STATES:
                return self._attach_trace(observation, target)
        remaining = self._cooldown_remaining()
        if remaining > 0:
            raise ValueError(f"Langfuse 限流冷却中，请 {remaining} 秒后重试。")
        with self._fetch_lock:
            # Another tab may have fetched this trace while this request waited for the lock.
            if not force:
                cached = self._trace_store.load(resolved_trace)
                if cached.export_state in _EXPORTED_STATES:
                    return self._attach_trace(observation, target)
            config = self._resolved_trace_config()
            result = self._trace_store.fetch(resolved_trace, config, run_id=str(target["runId"]))
            if str(result.get("exportState") or "") != "exported":
                error = str(result.get("error") or result.get("exportState") or "")
                if _is_rate_limited(error):
                    self._fetch_cooldown_until = time.monotonic() + FETCH_COOLDOWN_SECONDS
                    raise ValueError(f"Langfuse 限流（429），请 {int(FETCH_COOLDOWN_SECONDS)} 秒后重试。")
                raise ValueError(f"轨迹抓取失败：{error}")
            return self._attach_trace(observation, target)

    def _attach_trace(
        self,
        observation: Mapping[str, object],
        target: Mapping[str, object],
    ) -> dict[str, object]:
        """Attach one run's snapshot to its evidence row and return the payload."""
        payload = self._trace_store.cached_payload(
            str(target["traceId"]),
            run_id=str(target["runId"]),
            nms_kind=_row_nms_kind(observation),
            local_points=_string_list(target.get("localPoints")),
            local_known=_string_list(target.get("localKnown")),
        )
        stored = self._store.set_trace(
            case_id=_as_int(observation.get("caseId")),
            protocol=str(observation.get("protocol") or ""),
            batch_id=str(observation.get("batchId") or ""),
            source_round=_as_int(observation.get("sourceRound"), 1),
            run_id=str(observation.get("runId") or ""),
            trace=payload,
            fetched_at=now_iso(),
        )
        if not stored:
            raise ValueError("该证据行已不在库中，请重新采集。")
        return payload

    def _cooldown_remaining(self) -> int:
        """Return the whole seconds left of the 429 cooldown, or ``0``."""
        remaining = self._fetch_cooldown_until - time.monotonic()
        return max(0, int(remaining + 0.999)) if remaining > 0 else 0

    def _resolved_trace_config(self) -> Config:
        """Return the Langfuse credentials the sample itself reports with.

        The sample does not use ``langfuse_export``'s own ``LANGFUSE_HOST``: it reads
        ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` / ``LANGFUSE_BASE_URL`` through
        :func:`shared.config.load_config`, and ``LANGFUSE_BASE_URL`` is what points at the local
        proxy that fronts ``us.cloud.langfuse.com``. Building the fetch config from the same values
        keeps the console and the run export talking to the same endpoint.
        """
        if self._trace_config is not None:
            return self._trace_config
        sample = self._sample_config()
        if not sample.langfuse_configured:
            raise ValueError(
                "未配置 Langfuse 凭据（LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY），"
                "或 A2AT_SAMPLE_LANGFUSE_ENABLED=0。"
            )
        self._trace_config = Config(
            host=sample.langfuse_base_url.rstrip("/"),
            public_key=sample.langfuse_public_key,
            secret_key=sample.langfuse_secret_key,
            # The sample's precedence is process env > .env > default; ``load_config`` already
            # merged it, so the origin only matters for diagnostics.
            host_source="env",
        )
        return self._trace_config

    def _sample_config(self) -> SampleConfig:
        """Load the sample configuration from the runs directory, then its parent, then cwd."""
        from shared.config import load_config as load_sample_config

        for candidate in (self._runs_dir / ".env", self._runs_dir.parent / ".env"):
            if candidate.is_file():
                try:
                    return load_sample_config(env_path=candidate)
                except ValueError as exc:
                    raise ValueError(f"无法读取 sample 配置 {candidate}：{exc}") from exc
        return load_sample_config()

    def trace_configured(self) -> bool:
        """Return whether this instance can resolve Langfuse credentials."""
        if self._trace_config is not None:
            return True
        try:
            self._resolved_trace_config()
        except ValueError:
            return False
        return True

    def cases(self) -> list[dict[str, object]]:
        """Return every case with its two protocol tasks, verdicts and decisions."""
        latest = self._store.latest_by_target()
        decisions = self._store.decisions()
        rows: list[dict[str, object]] = []
        for case in self._catalog.documents():
            case_id = int(case["id"])
            rows.append(
                {
                    "caseId": case_id,
                    "caseName": str(case.get("name") or ""),
                    "operation": case.get("operation"),
                    "dimension": case.get("dimension"),
                    "tags": list(case.get("tags") or ()),
                    "blocked": _is_blocked(case),
                    "negotiation": _negotiation_flags(case),
                    "protocols": [
                        self._protocol_row(
                            case,
                            case_id,
                            protocol,
                            latest.get((case_id, protocol)),
                            decisions.get((case_id, protocol)),
                        )
                        for protocol in PROTOCOLS
                    ],
                }
            )
        return rows

    def export_cases(self) -> list[dict[str, object]]:
        """Return the case projection with the latest round's detail attached.

        The list endpoint must stay lean, so the export — which needs the full Task-T texts and
        the trace comparison — re-judges the newest round of every target and attaches it here.
        """
        rows = self.cases()
        for row in rows:
            protocols = row.get("protocols")
            for task in protocols if isinstance(protocols, list) else ():
                if not isinstance(task, dict) or task.get("status") == STATUS_NOT_RUN:
                    continue
                detail = self.detail(_as_int(row.get("caseId")), str(task.get("protocol") or ""))
                rounds = detail.get("rounds")
                if not isinstance(rounds, list) or not rounds:
                    continue
                latest = rounds[0]
                judgement = latest.get("judgement")
                task["exportDetail"] = judgement.get("detail") if isinstance(judgement, Mapping) else {}
                task["exportRequests"] = latest.get("requests") or {}
        return rows

    def detail(self, case_id: int, protocol: str) -> dict[str, object]:
        """Return every round of one protocol task, judged, plus its decision."""
        case = self._catalog.get(case_id)
        if case is None:
            raise ValueError("未知用例。")
        if protocol not in PROTOCOLS:
            raise ValueError(f"protocol 只能是 {', '.join(PROTOCOLS)}。")
        observations = self._store.observations(case_id, protocol)
        rounds = [self._round_row(case, protocol, observation) for observation in observations]
        decision = self._decision_block(
            case,
            protocol,
            self._store.decision(DecisionKey(case_id, protocol)),
            rounds,
        )
        return {
            "case": _case_summary(case),
            "protocol": protocol,
            "rounds": rounds,
            "decision": decision,
            "corpusVersion": self._catalog.version,
        }

    def batches(self) -> list[dict[str, object]]:
        """Return the ingested batches, newest first, without judging them."""
        return self._store.batches()

    def batch_rows(self, batch_id: str, round: int | None = None) -> dict[str, object]:
        """Judge every case × protocol against one batch, optionally narrowed to one round.

        Rows come from the corpus, not from the evidence: a case the batch never ran appears as
        ``not_run``, so the coverage of the round stays visible instead of reading as a pass.
        """
        observations = self._store.observations_for_batch(batch_id, round)
        if not observations:
            raise ValueError("未知批次，或该轮次没有证据。")
        by_target: dict[tuple[int, str], dict[str, object]] = {}
        attempts: dict[tuple[int, str], int] = {}
        for observation in observations:
            key = (_as_int(observation.get("caseId")), str(observation.get("protocol") or ""))
            attempts[key] = attempts.get(key, 0) + 1
            by_target.setdefault(key, observation)
        decisions = self._store.decisions()
        scope: dict[str, object] = {"batchId": batch_id, "round": round}
        observed = 0
        rows: list[dict[str, object]] = []
        documents = self._catalog.documents()
        for case in documents:
            case_id = int(case.get("id") or 0)
            tasks: list[dict[str, object]] = []
            for protocol in PROTOCOLS:
                observation = by_target.get((case_id, protocol))
                if observation is None:
                    tasks.append(
                        self._protocol_row(
                            case,
                            case_id,
                            protocol,
                            None,
                            decisions.get((case_id, protocol)),
                            scope=scope,
                        )
                    )
                    continue
                observed += 1
                tasks.append(
                    self._protocol_row(
                        case,
                        case_id,
                        protocol,
                        {**observation, "observations": attempts.get((case_id, protocol), 1)},
                        decisions.get((case_id, protocol)),
                        scope=scope,
                    )
                )
            rows.append(
                {
                    "caseId": case_id,
                    "caseName": str(case.get("name") or ""),
                    "operation": case.get("operation"),
                    "dimension": case.get("dimension"),
                    "tags": list(case.get("tags") or ()),
                    "blocked": _is_blocked(case),
                    "negotiation": _negotiation_flags(case),
                    "protocols": tasks,
                }
            )
        summary = self._batch_summary(batch_id)
        progress = self.progress(rows)
        return {
            "batch": {**summary, "round": round},
            "cases": rows,
            "coverage": {
                "observed": observed,
                "targets": len(documents) * len(PROTOCOLS),
                "cases": summary.get("cases"),
            },
            **progress,
            "corpusVersion": self._catalog.version,
        }

    def _batch_summary(self, batch_id: str) -> dict[str, object]:
        """Return one batch's metadata, or raise when it has no evidence."""
        for batch in self._store.batches():
            if batch.get("batchId") == batch_id:
                return batch
        raise ValueError("未知批次。")

    def progress(self, rows: Sequence[Mapping[str, object]] | None = None) -> dict[str, object]:
        """Return the protocol-task and decision counters for the header."""
        cases = rows if rows is not None else self.cases()
        statuses: dict[str, int] = {status: 0 for status in STATUSES}
        decisions: dict[str, int] = {status: 0 for status in DECISIONS}
        for case in cases:
            protocols = case.get("protocols")
            if not isinstance(protocols, list):
                continue
            for task in protocols:
                if not isinstance(task, Mapping):
                    continue
                status = str(task.get("status") or STATUS_NOT_RUN)
                statuses[status] = statuses.get(status, 0) + 1
                decision = task.get("decision")
                decided = (
                    str(decision.get("status") or DECISION_PENDING)
                    if isinstance(decision, Mapping)
                    else DECISION_PENDING
                )
                decisions[decided] = decisions.get(decided, 0) + 1
        return {
            "statuses": statuses,
            "decisions": decisions,
            "targets": sum(statuses.values()),
            "caseCount": len(cases),
        }

    def set_decision(
        self,
        case_id: int,
        protocol: str,
        *,
        status: str,
        category: str = "",
        note: str = "",
        reviewer: str = "",
        actor: str = "",
    ) -> dict[str, object]:
        """Record one human decision, rejecting an unexplained override of the verdict."""
        case = self._catalog.get(case_id)
        if case is None:
            raise ValueError("未知用例。")
        if protocol not in PROTOCOLS:
            raise ValueError(f"protocol 只能是 {', '.join(PROTOCOLS)}。")
        if status not in DECISIONS:
            raise ValueError(f"status 只能是 {', '.join(DECISIONS)}。")
        if status == DECISION_DEFECT and category not in CATEGORIES:
            raise ValueError(f"缺陷归属只能是 {', '.join(CATEGORIES)}。")
        if status != DECISION_DEFECT and category:
            raise ValueError("只有缺陷结论可以填写归属。")
        note = note.strip()
        if status in (DECISION_DEFECT, DECISION_KNOWN_ISSUE) and not note:
            raise ValueError("缺陷与已知问题必须填写备注。")
        key = DecisionKey(case_id, protocol)
        evidence_rows = self._store.observations(case_id, protocol)
        rounds = [self._round_row(case, protocol, row) for row in evidence_rows]
        auto = _auto_status(rounds)
        if status == DECISION_ACCEPTED and auto != VERDICT_PASS and not note:
            raise ValueError("自动判定不是通过，确认通过时必须填写理由。")
        if status == DECISION_DEFECT and auto == VERDICT_PASS and not note:
            raise ValueError("自动判定通过，判为缺陷时必须填写理由。")
        evidence = {
            "runIds": [row.get("runId") for row in rounds if row.get("runId")],
            "batchIds": [row.get("batchId") for row in rounds],
            "models": sorted({str(row.get("model")) for row in rounds if row.get("model")}),
            "modes": sorted({str(row.get("decisionMode")) for row in rounds}),
            "nms": any(bool(row.get("nms")) for row in rounds),
            "autoStatus": auto,
            "observedAt": str(evidence_rows[0].get("observedAt") or "") if evidence_rows else "",
        }
        self._store.set_decision(
            key,
            status=status,
            category=category,
            note=note,
            reviewer=reviewer,
            evidence=evidence,
            case_signature=case_signature(case),
            corpus_version=self._catalog.version,
            actor=actor,
        )
        return self.detail(case_id, protocol)

    # ------------------------------------------------------------------
    # Projection helpers
    # ------------------------------------------------------------------

    def _protocol_row(
        self,
        case: Mapping[str, Any],
        case_id: int,
        protocol: str,
        observation: Mapping[str, object] | None,
        decision: Mapping[str, object] | None,
        *,
        scope: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Build one protocol task row for the list and the header counters.

        ``scope`` names the batch/round the row is projected from; the decision block then reports
        whether the stored decision was made from that evidence.
        """
        if observation is None:
            return {
                "protocol": protocol,
                "status": STATUS_NOT_RUN,
                "dimensions": [],
                "observations": 0,
                "observedAt": "",
                "model": None,
                "decisionMode": "",
                "nms": False,
                "inputChanged": False,
                "decision": self._decision_block(case, protocol, decision, [], scope=scope),
            }
        evidence_map = _judged_evidence(observation)
        input_changed = not evidence_is_current_input(case, protocol, evidence_map)
        judgement = judge_protocol(case, protocol, evidence_map)
        status = STATUS_STALE if input_changed else judgement.status
        trace_reasons: list[str] = []
        trace_dimension = judgement.dimension(DIMENSION_TRACE)
        if trace_dimension is not None:
            # One round can fail for several reasons at once; the report counts each of them.
            trace_reasons = list(
                dict.fromkeys(str(diff.get("reason") or "") for diff in trace_dimension.diffs if diff.get("reason"))
            )
        return {
            "protocol": protocol,
            "status": status,
            "dimensions": [
                {"dimension": row.dimension, "status": row.status, "reason": row.reason} for row in judgement.dimensions
            ],
            "failures": [
                {
                    "dimension": row.dimension,
                    "status": row.status,
                    "reason": row.reason,
                    "diffs": [dict(diff) for diff in row.diffs][:20],
                }
                for row in judgement.dimensions
                if row.status in (VERDICT_FAIL, VERDICT_PARTIAL, VERDICT_MISSING, VERDICT_RECORDED)
            ],
            "observations": _as_int(observation.get("observations"), 1),
            "observedAt": observation.get("observedAt"),
            "model": observation.get("model"),
            "decisionMode": observation.get("decisionMode"),
            "nms": bool(observation.get("nms")),
            "inputChanged": input_changed,
            "traceReason": trace_reasons[0] if trace_reasons else "",
            "traceReasons": trace_reasons,
            "decision": self._decision_block(
                case,
                protocol,
                decision,
                [{"runId": observation.get("runId"), "batchId": observation.get("batchId")}],
                scope=scope,
            ),
        }

    def _round_row(
        self,
        case: Mapping[str, Any],
        protocol: str,
        observation: Mapping[str, object],
    ) -> dict[str, object]:
        """Build one judged evidence round for the detail payload."""
        evidence_map = _judged_evidence(observation)
        input_changed = not evidence_is_current_input(case, protocol, evidence_map)
        judgement: ProtocolJudgement = judge_protocol(case, protocol, evidence_map)
        detail = judgement.to_dict()
        _mark_fetch_state(detail, self._trace_fetch_enabled)
        self._mark_snapshot_state(detail, observation)
        return {
            "id": observation.get("id"),
            "batchId": observation.get("batchId"),
            "sourceRound": observation.get("sourceRound"),
            "runId": observation.get("runId"),
            "traceId": observation.get("traceId"),
            "observedAt": observation.get("observedAt"),
            "model": observation.get("model"),
            "decisionMode": observation.get("decisionMode"),
            "nms": bool(observation.get("nms")),
            "nmsSim": bool(observation.get("nmsSim")),
            "discovery": observation.get("discovery"),
            "status": observation.get("status"),
            "inputChanged": input_changed,
            "judgement": detail,
            "requests": _request_sources(case, evidence_map),
        }

    def _mark_snapshot_state(
        self,
        judgement: dict[str, Any],
        observation: Mapping[str, object],
    ) -> None:
        """Reflect a failed or on-disk snapshot that the store has not attached yet.

        A failed fetch leaves only a ``failure.json`` marker, so the stored evidence still has no
        trace. The page must say "上次抓取失败" and offer a retry instead of pretending the trace
        was never requested.
        """
        block = judgement.get("detail")
        trace = block.get("trace") if isinstance(block, dict) else None
        if not isinstance(trace, dict) or trace.get("present"):
            return
        trace_id = str(observation.get("traceId") or "")
        if not trace_id:
            return
        try:
            snapshot = self._trace_store.load(trace_id)
        except (InvalidTraceIdError, OSError):
            return
        state = str(snapshot.export_state or "")
        if state in _EXPORTED_STATES:
            trace["exportState"] = state
            trace["blockers"] = []
        elif state == "failed":
            trace["exportState"] = "failed"
            trace["blockers"] = [
                {
                    "code": "trace_snapshot_failed",
                    "text": "上次抓取失败，请重试",
                    "action": "retry",
                }
            ]

    def _decision_block(
        self,
        case: Mapping[str, Any],
        protocol: str,
        decision: Mapping[str, object] | None,
        rounds: Sequence[Mapping[str, object]],
        *,
        scope: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Return the decision together with its staleness flags.

        With ``scope`` (the batch view) the block also reports whether the decision's evidence
        includes the scoped run, and stops claiming "new evidence": that flag compares against the
        newest run of the whole corpus, which is meaningless for one batch.
        """
        if decision is None:
            return {
                "status": DECISION_PENDING,
                "category": "",
                "note": "",
                "reviewer": "",
                "confirmedAt": "",
                "evidence": {},
                "stale": False,
                "staleReason": "",
                "newEvidence": False,
                "onThisRound": None,
            }
        stale_reason = ""
        if str(decision.get("caseSignature") or "") != case_signature(case):
            stale_reason = "用例或期望已变更，结论需复核"
        evidence = decision.get("evidence")
        evidence_map = evidence if isinstance(evidence, Mapping) else {}
        run_ids = evidence_map.get("runIds")
        known = {str(value) for value in run_ids} if isinstance(run_ids, list) else set()
        latest = rounds[0].get("runId") if rounds else None
        newest = str(latest or "")
        on_this_round: bool | None = None
        if scope is None:
            new_evidence = bool(newest) and newest not in known
        else:
            on_this_round = bool(newest) and newest in known
            new_evidence = False
        return {
            "status": str(decision.get("status") or DECISION_PENDING),
            "category": str(decision.get("category") or ""),
            "note": str(decision.get("note") or ""),
            "reviewer": str(decision.get("reviewer") or ""),
            "confirmedAt": str(decision.get("confirmedAt") or ""),
            "evidence": dict(evidence_map),
            "stale": bool(stale_reason),
            "staleReason": stale_reason,
            "newEvidence": new_evidence,
            "onThisRound": on_this_round,
        }


def _as_int(value: object, default: int = 0) -> int:
    """Return ``value`` as an integer, or ``default`` when it is not one."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return default


def _mark_fetch_state(detail: dict[str, Any], fetch_enabled: bool) -> None:
    """Attach the instance's fetch capability to the trace block of one judged round.

    ``judge_protocol`` is pure and cannot know whether this process may call Langfuse. The page
    needs that bit to decide whether the fetch button is enabled and whether the automatic load
    runs at all, so the service adds it here instead of duplicating the rule in JavaScript.
    """
    block = detail.get("detail")
    if not isinstance(block, dict):
        return
    trace = block.get("trace")
    if not isinstance(trace, dict):
        return
    applicability = trace.get("applicability")
    applicable = isinstance(applicability, Mapping) and applicability.get("applicable") == "yes"
    trace["fetchEnabled"] = fetch_enabled
    trace["canFetch"] = bool(fetch_enabled and applicable)


def _select_round(
    rows: Sequence[Mapping[str, object]],
    *,
    trace_id: str | None,
    run_id: str | None,
    batch_id: str | None,
    source_round: int | None,
) -> Mapping[str, object] | None:
    """Return the evidence row a fetch request names, defaulting to the newest one.

    The page always sends the trace id of the round the reviewer is looking at, so a multi-round
    batch can no longer fetch the newest round while showing an older card. Every selector must
    match an already-ingested row: the endpoint never fetches an arbitrary trace id.
    """
    for row in rows:
        if trace_id and str(row.get("traceId") or "") != trace_id:
            continue
        if run_id and str(row.get("runId") or "") != run_id:
            continue
        if batch_id and str(row.get("batchId") or "") != batch_id:
            continue
        if source_round is not None and _as_int(row.get("sourceRound"), 1) != source_round:
            continue
        return row
    return None


def _is_rate_limited(error: str) -> bool:
    """Return whether a fetch error is a Langfuse rate-limit response."""
    lowered = error.lower()
    return "429" in lowered or "rate limit" in lowered or "rate_limit" in lowered


def _trace_target(observation: Mapping[str, object]) -> dict[str, object] | None:
    """Return the trace lookup of one observation, or ``None`` when it is not judgeable.

    A run that dispatched and carries a Langfuse trace id has a trace to look up. The declared
    downstream is deliberately not checked here: batches from before the tester recorded
    ``nms``/``nmsSim`` are still real-machine runs, and refusing them would leave the console
    unable to fetch the very evidence the acceptance gate needs.
    """
    applicability = _row_applicability(observation)
    if not applicability.applicable:
        return None
    evidence = observation.get("evidence")
    evidence_map = evidence if isinstance(evidence, Mapping) else {}
    return {
        "runId": applicability.run_id,
        "traceId": applicability.trace_id,
        "localPoints": evidence_map.get("localPoints"),
        "localKnown": evidence_map.get("localKnown"),
    }


def _row_nms_kind(observation: Mapping[str, object]) -> str:
    """Return the display-only downstream identity of one stored evidence row."""
    declared = observation.get("nmsDeclared")
    simulated = observation.get("nmsSimDeclared")
    return nms_kind(
        declared if isinstance(declared, bool) else None,
        simulated if isinstance(simulated, bool) else None,
    )


def _row_applicability(observation: Mapping[str, object]) -> TraceApplicability:
    """Return the trace applicability of one stored evidence row."""
    return trace_applicability(
        observation.get("runId"),
        observation.get("traceId"),
        simulated=observation.get("nmsSimDeclared") is True,
    )


def _string_list(value: object) -> list[str] | None:
    """Return a list of strings, or ``None`` when the value is not a list."""
    if not isinstance(value, list):
        return None
    return [str(item) for item in value]


def _judged_evidence(observation: Mapping[str, object]) -> dict[str, object]:
    """Merge the row's trace snapshot and declared downstream into the stored evidence.

    The tester item carries neither: the trace lives in its own column and the declared downstream
    flags are batch properties. ``judge_protocol`` stays a pure function over one mapping, so the
    service is the only place that knows the store layout.
    """
    evidence = observation.get("evidence")
    merged = dict(evidence) if isinstance(evidence, Mapping) else {}
    merged["runId"] = observation.get("runId")
    # The trace id decides applicability now; the stored evidence carries the snapshot, but the
    # round row is the authority for identity (a rescanned batch may re-point the same run).
    merged["traceId"] = observation.get("traceId")
    trace = observation.get("trace")
    if trace is not None:
        merged["trace"] = trace
    merged["batch"] = {
        "nms": observation.get("nmsDeclared"),
        "nmsSim": observation.get("nmsSimDeclared"),
    }
    return merged


def _request_sources(case: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, object]:
    """Return the three requests a verdict can be about: original, blocked, dispatched.

    A blocked case dispatches its replacement request, so the run's frozen input is the follow-up,
    while the blocked original only survives in the interception record. The precedence is the one
    that never substitutes today's corpus for what the run actually read:

    * original — for a blocked case only the intercepted text, else the current corpus flagged
      ``corpus``; both the run's ``rawRequest`` and ``refs.userInput`` are the *follow-up* text
      there, so they must never fill the original column. For a non-blocked case: ``refs.userInput``
      (the frozen input) and then the corpus, again flagged;
    * dispatched — the run's ``rawRequest``, else the frozen case input.
    """
    refs = evidence.get("refs")
    refs_map = refs if isinstance(refs, Mapping) else {}
    intent_meta = evidence.get("intentMeta")
    meta_map = intent_meta if isinstance(intent_meta, Mapping) else {}
    interception = evidence.get("interception")
    blocked = interception if isinstance(interception, Mapping) else {}
    frozen_input = refs_map.get("userInput")
    raw_request = meta_map.get("rawRequest")
    intercepted = blocked.get("input")
    trace = evidence.get("trace")
    traced_original = original_request(trace) if isinstance(trace, Mapping) else ""
    # A blocked run dispatched its follow-up, so ``rawRequest`` and ``refs.userInput`` both hold
    # that replacement text: neither may stand in for the original the operator wrote.
    if isinstance(interception, Mapping) or _is_blocked(case):
        if intercepted:
            original, original_source = intercepted, "interception"
        elif traced_original:
            original, original_source = traced_original, "trace"
        else:
            # The corpus text is the current file, not what the run read: mark it as degraded.
            original, original_source = case.get("user_input"), "corpus"
    elif frozen_input:
        original, original_source = frozen_input, "refs"
    elif traced_original:
        original, original_source = traced_original, "trace"
    else:
        original, original_source = case.get("user_input"), "corpus"
    if raw_request:
        dispatched, dispatched_source = raw_request, "rawRequest"
    elif frozen_input:
        dispatched, dispatched_source = frozen_input, "refs"
    else:
        dispatched, dispatched_source = None, ""
    return {
        "original": original,
        "originalSource": original_source,
        "intercepted": intercepted,
        "followup": blocked.get("followupInput"),
        "dispatched": dispatched,
        "dispatchedSource": dispatched_source,
    }


def _auto_status(rounds: Sequence[Mapping[str, object]]) -> str:
    """Return the newest round's automatic verdict, or ``not_run``."""
    if not rounds:
        return STATUS_NOT_RUN
    first = rounds[0]
    judgement = first.get("judgement")
    if not isinstance(judgement, Mapping):
        return STATUS_NOT_RUN
    return str(judgement.get("status") or STATUS_NOT_RUN)


def _case_summary(case: Mapping[str, Any]) -> dict[str, object]:
    """Return the case metadata the page shows above the rounds."""
    a2a_t = _protocol_block(case, PROTOCOL_A2A_T)
    a2a = _protocol_block(case, PROTOCOL_A2A)
    followup = case.get("blocked_followup")
    followup_map = followup if isinstance(followup, Mapping) else {}
    return {
        "id": int(case["id"]),
        "name": str(case.get("name") or ""),
        "operation": case.get("operation"),
        "dimension": case.get("dimension"),
        "userInput": case.get("user_input"),
        # The replacement request a blocked case dispatches. The detail shows it next to the
        # original so a reviewer can see what changed without opening the case YAML.
        "followupInput": followup_map.get("input"),
        "blocked": _is_blocked(case),
        "validate": a2a_t.get("validate"),
        "a2aTRounds": _rounds(a2a_t),
        "a2aRounds": _rounds(a2a),
        "finalState": a2a_t.get("final_state"),
        "hasSlotErrors": a2a_t.get("slot_errors") is not None,
        "hasMessages": bool(_messages(a2a_t) or _messages(a2a)),
        "caseSignature": case_signature(case),
    }


def _protocol_block(case: Mapping[str, Any], protocol: str) -> Mapping[str, Any]:
    """Return one protocol's expectation block of one case."""
    expect = case.get("expect")
    protocols = expect.get("protocols") if isinstance(expect, Mapping) else None
    block = protocols.get(protocol) if isinstance(protocols, Mapping) else None
    return block if isinstance(block, Mapping) else {}


def _rounds(block: Mapping[str, Any]) -> int:
    """Return one protocol block's expected negotiation rounds."""
    negotiation = block.get("negotiation")
    value = negotiation.get("expected_rounds") if isinstance(negotiation, Mapping) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _messages(block: Mapping[str, Any]) -> object:
    """Return the messages expectation of one protocol block, or ``None``."""
    negotiation = block.get("negotiation")
    return negotiation.get("messages") if isinstance(negotiation, Mapping) else None


def _is_blocked(case: Mapping[str, Any]) -> bool:
    """Return whether the case expects an interception."""
    return _protocol_block(case, PROTOCOL_A2A_T).get("validate") == "blocked"


def _negotiates(block: Mapping[str, Any]) -> bool:
    """Return whether one protocol block expects a negotiation at all."""
    negotiation = block.get("negotiation")
    if not isinstance(negotiation, Mapping):
        return False
    if negotiation.get("expected") is True:
        return True
    # Some converted cases spell the expectation as rounds or messages only.
    return _rounds(block) > 0 or bool(_messages(block))


def _negotiation_flags(case: Mapping[str, Any]) -> dict[str, bool]:
    """Return the negotiation expectation of both protocols, for the list markers and filters."""
    return {
        protocol: _negotiates(_protocol_block(case, protocol)) for protocol in PROTOCOLS
    }


__all__ = [
    "PROTOCOLS",
    "STATUSES",
    "STATUS_NOT_RUN",
    "STATUS_STALE",
    "ReviewService",
]
