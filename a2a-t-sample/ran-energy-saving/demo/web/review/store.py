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
from collections.abc import Generator, Iterable, Mapping, Sequence
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
CREATE INDEX IF NOT EXISTS idx_review_observation_batch
  ON observation(batch_id, source_round);
CREATE INDEX IF NOT EXISTS idx_review_observation_run
  ON observation(run_id);
CREATE INDEX IF NOT EXISTS idx_review_observation_trace
  ON observation(trace_id);

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


#: Schema version written after the additive migration (version 1 was the first release).
SCHEMA_VERSION: Final[int] = 2

#: Columns added after version 1, applied when missing so an existing store upgrades in place.
#: SQLite has no ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS``, so the migration checks
#: ``PRAGMA table_info`` first. All four stay nullable: ``NULL`` means "not recorded", which is
#: different from ``0`` for the declared downstream flags.
_ADDITIVE_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("trace_json", "TEXT"),
    ("trace_fetched_at", "TEXT"),
    ("nms_declared", "INTEGER"),
    ("nms_sim_declared", "INTEGER"),
)


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
        """Create the database and its schema when missing, then apply the additive migrations."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as connection:
            connection.executescript(_SCHEMA)
            self._migrate(connection)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Add the columns a version-1 store lacks; a fresh store already has them."""
        existing = {str(row[1]) for row in connection.execute("PRAGMA table_info(observation)")}
        for name, kind in _ADDITIVE_COLUMNS:
            if name not in existing:
                connection.execute(f"ALTER TABLE observation ADD COLUMN {name} {kind}")

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
                         nms_declared, nms_sim_declared,
                         status, input_signature, evidence_json, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        nms_declared=excluded.nms_declared,
                        nms_sim_declared=excluded.nms_sim_declared,
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
                        _optional_bool(observation.get("nmsDeclared")),
                        _optional_bool(observation.get("nmsSimDeclared")),
                        str(observation.get("status") or ""),
                        str(observation.get("inputSignature") or ""),
                        json.dumps(observation.get("evidence") or {}, ensure_ascii=False),
                        stamp,
                        stamp,
                    ),
                )
                inserted += int(cursor.rowcount > 0)
        return inserted

    def set_trace(
        self,
        *,
        case_id: int,
        protocol: str,
        batch_id: str,
        source_round: int,
        run_id: str,
        trace: Mapping[str, object],
        fetched_at: str,
    ) -> int:
        """Attach a trace snapshot to one observation; a rescan never overwrites it.

        The snapshot is evidence, not a verdict: the review re-judges it on read, so a deleted
        snapshot degrades to "not exported" instead of preserving a stale pass.
        """
        with self._session() as connection:
            cursor = connection.execute(
                """
                UPDATE observation
                   SET trace_json=?, trace_fetched_at=?
                 WHERE case_id=? AND protocol=? AND batch_id=? AND source_round=? AND run_id=?
                """,
                (
                    json.dumps(dict(trace), ensure_ascii=False),
                    fetched_at,
                    case_id,
                    protocol,
                    batch_id,
                    source_round,
                    run_id,
                ),
            )
        return int(cursor.rowcount)

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

    def batches(self) -> list[dict[str, object]]:
        """Return one row per ingested batch, newest first.

        The batch's own metadata (model, downstream, transport) is read from its observations, so
        the list survives the rotation of ``runs/tester/*.json``.
        """
        with self._session() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT batch_id,
                       MAX(observed_at) AS observed_at,
                       COUNT(*) AS observations,
                       COUNT(DISTINCT case_id) AS cases,
                       MAX(source_round) AS rounds,
                       MIN(model) AS model,
                       MIN(decision_mode) AS decision_mode,
                       MAX(nms) AS nms,
                       MAX(nms_sim) AS nms_sim,
                       MAX(nms_declared) AS nms_declared,
                       MAX(nms_sim_declared) AS nms_sim_declared,
                       MIN(discovery) AS discovery,
                       MIN(transport) AS transport
                FROM observation
                GROUP BY batch_id
                ORDER BY MAX(observed_at) DESC, batch_id DESC
                """
            ).fetchall()
            per_round = connection.execute(
                """
                SELECT batch_id, source_round,
                       COUNT(*) AS observations,
                       COUNT(DISTINCT case_id) AS cases
                FROM observation
                GROUP BY batch_id, source_round
                ORDER BY batch_id, source_round
                """
            ).fetchall()
        rounds_by_batch: dict[str, list[dict[str, int]]] = {}
        for row in per_round:
            rounds_by_batch.setdefault(str(row["batch_id"]), []).append(
                {
                    "round": max(1, int(row["source_round"] or 1)),
                    "observations": int(row["observations"] or 0),
                    "cases": int(row["cases"] or 0),
                }
            )
        return [
            {
                "batchId": str(row["batch_id"]),
                "observedAt": str(row["observed_at"] or ""),
                "observations": int(row["observations"] or 0),
                "cases": int(row["cases"] or 0),
                "rounds": max(1, int(row["rounds"] or 1)),
                #: One entry per source round: the list shows each round on its own, and the
                #: batch-wide count would overstate an older round after a retry.
                "roundsDetail": rounds_by_batch.get(str(row["batch_id"]), []),
                "model": row["model"],
                "decisionMode": str(row["decision_mode"] or ""),
                "nms": bool(row["nms"]),
                "nmsSim": bool(row["nms_sim"]),
                "nmsDeclared": _optional_bool_value(row["nms_declared"]),
                "nmsSimDeclared": _optional_bool_value(row["nms_sim_declared"]),
                "discovery": str(row["discovery"] or ""),
                "transport": str(row["transport"] or ""),
            }
            for row in rows
        ]

    def observations_for_batch(
        self, batch_id: str, round: int | None = None
    ) -> list[dict[str, object]]:
        """Return the evidence rows of one batch, newest first; ``round`` narrows it."""
        if round is None:
            sql = """
                SELECT * FROM observation
                WHERE batch_id=?
                ORDER BY observed_at DESC, source_round DESC, id DESC
            """
            params: tuple[object, ...] = (batch_id,)
        else:
            sql = """
                SELECT * FROM observation
                WHERE batch_id=? AND source_round=?
                ORDER BY observed_at DESC, id DESC
            """
            params = (batch_id, int(round))
        with self._session() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(sql, params).fetchall()
        return [_observation_row(row) for row in rows]

    def observations_by_ids(self, ids: Sequence[str]) -> list[dict[str, object]]:
        """Return every evidence row matched by run id or trace id, newest first.

        A trace id is not one run: presentation and correction-chain runs share the workflow
        trace, so the caller must group by the returned ``runId`` instead of assuming 1:1. The
        ids are chunked well below SQLite's parameter limit, and rows are deduplicated by the
        observation primary key because one row can match two different input ids.
        """
        wanted = list(dict.fromkeys(str(value).strip() for value in ids if str(value).strip()))
        if not wanted:
            return []
        found: dict[int, dict[str, object]] = {}
        with self._session() as connection:
            connection.row_factory = sqlite3.Row
            for start in range(0, len(wanted), 400):
                chunk = wanted[start : start + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT * FROM observation
                    WHERE run_id IN ({placeholders}) OR trace_id IN ({placeholders})
                    ORDER BY observed_at DESC, source_round DESC, id DESC
                    """,
                    (*chunk, *chunk),
                ).fetchall()
                for row in rows:
                    parsed = _observation_row(row)
                    found.setdefault(_as_int(parsed.get("id")), parsed)
        return sorted(
            found.values(),
            key=lambda row: (str(row.get("observedAt") or ""), str(row.get("id"))),
            reverse=True,
        )

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
        "trace": _json_or_none(row["trace_json"]),
        "traceFetchedAt": str(row["trace_fetched_at"] or ""),
        "nmsDeclared": _optional_bool_value(row["nms_declared"]),
        "nmsSimDeclared": _optional_bool_value(row["nms_sim_declared"]),
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


def _optional_bool(value: object) -> int | None:
    """Return ``1``/``0`` for a declared flag, ``NULL`` when the batch never recorded it."""
    if value is None:
        return None
    return 1 if value else 0


def _optional_bool_value(value: object) -> bool | None:
    """Return a stored nullable flag as a bool, or ``None`` when it was not recorded."""
    if value is None:
        return None
    return bool(value)


def _json_or_none(value: object) -> object:
    """Return a stored JSON column parsed, or ``None`` when empty or unreadable."""
    if not value:
        return None
    try:
        return json.loads(str(value))
    except ValueError:
        return None


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
