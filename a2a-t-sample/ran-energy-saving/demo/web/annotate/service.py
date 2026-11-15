"""Service layer of the expectation annotation console.

The console mirrors the corpus: one row per case, two protocol tasks per case (A2A-T and A2A), and
the expectation subtasks each protocol actually has.

    case 16  ─ a2a-t ─ slot_errors        (only when the case expects an interception)
             │       └ messages           (only when the case expects a negotiation)
             └ a2a   └ messages

A protocol with no subtask is still listed, as ``none``, because "this protocol expects no
negotiation" is itself a fact of the case; the page can hide those rows, but the API never pretends
they do not exist. Every target comes from the catalog, so a case nobody has run yet still appears
(as ``pending``) instead of hiding until it has evidence.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from eval.osp.catalog import PROTOCOL_A2A, PROTOCOL_A2A_T, CaseCatalog
from eval.osp.expectations import MESSAGE_DIMENSION, SLOT_DIMENSION, SLOT_MODES
from eval.osp.followup import is_blocked_case

from demo.web.annotate.corpus import case_documents, scan_corpus
from demo.web.annotate.store import (
    DIMENSIONS,
    TARGET_CONFIRMED,
    TARGET_DRAFT,
    TARGET_PENDING,
    AnnotationStore,
    TargetKey,
    as_int,
)

_LOGGER = logging.getLogger("ran_energy_saving.annotate")

#: Protocol tasks every case carries, in display order.
PROTOCOLS: Final[tuple[str, ...]] = (PROTOCOL_A2A_T, PROTOCOL_A2A)

#: Status of a protocol task that has no expectation to annotate.
STATUS_NONE: Final[str] = "none"


@dataclass(frozen=True)
class ScanSummary:
    """Outcome of one corpus scan, as reported by the console header."""

    inserted: int
    observations: int
    batches: int
    window_start: str
    window_end: str
    skipped: Mapping[str, int]
    truncated: bool

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation."""
        return {
            "inserted": self.inserted,
            "observations": self.observations,
            "batches": self.batches,
            "windowStart": self.window_start,
            "windowEnd": self.window_end,
            "skipped": dict(self.skipped),
            "truncated": self.truncated,
        }


class AnnotateService:
    """Corpus scanning and case projection for the annotation console."""

    def __init__(
        self,
        *,
        store: AnnotationStore,
        catalog: CaseCatalog,
        runs_dir: Path,
        client_db_path: Path | None,
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._runs_dir = Path(runs_dir)
        self._client_db_path = client_db_path
        self._last_scan: ScanSummary | None = None

    @property
    def store(self) -> AnnotationStore:
        """Return the annotation store (routes export from it)."""
        return self._store

    def scan(self, *, days: float = 1.0, max_batches: int = 500) -> ScanSummary:
        """Collect the candidates of the recent tester batches into the store."""
        cases = case_documents(self._catalog.documents())
        scan = scan_corpus(
            runs_dir=self._runs_dir,
            client_db_path=self._client_db_path,
            cases=cases,
            days=days,
            max_batches=max_batches,
        )
        inserted = self._store.ingest(scan.observations)
        summary = ScanSummary(
            inserted=inserted,
            observations=len(scan.observations),
            batches=len(scan.batches),
            window_start=scan.window_start,
            window_end=scan.window_end,
            skipped=scan.skipped,
            truncated=scan.truncated,
        )
        self._last_scan = summary
        _LOGGER.info(
            "annotation scan: %d observations (%d new) from %d batches",
            summary.observations,
            summary.inserted,
            summary.batches,
        )
        return summary

    def adopt_traces(
        self,
        traces: Sequence[str],
        *,
        protocol: str | None = None,
        dimension: str | None = None,
        confirm: bool = True,
        replace: bool = False,
        actor: str = "",
        max_batches: int = 500,
    ) -> dict[str, object]:
        """Adopt the observations of the supplied traceIds/runIds as the annotation values.

        The operator supplies identifiers of runs already verified as good, so the console can do
        in one action what it would otherwise ask for one checkbox at a time. Nothing is adopted
        blindly:

        * only items carrying one of the identifiers are read, and the time window is ignored —
          the list is the selection;
        * the consistency gate still applies: an observation that diverges from the case's existing
          verdict or round count is never promoted;
        * a target that already has adopted candidates or a corpus value is skipped unless
          ``replace`` is set, so a bulk run cannot silently overwrite reviewed work.
        """
        tokens = tuple(
            dict.fromkeys(
                str(token).strip().lower() for token in traces if str(token).strip()
            )
        )
        cases = case_documents(self._catalog.documents())
        scan = scan_corpus(
            runs_dir=self._runs_dir,
            client_db_path=self._client_db_path,
            cases=cases,
            max_batches=max_batches,
            traces=tokens,
        )
        inserted = self._store.ingest(scan.observations)
        rows = self._store.observations_by_traces(tokens)
        found = _identified(rows)
        grouped = _group_by_target(rows, protocol=protocol, dimension=dimension)
        adopted: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        for key in sorted(grouped, key=lambda item: (item.case_id, item.protocol, item.dimension)):
            group = grouped[key]
            label: dict[str, object] = {
                "caseId": key.case_id,
                "protocol": key.protocol,
                "dimension": key.dimension,
            }
            consistent = [row for row in group if row.get("consistent")]
            if not consistent:
                skipped.append({**label, "reason": "inconsistent"})
                continue
            # An empty block (``slot_errors: []``) states no expectation, exactly as the matcher
            # reads it, so truthiness — not ``is not None`` — decides whether a value exists.
            if not replace and self._current_value_of(key):
                skipped.append({**label, "reason": "has_value"})
                continue
            if not replace and self._store.accepted_ids(key):
                skipped.append({**label, "reason": "already_adopted"})
                continue
            observation_ids = [
                as_int(row.get("id")) for row in _distinct_observations(consistent)
            ]
            choices = self._store.adopt(
                key,
                observation_ids,
                replace=replace,
                confirm=confirm,
                actor=actor,
            )
            adopted.append(
                {
                    **label,
                    "choices": choices,
                    "status": TARGET_CONFIRMED if confirm else TARGET_DRAFT,
                }
            )
        matched = tuple(token for token in tokens if token in scan.matched_ids or token in found)
        unknown = tuple(token for token in tokens if token not in matched)
        no_candidate = tuple(token for token in matched if token not in found)
        _LOGGER.info(
            "annotation trace adoption: %d identifiers, %d targets adopted, %d skipped",
            len(tokens),
            len(adopted),
            len(skipped),
        )
        return {
            "requested": len(tokens),
            "matched": list(matched),
            "unknown": list(unknown),
            "noCandidate": list(no_candidate),
            "batches": len(scan.batches),
            "observations": len(scan.observations),
            "inserted": inserted,
            "truncated": scan.truncated,
            "skippedFiles": dict(scan.skipped),
            "confirm": confirm,
            "replace": replace,
            "adopted": adopted,
            "skipped": skipped,
        }

    def apply_targets(
        self,
        case_id: int,
        protocol: str,
        targets: Sequence[Mapping[str, object]],
        *,
        confirm: bool = True,
        actor: str = "",
    ) -> dict[str, dict[str, object]]:
        """Save the page's selections for one case+protocol in one request.

        The page deliberately does not save per checkbox: an operator selects a few candidates,
        then presses 确认保存 once. Each posted dimension's accepted set is replaced by exactly the
        posted ids, its mode/note are updated, and a non-empty target is confirmed. The refreshed
        detail payloads come back with the response so the page never has to reload blind.
        """
        if self._catalog.get(case_id) is None:
            raise ValueError("未知用例。")
        if protocol not in PROTOCOLS:
            raise ValueError(f"protocol 只能是 {', '.join(PROTOCOLS)}。")
        details: dict[str, dict[str, object]] = {}
        for raw in targets:
            dimension = str(raw.get("dimension") or "")
            if dimension not in DIMENSIONS:
                raise ValueError(f"dimension 只能是 {', '.join(DIMENSIONS)}。")
            if dimension == SLOT_DIMENSION and protocol != PROTOCOL_A2A_T:
                raise ValueError("slot_errors 只属于 A2A-T 协议任务。")
            mode = raw.get("mode")
            if dimension == SLOT_DIMENSION and mode is not None and mode not in SLOT_MODES:
                raise ValueError(f"mode 只能是 {', '.join(SLOT_MODES)}。")
            note = raw.get("note")
            self._store.apply(
                TargetKey(case_id, protocol, dimension),
                _observation_ids(raw.get("observationIds")),
                mode=str(mode) if dimension == SLOT_DIMENSION and mode is not None else None,
                note=note if isinstance(note, str) else None,
                edits=_edits(raw.get("edits")),
                confirm=confirm,
                actor=actor,
            )
            details[dimension] = self.detail(case_id, protocol, dimension)
        if not details:
            raise ValueError("没有要保存的子任务。")
        return details

    def cases(self) -> list[dict[str, object]]:
        """Return every case with its two protocol tasks and their subtasks."""
        observed = {
            (
                as_int(row.get("caseId")),
                str(row.get("protocol") or ""),
                str(row.get("dimension") or ""),
            ): row
            for row in self._store.targets()
        }
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
                    "protocols": [
                        self._protocol_task(case, case_id, protocol, observed)
                        for protocol in PROTOCOLS
                    ],
                }
            )
        return rows

    def detail(self, case_id: int, protocol: str, dimension: str) -> dict[str, object]:
        """Return the candidates, choices and current corpus value of one subtask."""
        key = TargetKey(case_id, protocol, dimension)
        detail = self._store.detail(key)
        case = self._catalog.get(case_id)
        detail["case"] = {
            "id": case_id,
            "name": str((case or {}).get("name") or ""),
            "operation": (case or {}).get("operation"),
            "dimension": (case or {}).get("dimension"),
            "userInput": (case or {}).get("user_input"),
            "validate": _expected_validate(case),
            "expectedRounds": _expected_rounds(case, protocol),
            "kinds": _expected_kinds(case, protocol),
        }
        detail["current"] = _current_value(case, protocol, dimension)
        detail["pending"] = self._pending_value(key)
        return detail

    def progress(self, rows: list[dict[str, object]] | None = None) -> dict[str, object]:
        """Return the number of subtasks per status and the last scan summary.

        ``rows`` lets a caller that already built the case projection reuse it: the counters are a
        pure function of the same data, and rebuilding 100 cases twice doubled the cost of every
        page load.
        """
        counts: dict[str, int] = {
            status: 0 for status in (TARGET_PENDING, TARGET_DRAFT, TARGET_CONFIRMED)
        }
        for case in rows if rows is not None else self.cases():
            protocols = case.get("protocols")
            if not isinstance(protocols, list):
                continue
            for task in protocols:
                subtasks = task.get("subtasks") if isinstance(task, Mapping) else None
                if not isinstance(subtasks, list):
                    continue
                for subtask in subtasks:
                    if not isinstance(subtask, Mapping):
                        continue
                    status = str(subtask.get("status") or TARGET_PENDING)
                    counts[status] = counts.get(status, 0) + 1
        return {
            "counts": {**counts, "total": sum(counts.values())},
            "caseCount": len(self._catalog.documents()),
            "lastScan": self._last_scan.to_dict() if self._last_scan is not None else None,
        }

    def _protocol_task(
        self,
        case: Mapping[str, Any],
        case_id: int,
        protocol: str,
        observed: Mapping[tuple[int, str, str], Mapping[str, object]],
    ) -> dict[str, object]:
        """Build one protocol task with the subtasks this case actually has."""
        subtasks: list[dict[str, object]] = []
        if protocol == PROTOCOL_A2A_T and is_blocked_case(case):
            subtasks.append(self._subtask(case, case_id, protocol, SLOT_DIMENSION, observed))
        if _expected_rounds(case, protocol) > 0:
            subtasks.append(self._subtask(case, case_id, protocol, MESSAGE_DIMENSION, observed))
        return {
            "protocol": protocol,
            "status": _aggregate_status(subtasks),
            "validate": _expected_validate(case) if protocol == PROTOCOL_A2A_T else None,
            "expectedRounds": _expected_rounds(case, protocol),
            "kinds": _expected_kinds(case, protocol),
            "subtasks": subtasks,
        }

    def _subtask(
        self,
        case: Mapping[str, Any],
        case_id: int,
        protocol: str,
        dimension: str,
        observed: Mapping[tuple[int, str, str], Mapping[str, object]],
    ) -> dict[str, object]:
        """Build one subtask row from the catalog and the stored evidence."""
        evidence = observed.get((case_id, protocol, dimension), {})
        current = _current_value(case, protocol, dimension)
        return {
            "caseId": case_id,
            "protocol": protocol,
            "dimension": dimension,
            "observations": as_int(evidence.get("observations")),
            "consistentObservations": as_int(evidence.get("consistentObservations")),
            "runs": as_int(evidence.get("runs")),
            "accepted": as_int(evidence.get("accepted")),
            "status": str(evidence.get("status") or TARGET_PENDING),
            "mode": evidence.get("mode"),
            "note": evidence.get("note"),
            "confirmedBy": evidence.get("confirmedBy"),
            "confirmedAt": evidence.get("confirmedAt"),
            "hasCurrent": current is not None,
            "current": current,
        }

    def _current_value_of(self, key: TargetKey) -> object | None:
        """Return the corpus value of one target, or ``None`` when the case states none yet."""
        return _current_value(self._catalog.get(key.case_id), key.protocol, key.dimension)

    def _pending_value(self, key: TargetKey) -> object | None:
        """Return the value the accepted choices of one subtask would export."""
        return self._store.value(key)


def _observation_ids(raw: object) -> list[int]:
    """Return the observation ids of a save payload, ignoring anything that is not an integer."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    return [
        int(value)
        for value in raw
        if isinstance(value, int) and not isinstance(value, bool)
    ]


def _edits(raw: object) -> dict[int, str]:
    """Return the per-candidate edited texts of a save payload, keyed by observation id."""
    if not isinstance(raw, Mapping):
        return {}
    edits: dict[int, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and key.strip().isdigit() and isinstance(value, str):
            edits[int(key.strip())] = value
    return edits


def _identified(rows: Sequence[Mapping[str, object]]) -> set[str]:
    """Return the traceIds and runIds the given observations carry, normalized for matching."""
    identifiers: set[str] = set()
    for row in rows:
        for key in ("traceId", "runId"):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                identifiers.add(value.strip().lower())
    return identifiers


def _group_by_target(
    rows: Sequence[Mapping[str, object]],
    *,
    protocol: str | None,
    dimension: str | None,
) -> dict[TargetKey, list[dict[str, object]]]:
    """Group the observations of the selected traces by the target they annotate."""
    grouped: dict[TargetKey, list[dict[str, object]]] = {}
    for row in rows:
        if protocol is not None and row.get("protocol") != protocol:
            continue
        if dimension is not None and row.get("dimension") != dimension:
            continue
        key = TargetKey(
            as_int(row.get("caseId")),
            str(row.get("protocol") or ""),
            str(row.get("dimension") or ""),
        )
        grouped.setdefault(key, []).append(dict(row))
    return grouped


def _distinct_observations(
    rows: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    """Return one observation per distinct value and round, newest first.

    The same value arrives once per batch and, for a repeated trace, once per source round; a
    matcher (or a message alternative) must be emitted once, not once per appearance. The round is
    part of the identity because two rounds may legitimately render the same text.
    """
    ordered = sorted(
        rows,
        key=lambda row: (
            str(row.get("observedAt") or ""),
            as_int(row.get("seenCount"), 1),
            as_int(row.get("id")),
        ),
        reverse=True,
    )
    seen: set[tuple[int, str]] = set()
    picked: list[Mapping[str, object]] = []
    for row in ordered:
        identity = (as_int(row.get("round")), str(row.get("contentHash") or ""))
        if identity in seen:
            continue
        seen.add(identity)
        picked.append(row)
    return picked


def _aggregate_status(subtasks: list[dict[str, object]]) -> str:
    """Return the status of one protocol task from its subtasks."""
    if not subtasks:
        return STATUS_NONE
    statuses = {str(subtask.get("status") or TARGET_PENDING) for subtask in subtasks}
    for status in (TARGET_PENDING, TARGET_DRAFT, TARGET_CONFIRMED):
        if status in statuses:
            return status
    return TARGET_CONFIRMED


def _expected_validate(case: Mapping[str, Any] | None) -> object:
    """Return the case's expected A2A-T validation verdict."""
    block = _protocol_block(case, PROTOCOL_A2A_T)
    return block.get("validate")


def _expected_rounds(case: Mapping[str, Any] | None, protocol: str) -> int:
    """Return the expected negotiation round count of one protocol."""
    negotiation = _protocol_block(case, protocol).get("negotiation")
    if not isinstance(negotiation, Mapping):
        return 0
    value = negotiation.get("expected_rounds")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _expected_kinds(case: Mapping[str, Any] | None, protocol: str) -> list[str] | None:
    """Return the expected negotiation kinds of one protocol, when stated."""
    negotiation = _protocol_block(case, protocol).get("negotiation")
    if not isinstance(negotiation, Mapping):
        return None
    kinds = negotiation.get("kinds")
    return [str(kind) for kind in kinds] if isinstance(kinds, list) else None


def _current_value(
    case: Mapping[str, Any] | None,
    protocol: str,
    dimension: str,
) -> object | None:
    """Return the expectation the corpus already states for one subtask."""
    block = _protocol_block(case, protocol)
    if dimension == SLOT_DIMENSION:
        return block.get(SLOT_DIMENSION)
    negotiation = block.get("negotiation")
    return negotiation.get(MESSAGE_DIMENSION) if isinstance(negotiation, Mapping) else None


def _protocol_block(case: Mapping[str, Any] | None, protocol: str) -> Mapping[str, Any]:
    """Return one protocol's expectation block of one case."""
    if case is None:
        return {}
    expect = case.get("expect")
    protocols = expect.get("protocols") if isinstance(expect, Mapping) else None
    block = protocols.get(protocol) if isinstance(protocols, Mapping) else None
    return block if isinstance(block, Mapping) else {}


__all__ = ["AnnotateService", "PROTOCOLS", "STATUS_NONE", "ScanSummary"]
