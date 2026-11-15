"""The annotation store: candidates, choices and the confirmation state of one target.

The store is deliberately outside both the generated case corpus and the run client database:

* the case YAML is a generated artifact and the operator has to review what a script would write;
* the run store owns protocol state, and an editable draft does not belong in it.

Everything is append-only in spirit: an observation is never rewritten (only its ``seen_count`` is
bumped when the same batch is ingested twice), a choice replacement records a new row, and the
confirmation keeps who confirmed and when. That is what lets an acceptance report say where an
expectation came from long after the batch is gone.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from collections.abc import Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from eval.osp.expectations import MESSAGE_DIMENSION, SLOT_DIMENSION, SLOT_MODE_ANY

#: Status of one annotation target.
TARGET_PENDING: Final[str] = "pending"
TARGET_DRAFT: Final[str] = "draft"
TARGET_CONFIRMED: Final[str] = "confirmed"
TARGET_APPLIED: Final[str] = "applied"
TARGETS: Final[tuple[str, ...]] = (
    TARGET_PENDING,
    TARGET_DRAFT,
    TARGET_CONFIRMED,
    TARGET_APPLIED,
)

#: Dimensions this console annotates.
DIMENSIONS: Final[tuple[str, ...]] = (SLOT_DIMENSION, MESSAGE_DIMENSION)

_SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS observation (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  dimension      TEXT NOT NULL,
  case_id        INTEGER NOT NULL,
  protocol       TEXT NOT NULL,
  round          INTEGER NOT NULL,
  content_hash   TEXT NOT NULL,
  kind           TEXT,
  code           TEXT,
  slot_name      TEXT,
  facts_json     TEXT NOT NULL DEFAULT '{}',
  message        TEXT,
  content        TEXT,
  source_batch   TEXT NOT NULL,
  source_round   INTEGER NOT NULL,
  run_id         TEXT,
  observed_at    TEXT NOT NULL,
  model          TEXT,
  nms            INTEGER NOT NULL DEFAULT 0,
  consistent     INTEGER NOT NULL,
  seen_count     INTEGER NOT NULL DEFAULT 1,
  first_seen     TEXT NOT NULL,
  last_seen      TEXT NOT NULL,
  UNIQUE (dimension, case_id, protocol, round, content_hash, source_batch, source_round, run_id)
);
CREATE INDEX IF NOT EXISTS idx_observation_target
  ON observation(dimension, case_id, protocol, round);

CREATE TABLE IF NOT EXISTS target (
  case_id     INTEGER NOT NULL,
  protocol    TEXT NOT NULL,
  dimension   TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',
  mode        TEXT,
  note        TEXT,
  confirmed_by TEXT,
  confirmed_at TEXT,
  applied_at  TEXT,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (case_id, protocol, dimension)
);

CREATE TABLE IF NOT EXISTS choice (
  case_id       INTEGER NOT NULL,
  protocol      TEXT NOT NULL,
  dimension     TEXT NOT NULL,
  observation_id INTEGER NOT NULL,
  accepted      INTEGER NOT NULL,
  edited_text   TEXT,
  created_at    TEXT NOT NULL,
  PRIMARY KEY (case_id, protocol, dimension, observation_id)
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
class TargetKey:
    """The identity of one annotatable expectation: one case, one protocol, one dimension."""

    case_id: int
    protocol: str
    dimension: str

    @property
    def text(self) -> str:
        """Return the stable key used by the audit table."""
        return f"{self.case_id}:{self.protocol}:{self.dimension}"


def now_iso() -> str:
    """Return the current UTC time in ISO 8601 form."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class AnnotationStore:
    """SQLite-backed store of the annotation console."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        """Return the database path (also used by the page to explain where drafts live)."""
        return self._path

    def ensure(self) -> None:
        """Create the database and its schema when missing."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as connection:
            connection.executescript(_SCHEMA)
            connection.execute("PRAGMA user_version=1")

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest(self, observations: Iterable[Mapping[str, object]]) -> int:
        """Insert the candidate observations, counting one already ingested only once."""
        inserted = 0
        stamp = now_iso()
        with self._session() as connection:
            for observation in observations:
                digest = _observation_hash(observation)
                cursor = connection.execute(
                    """
                    INSERT INTO observation
                        (dimension, case_id, protocol, round, content_hash, kind, code, slot_name,
                         facts_json, message, content, source_batch, source_round, run_id,
                         observed_at, model, nms, consistent, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT
                        (dimension, case_id, protocol, round, content_hash, source_batch,
                         source_round, run_id)
                    DO UPDATE SET
                        seen_count = observation.seen_count + 1,
                        last_seen = excluded.last_seen,
                        observed_at = excluded.observed_at
                    """,
                    (
                        str(observation.get("dimension") or ""),
                        as_int(observation.get("caseId")),
                        str(observation.get("protocol") or ""),
                        as_int(observation.get("round")),
                        digest,
                        observation.get("kind"),
                        observation.get("code"),
                        observation.get("slotName"),
                        json.dumps(observation.get("facts") or {}, ensure_ascii=False),
                        observation.get("message"),
                        observation.get("content"),
                        str(observation.get("sourceBatch") or ""),
                        as_int(observation.get("sourceRound"), 1),
                        observation.get("runId"),
                        str(observation.get("observedAt") or ""),
                        observation.get("model"),
                        1 if observation.get("nms") else 0,
                        1 if observation.get("consistent") else 0,
                        stamp,
                        stamp,
                    ),
                )
                inserted += int(cursor.rowcount > 0)
        return inserted

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def targets(self) -> list[dict[str, object]]:
        """Return every target the store knows about, with its candidate statistics.

        A target exists as soon as a candidate is ingested, so the list is the union of the
        ``target`` rows (which carry the confirmation state) and the observations.
        """
        with self._session() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT o.dimension, o.case_id, o.protocol,
                       COUNT(*) AS observations,
                       SUM(o.consistent) AS consistent_observations,
                       MAX(o.round) AS max_round,
                       COUNT(DISTINCT o.run_id) AS runs,
                       (SELECT COUNT(*) FROM choice c
                         WHERE c.case_id=o.case_id AND c.protocol=o.protocol
                           AND c.dimension=o.dimension AND c.accepted=1) AS accepted,
                       t.status, t.mode, t.note, t.confirmed_by, t.confirmed_at, t.applied_at
                FROM observation o
                LEFT JOIN target t
                  ON t.case_id=o.case_id AND t.protocol=o.protocol AND t.dimension=o.dimension
                GROUP BY o.dimension, o.case_id, o.protocol
                ORDER BY o.case_id ASC, o.protocol ASC, o.dimension ASC
                """
            ).fetchall()
        return [
            {
                "caseId": int(row["case_id"]),
                "protocol": str(row["protocol"]),
                "dimension": str(row["dimension"]),
                "observations": int(row["observations"] or 0),
                "consistentObservations": int(row["consistent_observations"] or 0),
                "maxRound": int(row["max_round"] or 0),
                "runs": int(row["runs"] or 0),
                "accepted": int(row["accepted"] or 0),
                "status": str(row["status"] or TARGET_PENDING),
                "mode": row["mode"],
                "note": row["note"],
                "confirmedBy": row["confirmed_by"],
                "confirmedAt": row["confirmed_at"],
                "appliedAt": row["applied_at"],
            }
            for row in rows
        ]

    def detail(
        self,
        key: TargetKey,
        *,
        consistent_only: bool = False,
    ) -> dict[str, object]:
        """Return the candidates and the accepted choices of one target."""
        clause = " AND consistent=1" if consistent_only else ""
        with self._session() as connection:
            connection.row_factory = sqlite3.Row
            observations = connection.execute(
                f"""
                SELECT * FROM observation
                WHERE dimension=? AND case_id=? AND protocol=?{clause}
                ORDER BY round ASC, seen_count DESC, observed_at DESC
                """,
                (key.dimension, key.case_id, key.protocol),
            ).fetchall()
            choices = connection.execute(
                """
                SELECT * FROM choice
                WHERE case_id=? AND protocol=? AND dimension=?
                ORDER BY observation_id ASC
                """,
                (key.case_id, key.protocol, key.dimension),
            ).fetchall()
            target = connection.execute(
                """
                SELECT * FROM target WHERE case_id=? AND protocol=? AND dimension=?
                """,
                (key.case_id, key.protocol, key.dimension),
            ).fetchone()
        return {
            "target": _target_row(target) if target is not None else _empty_target(key),
            "observations": [_observation_row(row) for row in observations],
            "choices": [_choice_row(row) for row in choices],
        }

    def progress(self) -> dict[str, int]:
        """Return the number of targets per status, for the console header."""
        counts = {status: 0 for status in TARGETS}
        with self._session() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT COALESCE(t.status, 'pending') AS status, COUNT(DISTINCT
                         o.case_id || ':' || o.protocol || ':' || o.dimension) AS total
                FROM observation o
                LEFT JOIN target t
                  ON t.case_id=o.case_id AND t.protocol=o.protocol AND t.dimension=o.dimension
                GROUP BY COALESCE(t.status, 'pending')
                """
            ).fetchall()
        for row in rows:
            counts[str(row["status"])] = int(row["total"] or 0)
        counts["total"] = sum(counts[status] for status in TARGETS)
        return counts

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def set_choice(
        self,
        key: TargetKey,
        observation_id: int,
        *,
        accepted: bool,
        edited_text: str | None = None,
        actor: str = "",
    ) -> None:
        """Accept, edit or reject one candidate of a target."""
        stamp = now_iso()
        with self._session() as connection:
            connection.execute(
                """
                INSERT INTO choice
                    (case_id, protocol, dimension, observation_id, accepted, edited_text,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (case_id, protocol, dimension, observation_id)
                DO UPDATE SET
                    accepted=excluded.accepted,
                    edited_text=excluded.edited_text,
                    created_at=excluded.created_at
                """,
                (
                    key.case_id,
                    key.protocol,
                    key.dimension,
                    observation_id,
                    1 if accepted else 0,
                    edited_text,
                    stamp,
                ),
            )
            self._touch_target(connection, key, status=TARGET_DRAFT, stamp=stamp)
            self._record(connection, actor, "choice", key, {"observationId": observation_id,
                                                            "accepted": accepted})
        self._refresh_status(key)

    def set_mode(self, key: TargetKey, mode: str, *, actor: str = "") -> None:
        """Set the matching mode of a slot-error target."""
        stamp = now_iso()
        with self._session() as connection:
            self._touch_target(connection, key, mode=mode, stamp=stamp)
            self._record(connection, actor, "mode", key, {"mode": mode})
        self._refresh_status(key)

    def set_note(self, key: TargetKey, note: str, *, actor: str = "") -> None:
        """Record a free-form note on one target."""
        stamp = now_iso()
        with self._session() as connection:
            self._touch_target(connection, key, note=note, stamp=stamp)
            self._record(connection, actor, "note", key, {"note": note})
        self._refresh_status(key)

    def confirm(self, key: TargetKey, *, actor: str = "") -> dict[str, object]:
        """Confirm the accepted choices of one target."""
        stamp = now_iso()
        with self._session() as connection:
            self._touch_target(
                connection,
                key,
                status=TARGET_CONFIRMED,
                confirmed_by=actor,
                confirmed_at=stamp,
                stamp=stamp,
            )
            self._record(connection, actor, "confirm", key, {})
        return {"status": TARGET_CONFIRMED, "confirmedAt": stamp, "confirmedBy": actor}

    def mark_applied(self, keys: Sequence[TargetKey], *, actor: str = "") -> None:
        """Mark the confirmed targets whose expectations were written to the corpus."""
        stamp = now_iso()
        with self._session() as connection:
            for key in keys:
                self._touch_target(connection, key, status=TARGET_APPLIED, applied_at=stamp,
                                   stamp=stamp)
                self._record(connection, actor, "apply", key, {"appliedAt": stamp})

    def export_payload(self, *, confirmed_only: bool = True) -> dict[str, object]:
        """Return the case-level expectation payload of the confirmed targets."""
        cases: dict[int, dict[str, object]] = {}
        with self._session() as connection:
            connection.row_factory = sqlite3.Row
            targets = connection.execute(
                "SELECT * FROM target ORDER BY case_id ASC"
            ).fetchall()
            for target in targets:
                key = TargetKey(
                    int(target["case_id"]),
                    str(target["protocol"]),
                    str(target["dimension"]),
                )
                status = str(target["status"] or TARGET_PENDING)
                if confirmed_only and status not in {TARGET_CONFIRMED, TARGET_APPLIED}:
                    continue
                value = self._value(connection, key, target)
                if value is None:
                    continue
                entry = cases.setdefault(key.case_id, {})
                protocols = entry.setdefault(key.protocol, {})
                if not isinstance(protocols, dict):
                    continue
                if key.dimension == SLOT_DIMENSION:
                    protocols["slot_errors"] = value
                else:
                    protocols["messages"] = value
        return {
            "schema": "osp-annotations/v1",
            "generatedAt": now_iso(),
            "cases": [
                {"id": case_id, **protocols}
                for case_id, protocols in sorted(cases.items())
            ],
        }

    def _value(
        self,
        connection: sqlite3.Connection,
        key: TargetKey,
        target: sqlite3.Row,
    ) -> object | None:
        """Build the YAML value of one target from its accepted choices."""
        rows = connection.execute(
            """
            SELECT c.accepted, c.edited_text, o.*
            FROM choice c
            JOIN observation o ON o.id = c.observation_id
            WHERE c.case_id=? AND c.protocol=? AND c.dimension=? AND c.accepted=1
            ORDER BY o.round ASC, o.seen_count DESC, o.id ASC
            """,
            (key.case_id, key.protocol, key.dimension),
        ).fetchall()
        if not rows:
            return None
        if key.dimension == SLOT_DIMENSION:
            matchers: list[dict[str, object]] = []
            for row in rows:
                matcher: dict[str, object] = {"code": str(row["code"] or "")}
                keywords = _keywords(row["edited_text"])
                if keywords:
                    matcher["contains"] = keywords
                matchers.append(matcher)
            mode = str(target["mode"] or SLOT_MODE_ANY)
            return {"mode": mode, "expect": matchers}
        rounds: dict[int, list[str]] = {}
        for row in rows:
            text = str(row["edited_text"] or row["content"] or "")
            if not text.strip():
                continue
            rounds.setdefault(int(row["round"] or 0), []).append(text)
        if not rounds:
            return None
        highest = max(rounds)
        return [rounds.get(round_number, []) for round_number in range(1, highest + 1)]

    def _refresh_status(self, key: TargetKey) -> None:
        """Downgrade a confirmed target when its choices changed after the confirmation."""
        with self._session() as connection:
            connection.execute(
                """
                UPDATE target SET status=?, confirmed_by=NULL, confirmed_at=NULL, updated_at=?
                WHERE case_id=? AND protocol=? AND dimension=? AND status=?
                """,
                (
                    TARGET_DRAFT,
                    now_iso(),
                    key.case_id,
                    key.protocol,
                    key.dimension,
                    TARGET_CONFIRMED,
                ),
            )

    def _touch_target(
        self,
        connection: sqlite3.Connection,
        key: TargetKey,
        *,
        status: str | None = None,
        mode: str | None = None,
        note: str | None = None,
        confirmed_by: str | None = None,
        confirmed_at: str | None = None,
        applied_at: str | None = None,
        stamp: str,
    ) -> None:
        """Create or update one target row, keeping the columns the caller did not set.

        A missing column value means "leave it alone", which is why this is an insert-or-ignore
        followed by a targeted update: a single upsert would have to write a placeholder status and
        then distinguish it from a real one.
        """
        connection.execute(
            """
            INSERT OR IGNORE INTO target
                (case_id, protocol, dimension, status, mode, note, confirmed_by, confirmed_at,
                 applied_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key.case_id,
                key.protocol,
                key.dimension,
                status or TARGET_PENDING,
                mode,
                note,
                confirmed_by,
                confirmed_at,
                applied_at,
                stamp,
            ),
        )
        assignments = ["updated_at=?"]
        params: list[object] = [stamp]
        for column, value in (
            ("status", status),
            ("mode", mode),
            ("note", note),
            ("confirmed_by", confirmed_by),
            ("confirmed_at", confirmed_at),
            ("applied_at", applied_at),
        ):
            if value is not None:
                assignments.append(f"{column}=?")
                params.append(value)
        params.extend([key.case_id, key.protocol, key.dimension])
        connection.execute(
            f"UPDATE target SET {', '.join(assignments)} "  # noqa: S608 - fixed column names
            "WHERE case_id=? AND protocol=? AND dimension=?",
            params,
        )

    def _record(
        self,
        connection: sqlite3.Connection,
        actor: str,
        action: str,
        key: TargetKey,
        payload: Mapping[str, object],
    ) -> None:
        connection.execute(
            "INSERT INTO event (at, actor, action, target_key, payload) VALUES (?, ?, ?, ?, ?)",
            (now_iso(), actor, action, key.text, json.dumps(payload, ensure_ascii=False)),
        )

    @contextmanager
    def _session(self) -> Generator[sqlite3.Connection, None, None]:
        """Open one short-lived connection, committing on success and always closing it."""
        connection = sqlite3.connect(self._path, timeout=10.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


def as_int(value: object, default: int = 0) -> int:
    """Return ``value`` as an integer, or ``default`` when it is not one.

    The observation mappings come from JSON documents, so a numeric field may arrive as a string,
    a float or nothing at all; a bare ``int(x or 0)`` is both wrong for ``" 3 "`` and untypable.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return int(text)
    return default


def _observation_hash(observation: Mapping[str, object]) -> str:
    """Return the stable digest that identifies one observed value inside a target."""
    payload = {
        "code": observation.get("code"),
        "slotName": observation.get("slotName"),
        "facts": observation.get("facts") or {},
        "content": _normalize_text(observation.get("content")),
    }
    return hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _normalize_text(value: object) -> str:
    """Normalize the message text so whitespace differences are not separate candidates."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    return "\n".join(" ".join(line.split()) for line in text.splitlines()).strip()


def _keywords(edited: object) -> list[str]:
    """Split the edited keyword list of a slot choice ("gain, 增益" → two keywords)."""
    if not isinstance(edited, str):
        return []
    parts = [part.strip() for part in edited.replace("，", ",").split(",")]
    return [part for part in parts if part]


def _target_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "caseId": int(row["case_id"]),
        "protocol": str(row["protocol"]),
        "dimension": str(row["dimension"]),
        "status": str(row["status"] or TARGET_PENDING),
        "mode": row["mode"],
        "note": row["note"],
        "confirmedBy": row["confirmed_by"],
        "confirmedAt": row["confirmed_at"],
        "appliedAt": row["applied_at"],
        "updatedAt": row["updated_at"],
    }


def _empty_target(key: TargetKey) -> dict[str, object]:
    return {
        "status": TARGET_PENDING,
        "mode": None,
        "note": None,
        "confirmedBy": None,
        "confirmedAt": None,
        "appliedAt": None,
        "updatedAt": None,
        "caseId": key.case_id,
        "protocol": key.protocol,
        "dimension": key.dimension,
    }


def _observation_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "round": int(row["round"] or 0),
        "kind": row["kind"],
        "code": row["code"],
        "slotName": row["slot_name"],
        "facts": json.loads(row["facts_json"] or "{}"),
        "message": row["message"],
        "content": row["content"],
        "sourceBatch": str(row["source_batch"]),
        "sourceRound": int(row["source_round"] or 1),
        "runId": row["run_id"],
        "observedAt": str(row["observed_at"] or ""),
        "model": row["model"],
        "nms": bool(row["nms"]),
        "consistent": bool(row["consistent"]),
        "seenCount": int(row["seen_count"] or 1),
    }


def _choice_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "observationId": int(row["observation_id"]),
        "accepted": bool(row["accepted"]),
        "editedText": row["edited_text"],
        "createdAt": str(row["created_at"]),
    }


__all__ = [
    "AnnotationStore",
    "DIMENSIONS",
    "TARGETS",
    "TARGET_APPLIED",
    "TARGET_CONFIRMED",
    "TARGET_DRAFT",
    "TARGET_PENDING",
    "TargetKey",
    "as_int",
    "now_iso",
]
