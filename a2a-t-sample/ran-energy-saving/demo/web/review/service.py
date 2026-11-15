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
)

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
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._runs_dir = Path(runs_dir)
        self._client_db_path = client_db_path
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
        summary: dict[str, object] = {
            "inserted": inserted,
            "observations": len(scan.observations),
            "batches": len(scan.batches),
            "windowStart": scan.window_start,
            "windowEnd": scan.window_end,
            "skipped": dict(scan.skipped),
            "truncated": scan.truncated,
        }
        self._last_scan = summary
        return summary

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
    ) -> dict[str, object]:
        """Build one protocol task row for the list and the header counters."""
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
                "decision": self._decision_block(case, protocol, decision, []),
            }
        evidence = observation.get("evidence")
        evidence_map = evidence if isinstance(evidence, Mapping) else {}
        input_changed = not evidence_is_current_input(case, protocol, evidence_map)
        judgement = judge_protocol(case, protocol, evidence_map)
        status = STATUS_STALE if input_changed else judgement.status
        return {
            "protocol": protocol,
            "status": status,
            "dimensions": [
                {"dimension": row.dimension, "status": row.status}
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
            "decision": self._decision_block(
                case,
                protocol,
                decision,
                [{"runId": observation.get("runId"), "batchId": observation.get("batchId")}],
            ),
        }

    def _round_row(
        self,
        case: Mapping[str, Any],
        protocol: str,
        observation: Mapping[str, object],
    ) -> dict[str, object]:
        """Build one judged evidence round for the detail payload."""
        evidence = observation.get("evidence")
        evidence_map = evidence if isinstance(evidence, Mapping) else {}
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
        }

    def _decision_block(
        self,
        case: Mapping[str, Any],
        protocol: str,
        decision: Mapping[str, object] | None,
        rounds: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        """Return the decision together with its staleness flags."""
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
        new_evidence = bool(newest) and newest not in known
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
    return {
        "id": int(case["id"]),
        "name": str(case.get("name") or ""),
        "operation": case.get("operation"),
        "dimension": case.get("dimension"),
        "userInput": case.get("user_input"),
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
