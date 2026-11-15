"""The acceptance review store: evidence snapshots and human decisions.

Two things must survive the source batches:

* the **evidence** a verdict was based on, because tester batches are rotated and a decision has to
  stay explainable after the file is gone;
* the **decision** itself, with who made it, on which runs and against which case fingerprint.

Observations are append-only in spirit: a rescan may refresh the metadata of the same
``(case, protocol, batch, round, run)`` row, but never rewrites its history. Decisions are one row
per ``(case, protocol)``; changing one records an audit event.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

#: Manual review statuses. ``pending`` is the absence of a decision.
DECISION_PENDING: Final[str] = "pending"
DECISION_ACCEPTED: Final[str] = "accepted"
DECISION_DEFECT: Final[str] = "defect"
DECISION_KNOWN_ISSUE: Final[str] = "known_issue"
DECISION_RECHECK: Final[str] = "recheck"

DECISIONS: Final[tuple[str, ...]] = (
    DECISION_PENDING,
    DECISION_ACCEPTED,
    DECISION_DEFECT,
    DECISION_KNOWN_ISSUE,
    DECISION_RECHECK,
)

#: Who a defect belongs to. Empty for a non-defect decision.
CATEGORY_SDK: Final[str] = "sdk"
CATEGORY_VENDOR: Final[str] = "vendor"
CATEGORY_CASE: Final[str] = "case"
CATEGORY_ENV: Final[str] = "env"
CATEGORY_OTHER: Final[str] = "other"

CATEGORIES: Final[tuple[str, ...]] = (
    CATEGORY_SDK,
    CATEGORY_VENDOR,
    CATEGORY_CASE,
    CATEGORY_ENV,
    CATEGORY_OTHER,
)

_SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS observation (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id        INTEGER NOT NULL,
  protocol       TEXT NOT NULL,
  batch_id       TEXT NOT NULL,
  source_round   INTEGER NOT NULL,
  run_id         TEXT NOT NULL DEFAULT '',
  trace_id       TEXT NOT NULL DEFAULT '',
  observed_at    TEXT NOT NULL,
  model          TEXT,
  decision_mode  TEXT NOT NULL DEFAULT '',
  nms            INTEGER NOT NULL DEFAULT 0,
  nms_sim        INTEGER NOT NULL DEFAULT 0,
  discovery      TEXT NOT NULL DEFAULT '',
  transport      TEXT NOT NULL DEFAULT '',
  status         TEXT NOT NULL DEFAULT '',
  input_signature TEXT NOT NULL DEFAULT '',
  evidence_json  TEXT NOT NULL,
  first_seen     TEXT NOT NULL,
  last_seen      TEXT NOT NULL,
  UNIQUE (case_id, protocol, batch_id, source_round, run_id)
);
CREATE INDEX IF NOT EXISTS idx_review_observation_target
  ON observation(case_id, protocol, observed_at DESC);

CREATE TABLE IF NOT EXISTS decision (
  case_id        INTEGER NOT NULL,
  protocol       TEXT NOT NULL,
  status         TEXT NOT NULL,
  category       TEXT NOT NULL DEFAULT '',
  note           TEXT NOT NULL DEFAULT '',
  reviewer       TEXT NOT NULL DEFAULT '',
  evidence_json  TEXT NOT NULL DEFAULT '{}',
  case_signature TEXT NOT NULL DEFAULT '',
  corpus_version TEXT NOT NULL DEFAULT '',
  confirmed_at   TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  PRIMARY KEY (case_id, protocol)
);

CREATE TABLE IF NOT EXISTS event (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  at         TEXT NOT NULL,
  actor      TEXT,
  action     TEXT NOT NULL,
  target_key TEXT NOT NULL,
  payload    TEXT NOT NULL DEFAULT '{}'
);
"""


@dataclass(frozen=True)
class DecisionKey:
    """The identity of one review decision: one case, one protocol task."""

    case_id: int
    protocol: str

    @property
    def text(self) -> str:
        """Return the stable key used by the audit table."""
        return f"{self.case_id}:{self.protocol}"


def now_iso() -> str:
    """Return the current UTC time in ISO 8601 form."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class ReviewStore:
    """SQLite-backed store of the acceptance review console."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        """Return the database path (also shown by the page)."""
        return self._path

    def ensure(self) -> None:
        """Create the database and its schema when missing."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as connection:
            connection.executescript(_SCHEMA)
            connection.execute("PRAGMA user_version=1")

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def ingest(self, observations: Iterable[Mapping[str, object]]) -> int:
        """Insert or refresh the evidence observations of a scan."""
        inserted = 0
        stamp = now_iso()
        with self._session() as connection:
            for observation in observations:
                cursor = connection.execute(
                    """
                    INSERT INTO observation
                        (case_id, protocol, batch_id, source_round, run_id, trace_id,
                         observed_at, model, decision_mode, nms, nms_sim, discovery, transport,
                         status, input_signature, evidence_json, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (case_id, protocol, batch_id, source_round, run_id)
                    DO UPDATE SET
                        trace_id=excluded.trace_id,
                        observed_at=excluded.observed_at,
                        model=excluded.model,
                        decision_mode=excluded.decision_mode,
                        nms=excluded.nms,
                        nms_sim=excluded.nms_sim,
                        discovery=excluded.discovery,
                        transport=excluded.transport,
                        status=excluded.status,
                        input_signature=excluded.input_signature,
                        evidence_json=excluded.evidence_json,
                        last_seen=excluded.last_seen
                    """,
                    (
                        _as_int(observation.get("caseId")),
                        str(observation.get("protocol") or ""),
                        str(observation.get("batchId") or ""),
                        _as_int(observation.get("sourceRound"), 1),
                        str(observation.get("runId") or ""),
                        str(observation.get("traceId") or ""),
                        str(observation.get("observedAt") or ""),
                        observation.get("model"),
                        str(observation.get("decisionMode") or ""),
                        1 if observation.get("nms") else 0,
                        1 if observation.get("nmsSim") else 0,
                        str(observation.get("discovery") or ""),
                        str(observation.get("transport") or ""),
                        str(observation.get("status") or ""),
                        str(observation.get("inputSignature") or ""),
                        json.dumps(observation.get("evidence") or {}, ensure_ascii=False),
                        stamp,
                        stamp,
                    ),
                )
                inserted += int(cursor.rowcount > 0)
        return inserted

    def observations(self, case_id: int, protocol: str) -> list[dict[str, object]]:
        """Return the evidence rows of one case × protocol, newest first."""
        with self._session() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT * FROM observation
                WHERE case_id=? AND protocol=?
                ORDER BY observed_at DESC, source_round DESC, id DESC
                """,
                (case_id, protocol),
            ).fetchall()
        return [_observation_row(row) for row in rows]

    def latest_by_target(self) -> dict[tuple[int, str], dict[str, object]]:
        """Return the newest evidence row of every case × protocol, plus its run count."""
        counts: dict[tuple[int, str], int] = {}
        latest: dict[tuple[int, str], dict[str, object]] = {}
        with self._session() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM observation ORDER BY observed_at DESC, source_round DESC, id DESC"
            ).fetchall()
        for row in rows:
            key = (int(row["case_id"]), str(row["protocol"]))
            counts[key] = counts.get(key, 0) + 1
            if key not in latest:
                latest[key] = _observation_row(row)
        for key, row in latest.items():
            row["observations"] = counts.get(key, 0)
        return latest

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def decisions(self) -> dict[tuple[int, str], dict[str, object]]:
        """Return every stored decision keyed by case × protocol."""
        with self._session() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM decision").fetchall()
        return {
            (int(row["case_id"]), str(row["protocol"])): _decision_row(row) for row in rows
        }

    def decision(self, key: DecisionKey) -> dict[str, object] | None:
        """Return one stored decision, or ``None``."""
        with self._session() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM decision WHERE case_id=? AND protocol=?",
                (key.case_id, key.protocol),
            ).fetchone()
        return _decision_row(row) if row is not None else None

    def set_decision(
        self,
        key: DecisionKey,
        *,
        status: str,
        category: str = "",
        note: str = "",
        reviewer: str = "",
        evidence: Mapping[str, object] | None = None,
        case_signature: str = "",
        corpus_version: str = "",
        actor: str = "",
    ) -> dict[str, object]:
        """Record one review decision (or reset it with ``pending``)."""
        stamp = now_iso()
        with self._session() as connection:
            if status == DECISION_PENDING:
                connection.execute(
                    "DELETE FROM decision WHERE case_id=? AND protocol=?",
                    (key.case_id, key.protocol),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO decision
                        (case_id, protocol, status, category, note, reviewer, evidence_json,
                         case_signature, corpus_version, confirmed_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (case_id, protocol)
                    DO UPDATE SET
                        status=excluded.status,
                        category=excluded.category,
                        note=excluded.note,
                        reviewer=excluded.reviewer,
                        evidence_json=excluded.evidence_json,
                        case_signature=excluded.case_signature,
                        corpus_version=excluded.corpus_version,
                        confirmed_at=excluded.confirmed_at,
                        updated_at=excluded.updated_at
                    """,
                    (
                        key.case_id,
                        key.protocol,
                        status,
                        category,
                        note,
                        reviewer,
                        json.dumps(evidence or {}, ensure_ascii=False),
                        case_signature,
                        corpus_version,
                        stamp,
                        stamp,
                    ),
                )
            connection.execute(
                "INSERT INTO event (at, actor, action, target_key, payload) VALUES (?, ?, ?, ?, ?)",
                (
                    stamp,
                    actor,
                    "decision",
                    key.text,
                    json.dumps({"status": status, "category": category}, ensure_ascii=False),
                ),
            )
        return {
            "status": status,
            "category": category,
            "note": note,
            "reviewer": reviewer,
            "evidence": dict(evidence or {}),
            "caseSignature": case_signature,
            "corpusVersion": corpus_version,
            "confirmedAt": stamp,
            "updatedAt": stamp,
        }

    @contextmanager
    def _session(self) -> Generator[sqlite3.Connection, None, None]:
        """Open one short-lived connection, committing on success and always closing it."""
        connection = sqlite3.connect(self._path, timeout=10.0)
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


def _observation_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "caseId": int(row["case_id"]),
        "protocol": str(row["protocol"]),
        "batchId": str(row["batch_id"]),
        "sourceRound": int(row["source_round"] or 1),
        "runId": str(row["run_id"] or ""),
        "traceId": str(row["trace_id"] or ""),
        "observedAt": str(row["observed_at"] or ""),
        "model": row["model"],
        "decisionMode": str(row["decision_mode"] or ""),
        "nms": bool(row["nms"]),
        "nmsSim": bool(row["nms_sim"]),
        "discovery": str(row["discovery"] or ""),
        "transport": str(row["transport"] or ""),
        "status": str(row["status"] or ""),
        "inputSignature": str(row["input_signature"] or ""),
        "evidence": json.loads(str(row["evidence_json"] or "{}")),
        "firstSeen": str(row["first_seen"] or ""),
        "lastSeen": str(row["last_seen"] or ""),
    }


def _decision_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "caseId": int(row["case_id"]),
        "protocol": str(row["protocol"]),
        "status": str(row["status"]),
        "category": str(row["category"] or ""),
        "note": str(row["note"] or ""),
        "reviewer": str(row["reviewer"] or ""),
        "evidence": json.loads(str(row["evidence_json"] or "{}")),
        "caseSignature": str(row["case_signature"] or ""),
        "corpusVersion": str(row["corpus_version"] or ""),
        "confirmedAt": str(row["confirmed_at"] or ""),
        "updatedAt": str(row["updated_at"] or ""),
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


__all__ = [
    "CATEGORIES",
    "CATEGORY_CASE",
    "CATEGORY_ENV",
    "CATEGORY_OTHER",
    "CATEGORY_SDK",
    "CATEGORY_VENDOR",
    "DECISIONS",
    "DECISION_ACCEPTED",
    "DECISION_DEFECT",
    "DECISION_KNOWN_ISSUE",
    "DECISION_PENDING",
    "DECISION_RECHECK",
    "DecisionKey",
    "ReviewStore",
    "now_iso",
]
