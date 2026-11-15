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
#: Statuses of one subtask. There is no "applied" state: the console only exports, because the
#: yellow zone hot-replaces the case files and a direct write would be overwritten.
TARGETS: Final[tuple[str, ...]] = (
    TARGET_PENDING,
    TARGET_DRAFT,
    TARGET_CONFIRMED,
)

#: Dimensions this console annotates.
DIMENSIONS: Final[tuple[str, ...]] = (SLOT_DIMENSION, MESSAGE_DIMENSION)

#: SQLite binds at most 999 parameters; the trace lookup goes in chunks of this size.
_TRACE_CHUNK: Final[int] = 400

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
  trace_id       TEXT,
  observed_at    TEXT NOT NULL,
  model          TEXT,
  nms            INTEGER NOT NULL DEFAULT 0,
  decision_mode  TEXT NOT NULL DEFAULT '',
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
        """Create the database and its schema, upgrading an older build's database in place."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as connection:
            connection.executescript(_SCHEMA)
            _ensure_columns(connection)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_observation_trace ON observation(trace_id)"
            )
            connection.execute("PRAGMA user_version=2")

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
                         trace_id, observed_at, model, nms, decision_mode, consistent,
                         first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT
                        (dimension, case_id, protocol, round, content_hash, source_batch,
                         source_round, run_id)
                    DO UPDATE SET
                        seen_count = observation.seen_count + 1,
                        last_seen = excluded.last_seen,
                        observed_at = excluded.observed_at,
                        -- Metadata the page filters on may have been ingested wrong (or absent)
                        -- by an older build; a rescan must repair it instead of keeping the
                        -- stale value forever.
                        trace_id = CASE WHEN excluded.trace_id != ''
                                        THEN excluded.trace_id ELSE observation.trace_id END,
                        model = COALESCE(excluded.model, observation.model),
                        nms = excluded.nms,
                        decision_mode = excluded.decision_mode,
                        consistent = excluded.consistent
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
                        str(observation.get("traceId") or ""),
                        str(observation.get("observedAt") or ""),
                        observation.get("model"),
                        1 if observation.get("nms") else 0,
                        str(observation.get("decisionMode") or ""),
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
                       t.status, t.mode, t.note, t.confirmed_by, t.confirmed_at
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

    def observations_by_traces(self, traces: Iterable[str]) -> list[dict[str, object]]:
        """Return the observations carrying one of the given traceIds or runIds.

        The console's bulk adoption starts from identifiers an operator has already verified
        (the trace/run id of a good live run), so the lookup accepts either: a tester item stores
        the traceId of its run, while the run console and the wire logs are keyed by runId.
        """
        tokens = sorted({str(token).strip().lower() for token in traces if str(token).strip()})
        rows: list[sqlite3.Row] = []
        with self._session() as connection:
            connection.row_factory = sqlite3.Row
            for start in range(0, len(tokens), _TRACE_CHUNK):
                chunk = tokens[start : start + _TRACE_CHUNK]
                marks = ",".join("?" for _ in chunk)
                rows.extend(
                    connection.execute(
                        f"""
                        SELECT * FROM observation
                        WHERE trace_id IN ({marks}) OR lower(run_id) IN ({marks})
                        ORDER BY observed_at DESC, seen_count DESC, id DESC
                        """,
                        (*chunk, *chunk),
                    ).fetchall()
                )
        return [_observation_row(row) for row in rows]

    def value(self, key: TargetKey) -> object | None:
        """Return the value one target would export, without building the whole payload.

        The per-subtask page asks for this on every click; building the global payload meant
        reading and rendering every confirmed target of every case, which is what made selecting a
        case feel slow once the corpus had evidence.
        """
        with self._session() as connection:
            connection.row_factory = sqlite3.Row
            target = connection.execute(
                "SELECT * FROM target WHERE case_id=? AND protocol=? AND dimension=?",
                (key.case_id, key.protocol, key.dimension),
            ).fetchone()
            if target is None:
                return None
            return self._value(connection, key, target)

    def accepted_ids(self, key: TargetKey) -> list[int]:
        """Return the candidates currently accepted for one target."""
        with self._session() as connection:
            rows = connection.execute(
                """
                SELECT observation_id FROM choice
                WHERE case_id=? AND protocol=? AND dimension=? AND accepted=1
                ORDER BY observation_id ASC
                """,
                (key.case_id, key.protocol, key.dimension),
            ).fetchall()
        return [int(row[0]) for row in rows]

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

    def apply(
        self,
        key: TargetKey,
        observation_ids: Sequence[int],
        *,
        mode: str | None = None,
        note: str | None = None,
        edits: Mapping[int, str] | None = None,
        confirm: bool = False,
        actor: str = "",
    ) -> int:
        """Replace the accepted set of one target with exactly the given candidates.

        The page edits every subtask of a case and saves once, so the request carries the full
        selection rather than one checkbox at a time: anything accepted before but absent now is
        rejected. Ids that do not belong to this target are dropped instead of accepted, because a
        client-side mistake must not let one case's evidence become another case's oracle.
        """
        requested = {int(value) for value in observation_ids}
        stamp = now_iso()
        with self._session() as connection:
            keep = self._owned_ids(connection, key, requested)
            if keep:
                marks = ",".join("?" for _ in keep)
                connection.execute(
                    f"""
                    UPDATE choice SET accepted=0
                    WHERE case_id=? AND protocol=? AND dimension=?
                      AND observation_id NOT IN ({marks})
                    """,
                    (key.case_id, key.protocol, key.dimension, *keep),
                )
            else:
                connection.execute(
                    "UPDATE choice SET accepted=0 "
                    "WHERE case_id=? AND protocol=? AND dimension=?",
                    (key.case_id, key.protocol, key.dimension),
                )
            for observation_id in keep:
                edited = (edits or {}).get(observation_id)
                connection.execute(
                    """
                    INSERT INTO choice
                        (case_id, protocol, dimension, observation_id, accepted, edited_text,
                         created_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT (case_id, protocol, dimension, observation_id)
                    DO UPDATE SET
                        accepted=1,
                        edited_text=COALESCE(excluded.edited_text, choice.edited_text)
                    """,
                    (
                        key.case_id,
                        key.protocol,
                        key.dimension,
                        observation_id,
                        edited if isinstance(edited, str) else None,
                        stamp,
                    ),
                )
            self._touch_target(connection, key, status=TARGET_DRAFT, mode=mode, note=note,
                               stamp=stamp)
            self._record(
                connection,
                actor,
                "apply",
                key,
                {"observationIds": list(keep), "mode": mode, "note": note, "confirm": confirm},
            )
        if confirm and keep:
            self.confirm(key, actor=actor)
        return len(keep)

    def adopt(
        self,
        key: TargetKey,
        observation_ids: Sequence[int],
        *,
        replace: bool = False,
        confirm: bool = False,
        actor: str = "",
    ) -> int:
        """Accept a batch of candidates of one target, optionally confirming the result.

        This is the bulk form of :meth:`set_choice`, used by the trace-list adoption: the operator
        supplied a trace list, which is the review step. ``replace`` keeps the documented meaning —
        without it the previously accepted candidates are kept and the new ones are appended.
        """
        ids = {int(value) for value in observation_ids}
        if not ids:
            return 0
        if not replace:
            ids |= set(self.accepted_ids(key))
        return self.apply(key, sorted(ids), confirm=confirm, actor=actor)

    def _owned_ids(
        self,
        connection: sqlite3.Connection,
        key: TargetKey,
        ids: set[int],
    ) -> list[int]:
        """Return the given observation ids that belong to one target, sorted."""
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"""
            SELECT id FROM observation
            WHERE dimension=? AND case_id=? AND protocol=? AND id IN ({marks})
            """,
            (key.dimension, key.case_id, key.protocol, *sorted(ids)),
        ).fetchall()
        return sorted(int(row[0]) for row in rows)

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
                if confirmed_only and status != TARGET_CONFIRMED:
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
                # Adopting a slot error keeps the whole observed error, not just its code: the
                # code alone cannot tell "gain target rejected" from any other constraint
                # violation of the same case. The author can still delete fields in the case.
                matcher: dict[str, object] = {"code": str(row["code"] or "")}
                slot_name = str(row["slot_name"] or "")
                if slot_name:
                    matcher["slotName"] = slot_name
                message = str(row["message"] or "")
                if message:
                    matcher["message"] = message
                facts = _facts(row["facts_json"])
                if facts:
                    matcher["facts"] = facts
                keywords = _keywords(row["edited_text"])
                if keywords:
                    matcher["contains"] = keywords
                # Two observations of the same error differ only in the facts the matcher ignores,
                # so the same entry must not be emitted twice.
                if matcher not in matchers:
                    matchers.append(matcher)
            mode = str(target["mode"] or SLOT_MODE_ANY)
            return {"mode": mode, "expect": matchers}
        rounds: dict[int, list[str]] = {}
        for row in rows:
            text = str(row["edited_text"] or row["content"] or "")
            if not text.strip():
                continue
            alternatives = rounds.setdefault(int(row["round"] or 0), [])
            if text not in alternatives:
                alternatives.append(text)
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
                 updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        # The message is part of the exported matcher now, so two renders that differ only in
        # wording are two candidates, not one candidate seen twice.
        "message": _normalize_text(observation.get("message")),
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


def _facts(raw: object) -> dict[str, str]:
    """Return the stored facts of one observation as the string mapping a matcher expects."""
    try:
        facts = json.loads(str(raw or "{}"))
    except ValueError:
        return {}
    if not isinstance(facts, Mapping):
        return {}
    return {str(key): str(value) for key, value in facts.items() if value is not None}


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
        "updatedAt": row["updated_at"],
    }


def _empty_target(key: TargetKey) -> dict[str, object]:
    return {
        "status": TARGET_PENDING,
        "mode": None,
        "note": None,
        "confirmedBy": None,
        "confirmedAt": None,
        "updatedAt": None,
        "caseId": key.case_id,
        "protocol": key.protocol,
        "dimension": key.dimension,
    }


def _ensure_columns(connection: sqlite3.Connection) -> None:
    """Add the columns a database created by an older build of the console lacks."""
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(observation)")}
    if "trace_id" not in columns:
        connection.execute("ALTER TABLE observation ADD COLUMN trace_id TEXT")
    if "decision_mode" not in columns:
        connection.execute(
            "ALTER TABLE observation ADD COLUMN decision_mode TEXT NOT NULL DEFAULT ''"
        )


def _observation_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "caseId": int(row["case_id"]),
        "protocol": str(row["protocol"]),
        "dimension": str(row["dimension"]),
        "contentHash": str(row["content_hash"]),
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
        "traceId": row["trace_id"],
        "observedAt": str(row["observed_at"] or ""),
        "model": row["model"],
        "nms": bool(row["nms"]),
        "decisionMode": str(row["decision_mode"] or ""),
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
    "TARGET_CONFIRMED",
    "TARGET_DRAFT",
    "TARGET_PENDING",
    "TargetKey",
    "as_int",
    "now_iso",
]
