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
from eval.osp.tracecheck import original_request
from langfuse_export.config import Config

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
        self._trace_store = (
            trace_store if trace_store is not None else TraceStore(self._runs_dir)
        )
        #: Langfuse credentials for the opt-in fetch; ``None`` means "not configured yet".
        self._trace_config = trace_config
        #: The standalone read-only instance leaves this off: it must not call Langfuse at all.
        self._trace_fetch_enabled = trace_fetch_enabled
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

    def scan(self, *, days: float = 7.0, max_batches: int = 500) -> dict[str, object]:
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
                    nms_kind="real",
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

    def fetch_trace(self, case_id: int, protocol: str) -> dict[str, object]:
        """Fetch one run's trace snapshot from Langfuse (explicit opt-in) and attach it."""
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
        observation = rows[0]
        target = _trace_target(observation)
        if target is None:
            raise ValueError("该运行不是真机下发，轨迹不参与验收。")
        config = self._resolved_trace_config()
        result = self._trace_store.fetch(
            str(target["traceId"]), config, run_id=str(target["runId"])
        )
        if str(result.get("exportState") or "") != "exported":
            raise ValueError(f"轨迹抓取失败：{result.get('error') or result.get('exportState')}")
        payload = self._trace_store.cached_payload(
            str(target["traceId"]),
            run_id=str(target["runId"]),
            nms_kind="real",
            local_points=_string_list(target.get("localPoints")),
            local_known=_string_list(target.get("localKnown")),
        )
        stored = self._store.set_trace(
            case_id=case_id,
            protocol=protocol,
            batch_id=str(observation.get("batchId") or ""),
            source_round=_as_int(observation.get("sourceRound"), 1),
            run_id=str(observation.get("runId") or ""),
            trace=payload,
            fetched_at=now_iso(),
        )
        if not stored:
            raise ValueError("该证据行已不在库中，请重新采集。")
        return payload

    def _resolved_trace_config(self) -> Config:
        """Return the Langfuse credentials, loading them from the environment once."""
        if self._trace_config is not None:
            return self._trace_config
        from langfuse_export.config import ConfigError, load_config

        try:
            self._trace_config = load_config(cwd=self._runs_dir)
        except ConfigError as exc:
            raise ValueError(
                "未配置 Langfuse 凭据（LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY）。"
            ) from exc
        return self._trace_config

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
                task["exportDetail"] = (
                    judgement.get("detail") if isinstance(judgement, Mapping) else {}
                )
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
        rounds = [
            self._round_row(case, protocol, observation) for observation in observations
        ]
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
                "decision": self._decision_block(
                    case, protocol, decision, [], scope=scope
                ),
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
                dict.fromkeys(
                    str(diff.get("reason") or "")
                    for diff in trace_dimension.diffs
                    if diff.get("reason")
                )
            )
        return {
            "protocol": protocol,
            "status": status,
            "dimensions": [
                {"dimension": row.dimension, "status": row.status, "reason": row.reason}
                for row in judgement.dimensions
            ],
            "failures": [
                {
                    "dimension": row.dimension,
                    "status": row.status,
                    "reason": row.reason,
                    "diffs": [dict(diff) for diff in row.diffs][:20],
                }
                for row in judgement.dimensions
                if row.status
                in (VERDICT_FAIL, VERDICT_PARTIAL, VERDICT_MISSING, VERDICT_RECORDED)
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
            "judgement": judgement.to_dict(),
            "requests": _request_sources(case, evidence_map),
        }

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


def _trace_target(observation: Mapping[str, object]) -> dict[str, object] | None:
    """Return the trace lookup of one observation, or ``None`` when it is not judgeable.

    Only a batch that declared the real vendor NMS and a run that actually dispatched have a
    vendor trace to look up; an undeclared legacy batch must not borrow a trace by accident.
    """
    if observation.get("nmsDeclared") is not True or observation.get("nmsSimDeclared") is not False:
        return None
    run_id = observation.get("runId")
    trace_id = observation.get("traceId")
    if not isinstance(run_id, str) or not run_id:
        return None
    if not isinstance(trace_id, str) or not trace_id:
        return None
    evidence = observation.get("evidence")
    evidence_map = evidence if isinstance(evidence, Mapping) else {}
    return {
        "runId": run_id,
        "traceId": trace_id,
        "localPoints": evidence_map.get("localPoints"),
        "localKnown": evidence_map.get("localKnown"),
    }


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
    trace = observation.get("trace")
    if trace is not None:
        merged["trace"] = trace
    merged["batch"] = {
        "nms": observation.get("nmsDeclared"),
        "nmsSim": observation.get("nmsSimDeclared"),
    }
    return merged


def _request_sources(
    case: Mapping[str, Any], evidence: Mapping[str, Any]
) -> dict[str, object]:
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
        "hasMessages": bool(
            _messages(a2a_t) or _messages(a2a)
        ),
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


__all__ = [
    "PROTOCOLS",
    "STATUSES",
    "STATUS_NOT_RUN",
    "STATUS_STALE",
    "ReviewService",
]
