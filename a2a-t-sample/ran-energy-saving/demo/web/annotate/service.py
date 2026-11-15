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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from eval.osp.catalog import PROTOCOL_A2A, PROTOCOL_A2A_T, CaseCatalog
from eval.osp.expectations import MESSAGE_DIMENSION, SLOT_DIMENSION
from eval.osp.followup import is_blocked_case

from demo.web.annotate.corpus import case_documents, scan_corpus
from demo.web.annotate.store import (
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

    def progress(self) -> dict[str, object]:
        """Return the number of subtasks per status and the last scan summary."""
        counts: dict[str, int] = {
            status: 0 for status in (TARGET_PENDING, TARGET_DRAFT, TARGET_CONFIRMED)
        }
        for case in self.cases():
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

    def _pending_value(self, key: TargetKey) -> object | None:
        """Return the value the accepted choices of one subtask would export."""
        payload = self._store.export_payload(confirmed_only=False)
        entries = payload.get("cases")
        if not isinstance(entries, list):
            return None
        for entry in entries:
            if not isinstance(entry, Mapping) or as_int(entry.get("id")) != key.case_id:
                continue
            block = entry.get(key.protocol)
            if not isinstance(block, Mapping):
                return None
            return block.get(key.dimension)
        return None


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
