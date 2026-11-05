"""SQLite persistence of the client agent's conversations, runs and negotiations.

The client and the RAN server are two systems with two databases:

* the server owns the A2A ``Task`` history (:mod:`protocol.sqlite_task_store`);
* the client owns the conversation, the run lifecycle and the pre-task negotiation audit
  (this module).

The client database is deliberately small and queryable: full transcripts and trace spans stay in
the ``runs/<runId>/`` files, while this store keeps the rows the Web UI, the A2A gateway and a
production console need to list, resume-by-inspection and audit a run.

The implementation uses the standard library only (WAL + busy timeout + one connection per
operation), matching :class:`protocol.sqlite_task_store.SqliteTaskStore`. Persistence failures are
logged and never break a client run.
"""

from __future__ import annotations

import asyncio
import builtins
import ctypes
import json
import logging
import os
import sqlite3
import uuid
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

from shared.run_constants import PROTOCOL_A2A_T
from shared.sqlite import SchemaMigration, apply_migrations, connect

_LOGGER = logging.getLogger("ran_energy_saving.client_store")


#: Longest user request/title shown in list responses.
SUMMARY_LIMIT: Final[int] = 200

#: Run statuses owned by the client.
RUN_STATUS_RUNNING: Final[str] = "running"
RUN_STATUS_FINISHED: Final[str] = "finished"
RUN_STATUS_FAILED: Final[str] = "failed"
RUN_STATUS_INTERRUPTED: Final[str] = "interrupted"
RUN_STATUSES: Final[tuple[str, ...]] = (
    RUN_STATUS_RUNNING,
    RUN_STATUS_FINISHED,
    RUN_STATUS_FAILED,
    RUN_STATUS_INTERRUPTED,
)

#: Turn roles.
TURN_ROLE_USER: Final[str] = "user"
TURN_ROLE_ASSISTANT: Final[str] = "assistant"
TURN_ROLE_SYSTEM: Final[str] = "system"

#: Negotiation event directions.
NEGOTIATION_DIRECTION_INBOUND: Final[str] = "inbound"
NEGOTIATION_DIRECTION_OUTBOUND: Final[str] = "outbound"

#: Per-run comparison metrics (the A2A-T vs pure A2A analysis carrier).
_RUN_METRICS_SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS run_metrics (
  run_id            TEXT PRIMARY KEY,
  conversation_id   TEXT,
  protocol          TEXT NOT NULL,
  scenario          TEXT NOT NULL,
  mode              TEXT NOT NULL,
  transport         TEXT NOT NULL,
  language          TEXT NOT NULL,
  model_mode        TEXT NOT NULL,
  status            TEXT NOT NULL,
  final_state       TEXT,
  passed            INTEGER,
  accuracy_score    REAL,
  duration_ms       REAL,
  model_ms          REAL,
  tool_ms           REAL,
  approval_wait_ms  REAL,
  prompt_tokens     INTEGER,
  completion_tokens INTEGER,
  total_tokens      INTEGER,
  llm_calls         INTEGER,
  token_available   INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  metric_json       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_run_metrics_group
  ON run_metrics(protocol, scenario, created_at DESC);
"""

_SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS conversations (
  conversation_id  TEXT PRIMARY KEY,
  external_id      TEXT NOT NULL DEFAULT '',
  user_id          TEXT NOT NULL DEFAULT '',
  title            TEXT NOT NULL DEFAULT '',
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  metadata_json    TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS turns (
  turn_id          TEXT PRIMARY KEY,
  conversation_id  TEXT NOT NULL,
  seq              INTEGER NOT NULL,
  role             TEXT NOT NULL,
  content          TEXT NOT NULL,
  run_id           TEXT,
  created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_conversation ON turns(conversation_id, seq);

CREATE TABLE IF NOT EXISTS runs (
  run_id            TEXT PRIMARY KEY,
  conversation_id   TEXT,
  turn_id           TEXT,
  scenario          TEXT NOT NULL,
  mode              TEXT NOT NULL,
  transport         TEXT NOT NULL,
  discovery         TEXT NOT NULL,
  protocol          TEXT NOT NULL DEFAULT 'a2a-t',
  user_request      TEXT NOT NULL,
  status            TEXT NOT NULL,
  final_state       TEXT,
  server_context_id TEXT,
  server_task_id    TEXT,
  error             TEXT,
  owner_pid         INTEGER NOT NULL DEFAULT 0,
  idempotency_key   TEXT NOT NULL DEFAULT '',
  transcript_json   TEXT,
  transcript_markdown TEXT,
  trace_json        TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  finished_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_conversation ON runs(conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_idempotency
  ON runs(conversation_id, idempotency_key) WHERE idempotency_key <> '';

CREATE TABLE IF NOT EXISTS negotiation_events (
  event_id          TEXT PRIMARY KEY,
  run_id            TEXT NOT NULL,
  seq               INTEGER NOT NULL,
  negotiation_id    TEXT,
  kind              TEXT,
  round             INTEGER,
  performative      TEXT,
  direction         TEXT NOT NULL,
  actor             TEXT NOT NULL,
  content_json      TEXT NOT NULL DEFAULT '{}',
  decision_action   TEXT,
  rationale         TEXT,
  created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_negotiation_run ON negotiation_events(run_id, seq);

CREATE TABLE IF NOT EXISTS task_snapshots (
  run_id       TEXT PRIMARY KEY,
  task_id      TEXT,
  context_id   TEXT,
  state        TEXT,
  updated_at   TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}'
);
""" + _RUN_METRICS_SCHEMA


def _create_schema(connection: sqlite3.Connection) -> None:
    """Create the version-1 client schema."""
    connection.executescript(_SCHEMA)


def _enforce_one_active_run(connection: sqlite3.Connection) -> None:
    """Add the partial unique index that guards one active run per conversation.

    A database written before this migration may already hold duplicate ``running`` rows created
    by two processes. The newest row of each conversation survives; older ones are marked
    ``interrupted`` so the index can be created.
    """
    connection.execute(
        """
        UPDATE runs
           SET status = 'interrupted',
               error = 'Superseded by a newer active run during the schema upgrade.',
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
               finished_at = COALESCE(
                   finished_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               )
         WHERE status = 'running'
           AND rowid NOT IN (
               SELECT MAX(rowid) FROM runs
                WHERE status = 'running'
                GROUP BY conversation_id
           )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_one_active
          ON runs(conversation_id) WHERE status = 'running'
        """
    )


def _create_run_metrics(connection: sqlite3.Connection) -> None:
    """Add the per-run metrics table to a database written by version 2."""
    connection.executescript(_RUN_METRICS_SCHEMA)


def _add_run_protocol(connection: sqlite3.Connection) -> None:
    """Persist the client request form so an idempotent replay keeps it.

    Existing rows were written by an A2A-T-only client, which is the correct historical default.
    """
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(runs)")}
    if "protocol" not in columns:
        connection.execute(
            "ALTER TABLE runs ADD COLUMN protocol TEXT NOT NULL DEFAULT 'a2a-t'"
        )


#: Ordered schema migrations; index ``i`` upgrades version ``i`` to ``i + 1``.
_MIGRATIONS: Final[tuple[SchemaMigration, ...]] = (
    _create_schema,
    _enforce_one_active_run,
    _create_run_metrics,
    _add_run_protocol,
)


#: Fields ``update_run`` may change (keeps the SQL dynamic but closed).
_RUN_UPDATE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "conversation_id",
        "turn_id",
        "status",
        "final_state",
        "server_context_id",
        "server_task_id",
        "error",
        "transcript_json",
        "transcript_markdown",
        "trace_json",
        "finished_at",
    }
)


def now_iso() -> str:
    """Return the current UTC time in ISO 8601 form."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _pid_alive(pid: int) -> bool:
    """Return whether one process is still running (used to sweep stale client runs)."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        process_handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # type: ignore[attr-defined]
        if not process_handle:
            return False
        ctypes.windll.kernel32.CloseHandle(process_handle)  # type: ignore[attr-defined]
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _truncate(value: str, limit: int = SUMMARY_LIMIT) -> str:
    """Truncate one display string."""
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _section(value: object) -> dict[str, object]:
    """Return one nested metrics section as a plain mapping."""
    return dict(value) if isinstance(value, Mapping) else {}


def _optional_int(value: object) -> int | None:
    """Return an integer value, ``None`` when absent or not numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _optional_float(value: object) -> float | None:
    """Return a float value, ``None`` when absent or not numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _optional_bool(value: object) -> int | None:
    """Return a SQLite integer for a tri-state boolean (``None`` when not applicable)."""
    return 1 if value is True else 0 if value is False else None


def _dump_json(value: Mapping[str, object] | None) -> str:
    """Serialize one JSON object deterministically."""
    return json.dumps(dict(value or {}), ensure_ascii=False, sort_keys=True)


def _load_json(value: object) -> dict[str, object]:
    """Parse one JSON object column, returning an empty object for malformed values."""
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


@dataclass(frozen=True)
class ConversationRecord:
    """One client conversation."""

    conversation_id: str
    external_id: str = ""
    user_id: str = ""
    title: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnRecord:
    """One conversation turn."""

    turn_id: str
    conversation_id: str
    seq: int
    role: str
    content: str
    run_id: str | None = None
    created_at: str = field(default_factory=now_iso)


@dataclass(frozen=True)
class ClientRunRecord:
    """One client run."""

    run_id: str
    scenario: str
    mode: str
    transport: str
    discovery: str
    user_request: str
    status: str = RUN_STATUS_RUNNING
    conversation_id: str | None = None
    turn_id: str | None = None
    final_state: str | None = None
    server_context_id: str | None = None
    server_task_id: str | None = None
    error: str | None = None
    owner_pid: int = 0
    idempotency_key: str = ""
    transcript_json: str | None = None
    transcript_markdown: str | None = None
    trace_json: str | None = None
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    finished_at: str | None = None
    protocol: str = PROTOCOL_A2A_T


@dataclass(frozen=True)
class NegotiationEventRecord:
    """One inbound proposal or outbound decision of a run."""

    event_id: str
    run_id: str
    seq: int
    direction: str
    actor: str
    negotiation_id: str | None = None
    kind: str | None = None
    round: int | None = None
    performative: str | None = None
    content: dict[str, object] = field(default_factory=dict)
    decision_action: str | None = None
    rationale: str | None = None
    created_at: str = field(default_factory=now_iso)

    @classmethod
    def new(
        cls,
        *,
        run_id: str,
        seq: int,
        direction: str,
        actor: str,
        negotiation_id: str | None = None,
        kind: str | None = None,
        round: int | None = None,
        performative: str | None = None,
        content: Mapping[str, object] | None = None,
        decision_action: str | None = None,
        rationale: str | None = None,
    ) -> NegotiationEventRecord:
        """Build one event with a fresh id and timestamp."""
        return cls(
            event_id=uuid.uuid4().hex,
            run_id=run_id,
            seq=seq,
            direction=direction,
            actor=actor,
            negotiation_id=negotiation_id,
            kind=kind,
            round=round,
            performative=performative,
            content=dict(content or {}),
            decision_action=decision_action,
            rationale=rationale,
        )


@dataclass(frozen=True)
class TaskSnapshotRecord:
    """Last observed A2A Task state of one client run."""

    run_id: str
    task_id: str | None = None
    context_id: str | None = None
    state: str | None = None
    updated_at: str = field(default_factory=now_iso)
    payload: dict[str, object] = field(default_factory=dict)


class ClientStore(Protocol):
    """Persistence surface used by the client service."""

    async def ensure_conversation(
        self,
        *,
        conversation_id: str,
        external_id: str = "",
        user_id: str = "",
        title: str = "",
    ) -> ConversationRecord | None: ...

    async def get_conversation(self, conversation_id: str) -> ConversationRecord | None: ...

    async def append_turn(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        run_id: str | None = None,
    ) -> TurnRecord | None: ...

    async def list_turns(
        self,
        conversation_id: str,
        limit: int = 100,
    ) -> builtins.list[TurnRecord]: ...

    async def list_recent_turns(
        self,
        conversation_id: str,
        limit: int = 6,
    ) -> builtins.list[TurnRecord]: ...

    async def create_run(self, record: ClientRunRecord) -> None: ...

    async def claim_run(self, record: ClientRunRecord) -> None:
        """Persist one run, raising :class:`RunAlreadyActiveError` on a live conflict."""
        ...

    async def update_run(self, run_id: str, **fields: object) -> None: ...

    async def get_run(self, run_id: str) -> ClientRunRecord | None: ...

    async def find_run_by_idempotency(
        self,
        conversation_id: str,
        idempotency_key: str,
    ) -> ClientRunRecord | None: ...

    async def list_runs(
        self,
        limit: int = 50,
        conversation_id: str | None = None,
    ) -> builtins.list[ClientRunRecord]: ...

    async def record_negotiation_event(self, record: NegotiationEventRecord) -> None: ...

    async def list_negotiation_events(
        self,
        run_id: str,
    ) -> builtins.list[NegotiationEventRecord]: ...

    async def save_task_snapshot(self, record: TaskSnapshotRecord) -> None: ...

    async def get_task_snapshot(self, run_id: str) -> TaskSnapshotRecord | None: ...

    async def save_run_metrics(self, metrics: Mapping[str, object]) -> None:
        """Upsert the per-run comparison metrics of one run."""
        ...

    async def mark_running_interrupted(self) -> int: ...


class RunAlreadyActiveError(RuntimeError):
    """Raised when a conversation already has an active run owned by another process."""

    def __init__(
        self,
        conversation_id: str,
        run_id: str | None = None,
        idempotency_key: str = "",
    ) -> None:
        self.conversation_id = conversation_id
        self.run_id = run_id
        self.idempotency_key = idempotency_key
        suffix = f" ({run_id})" if run_id else ""
        super().__init__(f"Conversation {conversation_id} already has an active run{suffix}.")


class SqliteClientStore:
    """SQLite implementation of :class:`ClientStore`."""

    def __init__(self, path: str | Path) -> None:
        """Open (or create) the client database at ``path``.

        Args:
            path: Absolute SQLite file path.
        """
        self._path = Path(path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @property
    def path(self) -> Path:
        """Return the backing SQLite file path."""
        return self._path

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection]:
        """Yield one connection, commit on success, and always close it."""
        with connect(self._path) as connection:
            yield connection

    def _initialize(self) -> None:
        """Apply the schema migrations (WAL is enabled by the shared connection helper)."""
        with self._connection() as connection:
            apply_migrations(connection, _MIGRATIONS)

    async def ensure_conversation(
        self,
        *,
        conversation_id: str,
        external_id: str = "",
        user_id: str = "",
        title: str = "",
    ) -> ConversationRecord | None:
        """Create or update one conversation and return its current row."""
        try:
            await asyncio.to_thread(
                self._ensure_conversation,
                conversation_id,
                external_id,
                user_id,
                title,
            )
            return await self.get_conversation(conversation_id)
        except sqlite3.Error as exc:
            _LOGGER.warning("conversation persistence failed for %s: %s", conversation_id, exc)
            return None

    async def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        """Read one conversation."""
        try:
            return await asyncio.to_thread(self._get_conversation, conversation_id)
        except sqlite3.Error as exc:
            _LOGGER.warning("conversation read failed for %s: %s", conversation_id, exc)
            return None

    async def append_turn(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        run_id: str | None = None,
    ) -> TurnRecord | None:
        """Append one turn and return it (``None`` on a persistence error)."""
        try:
            return await asyncio.to_thread(
                self._append_turn,
                conversation_id,
                role,
                content,
                run_id,
            )
        except sqlite3.Error as exc:
            _LOGGER.warning("turn persistence failed for %s: %s", conversation_id, exc)
            return None

    async def list_turns(
        self,
        conversation_id: str,
        limit: int = 100,
    ) -> builtins.list[TurnRecord]:
        """Return the conversation turns in chronological order."""
        try:
            return await asyncio.to_thread(self._list_turns, conversation_id, limit, False)
        except sqlite3.Error as exc:
            _LOGGER.warning("turn list failed for %s: %s", conversation_id, exc)
            return []

    async def list_recent_turns(
        self,
        conversation_id: str,
        limit: int = 6,
    ) -> builtins.list[TurnRecord]:
        """Return the newest ``limit`` turns in chronological order."""
        try:
            rows = await asyncio.to_thread(self._list_turns, conversation_id, limit, True)
        except sqlite3.Error as exc:
            _LOGGER.warning("recent turn list failed for %s: %s", conversation_id, exc)
            return []
        return list(reversed(rows))

    async def create_run(self, record: ClientRunRecord) -> None:
        """Persist one new run."""
        try:
            await asyncio.to_thread(self._create_run, record)
        except sqlite3.Error as exc:
            _LOGGER.warning("run persistence failed for %s: %s", record.run_id, exc)

    async def claim_run(self, record: ClientRunRecord) -> None:
        """Persist one run, raising when the conversation already has an active run.

        The partial unique index ``idx_runs_one_active`` makes the claim atomic across processes.
        Other SQLite failures stay best-effort: the run continues without persistence rather than
        failing a decision.
        """
        try:
            await asyncio.to_thread(self._create_run, record)
        except sqlite3.IntegrityError as exc:
            existing = await asyncio.to_thread(
                self._find_active_run, record.conversation_id or ""
            )
            if existing is not None:
                raise RunAlreadyActiveError(
                    record.conversation_id or "",
                    existing.run_id,
                    existing.idempotency_key,
                ) from exc
            _LOGGER.warning("run claim failed for %s: %s", record.run_id, exc)
        except sqlite3.Error as exc:
            _LOGGER.warning("run persistence failed for %s: %s", record.run_id, exc)

    async def update_run(self, run_id: str, **fields: object) -> None:
        """Update the allowed mutable fields of one run."""
        unknown = sorted(set(fields) - _RUN_UPDATE_FIELDS)
        if unknown:
            raise ValueError(f"Unsupported run fields: {unknown}")
        try:
            await asyncio.to_thread(self._update_run, run_id, fields)
        except sqlite3.Error as exc:
            _LOGGER.warning("run update failed for %s: %s", run_id, exc)

    async def get_run(self, run_id: str) -> ClientRunRecord | None:
        """Read one run."""
        try:
            return await asyncio.to_thread(self._get_run, run_id)
        except sqlite3.Error as exc:
            _LOGGER.warning("run read failed for %s: %s", run_id, exc)
            return None

    async def find_run_by_idempotency(
        self,
        conversation_id: str,
        idempotency_key: str,
    ) -> ClientRunRecord | None:
        """Return the run created by one idempotency key, when it exists."""
        if not idempotency_key:
            return None
        try:
            return await asyncio.to_thread(
                self._find_run_by_idempotency, conversation_id, idempotency_key
            )
        except sqlite3.Error as exc:
            _LOGGER.warning("idempotency lookup failed: %s", exc)
            return None

    async def list_runs(
        self,
        limit: int = 50,
        conversation_id: str | None = None,
    ) -> builtins.list[ClientRunRecord]:
        """Return the newest runs, optionally scoped to one conversation."""
        try:
            return await asyncio.to_thread(self._list_runs, limit, conversation_id)
        except sqlite3.Error as exc:
            _LOGGER.warning("run list failed: %s", exc)
            return []

    async def record_negotiation_event(self, record: NegotiationEventRecord) -> None:
        """Persist one inbound proposal or outbound decision."""
        try:
            await asyncio.to_thread(self._record_negotiation_event, record)
        except sqlite3.Error as exc:
            _LOGGER.warning("negotiation persistence failed for %s: %s", record.run_id, exc)

    async def list_negotiation_events(
        self,
        run_id: str,
    ) -> builtins.list[NegotiationEventRecord]:
        """Return one run's negotiation events in order."""
        try:
            return await asyncio.to_thread(self._list_negotiation_events, run_id)
        except sqlite3.Error as exc:
            _LOGGER.warning("negotiation list failed for %s: %s", run_id, exc)
            return []

    async def save_task_snapshot(self, record: TaskSnapshotRecord) -> None:
        """Upsert the last observed Task state of one run."""
        try:
            await asyncio.to_thread(self._save_task_snapshot, record)
        except sqlite3.Error as exc:
            _LOGGER.warning("task snapshot failed for %s: %s", record.run_id, exc)

    async def get_task_snapshot(self, run_id: str) -> TaskSnapshotRecord | None:
        """Read the last observed Task state of one run."""
        try:
            return await asyncio.to_thread(self._get_task_snapshot, run_id)
        except sqlite3.Error as exc:
            _LOGGER.warning("task snapshot read failed for %s: %s", run_id, exc)
            return None

    async def save_run_metrics(self, metrics: Mapping[str, object]) -> None:
        """Upsert the per-run comparison metrics of one run."""
        try:
            await asyncio.to_thread(self._save_run_metrics, metrics)
        except sqlite3.Error as exc:
            _LOGGER.warning("run metrics persistence failed for %s: %s", metrics.get("runId"), exc)

    async def mark_running_interrupted(self) -> int:
        """Mark every run left ``running`` by a previous process as ``interrupted``."""
        try:
            return await asyncio.to_thread(self._mark_running_interrupted)
        except sqlite3.Error as exc:
            _LOGGER.warning("interrupted-run sweep failed: %s", exc)
            return 0

    # -- sqlite worker methods -------------------------------------------------

    def _ensure_conversation(
        self,
        conversation_id: str,
        external_id: str,
        user_id: str,
        title: str,
    ) -> None:
        stamp = now_iso()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO conversations
                    (conversation_id, external_id, user_id, title, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, '{}')
                ON CONFLICT(conversation_id) DO UPDATE SET
                    external_id=CASE WHEN excluded.external_id <> '' THEN excluded.external_id
                                     ELSE conversations.external_id END,
                    user_id=CASE WHEN excluded.user_id <> '' THEN excluded.user_id
                                 ELSE conversations.user_id END,
                    title=CASE WHEN excluded.title <> '' THEN excluded.title
                               ELSE conversations.title END,
                    updated_at=excluded.updated_at
                """,
                (
                    conversation_id,
                    external_id,
                    user_id,
                    _truncate(title, 120),
                    stamp,
                    stamp,
                ),
            )

    def _get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return ConversationRecord(
            conversation_id=str(row["conversation_id"]),
            external_id=str(row["external_id"]),
            user_id=str(row["user_id"]),
            title=str(row["title"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            metadata=_load_json(row["metadata_json"]),
        )

    def _append_turn(
        self,
        conversation_id: str,
        role: str,
        content: str,
        run_id: str | None,
    ) -> TurnRecord:
        turn_id = uuid.uuid4().hex
        stamp = now_iso()
        with self._connection() as connection:
            # The sequence read and the insert must form one write transaction; without the
            # immediate lock two concurrent appends can both observe the same MAX(seq).
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM turns WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            seq = int(row[0]) if row is not None else 1
            connection.execute(
                """
                INSERT INTO turns (turn_id, conversation_id, seq, role, content, run_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (turn_id, conversation_id, seq, role, content, run_id, stamp),
            )
            connection.execute(
                "UPDATE conversations SET updated_at=? WHERE conversation_id=?",
                (stamp, conversation_id),
            )
        return TurnRecord(
            turn_id=turn_id,
            conversation_id=conversation_id,
            seq=seq,
            role=role,
            content=content,
            run_id=run_id,
            created_at=stamp,
        )

    def _list_turns(
        self,
        conversation_id: str,
        limit: int,
        newest_first: bool,
    ) -> builtins.list[TurnRecord]:
        resolved_limit = max(1, min(limit, 500))
        order = "DESC" if newest_first else "ASC"
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"SELECT * FROM turns WHERE conversation_id=? ORDER BY seq {order} LIMIT ?",
                (conversation_id, resolved_limit),
            ).fetchall()
        return [
            TurnRecord(
                turn_id=str(row["turn_id"]),
                conversation_id=str(row["conversation_id"]),
                seq=int(row["seq"]),
                role=str(row["role"]),
                content=str(row["content"]),
                run_id=str(row["run_id"]) if row["run_id"] else None,
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def _find_active_run(self, conversation_id: str) -> ClientRunRecord | None:
        """Return the running row of one conversation, when it exists."""
        if not conversation_id:
            return None
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM runs WHERE conversation_id=? AND status=? "
                "ORDER BY created_at DESC LIMIT 1",
                (conversation_id, RUN_STATUS_RUNNING),
            ).fetchone()
        return self._row_to_run(row) if row is not None else None

    def _create_run(self, record: ClientRunRecord) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO runs
                    (run_id, conversation_id, turn_id, scenario, mode, transport, discovery,
                     protocol, user_request, status, final_state, server_context_id, server_task_id,
                     error, owner_pid, idempotency_key, transcript_json, transcript_markdown,
                     trace_json, created_at, updated_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.conversation_id,
                    record.turn_id,
                    record.scenario,
                    record.mode,
                    record.transport,
                    record.discovery,
                    record.protocol,
                    record.user_request,
                    record.status,
                    record.final_state,
                    record.server_context_id,
                    record.server_task_id,
                    record.error,
                    record.owner_pid,
                    record.idempotency_key,
                    record.transcript_json,
                    record.transcript_markdown,
                    record.trace_json,
                    record.created_at,
                    record.updated_at,
                    record.finished_at,
                ),
            )

    def _update_run(self, run_id: str, fields: Mapping[str, object]) -> None:
        if not fields:
            return
        columns = sorted(fields)
        assignments = ", ".join(f"{column}=?" for column in columns)
        values: builtins.list[object] = [fields[column] for column in columns]
        values.extend([now_iso(), run_id])
        with self._connection() as connection:
            connection.execute(
                f"UPDATE runs SET {assignments}, updated_at=? WHERE run_id=?",
                values,
            )

    def _row_to_run(self, row: sqlite3.Row) -> ClientRunRecord:
        return ClientRunRecord(
            run_id=str(row["run_id"]),
            conversation_id=str(row["conversation_id"]) if row["conversation_id"] else None,
            turn_id=str(row["turn_id"]) if row["turn_id"] else None,
            scenario=str(row["scenario"]),
            mode=str(row["mode"]),
            transport=str(row["transport"]),
            discovery=str(row["discovery"]),
            protocol=str(row["protocol"]),
            user_request=str(row["user_request"]),
            status=str(row["status"]),
            final_state=str(row["final_state"]) if row["final_state"] else None,
            server_context_id=(
                str(row["server_context_id"]) if row["server_context_id"] else None
            ),
            server_task_id=str(row["server_task_id"]) if row["server_task_id"] else None,
            error=str(row["error"]) if row["error"] else None,
            owner_pid=int(row["owner_pid"] or 0),
            idempotency_key=str(row["idempotency_key"]),
            transcript_json=str(row["transcript_json"]) if row["transcript_json"] else None,
            transcript_markdown=(
                str(row["transcript_markdown"]) if row["transcript_markdown"] else None
            ),
            trace_json=str(row["trace_json"]) if row["trace_json"] else None,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            finished_at=str(row["finished_at"]) if row["finished_at"] else None,
        )

    def _get_run(self, run_id: str) -> ClientRunRecord | None:
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return self._row_to_run(row) if row is not None else None

    def _find_run_by_idempotency(
        self,
        conversation_id: str,
        idempotency_key: str,
    ) -> ClientRunRecord | None:
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM runs WHERE conversation_id=? AND idempotency_key=?",
                (conversation_id, idempotency_key),
            ).fetchone()
        return self._row_to_run(row) if row is not None else None

    def _list_runs(
        self,
        limit: int,
        conversation_id: str | None,
    ) -> builtins.list[ClientRunRecord]:
        resolved_limit = max(1, min(limit, 500))
        query = "SELECT * FROM runs"
        params: builtins.list[object] = []
        if conversation_id:
            query += " WHERE conversation_id=?"
            params.append(conversation_id)
        query += " ORDER BY created_at DESC, run_id DESC LIMIT ?"
        params.append(resolved_limit)
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_run(row) for row in rows]

    def _record_negotiation_event(self, record: NegotiationEventRecord) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO negotiation_events
                    (event_id, run_id, seq, negotiation_id, kind, round, performative, direction,
                     actor, content_json, decision_action, rationale, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.event_id,
                    record.run_id,
                    record.seq,
                    record.negotiation_id,
                    record.kind,
                    record.round,
                    record.performative,
                    record.direction,
                    record.actor,
                    _dump_json(record.content),
                    record.decision_action,
                    record.rationale,
                    record.created_at,
                ),
            )

    def _list_negotiation_events(self, run_id: str) -> builtins.list[NegotiationEventRecord]:
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM negotiation_events WHERE run_id=? ORDER BY seq ASC",
                (run_id,),
            ).fetchall()
        return [
            NegotiationEventRecord(
                event_id=str(row["event_id"]),
                run_id=str(row["run_id"]),
                seq=int(row["seq"]),
                negotiation_id=(
                    str(row["negotiation_id"]) if row["negotiation_id"] else None
                ),
                kind=str(row["kind"]) if row["kind"] else None,
                round=int(row["round"]) if row["round"] is not None else None,
                performative=str(row["performative"]) if row["performative"] else None,
                direction=str(row["direction"]),
                actor=str(row["actor"]),
                content=_load_json(row["content_json"]),
                decision_action=(
                    str(row["decision_action"]) if row["decision_action"] else None
                ),
                rationale=str(row["rationale"]) if row["rationale"] else None,
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def _save_task_snapshot(self, record: TaskSnapshotRecord) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO task_snapshots
                    (run_id, task_id, context_id, state, updated_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    task_id=excluded.task_id,
                    context_id=excluded.context_id,
                    state=excluded.state,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    record.run_id,
                    record.task_id,
                    record.context_id,
                    record.state,
                    record.updated_at,
                    _dump_json(record.payload),
                ),
            )

    def _save_run_metrics(self, metrics: Mapping[str, object]) -> None:
        accuracy = _section(metrics.get("accuracy"))
        tokens = _section(metrics.get("tokens"))
        latency = _section(metrics.get("latency"))
        stamp = now_iso()
        started = metrics.get("startedAt")
        created_at = started if isinstance(started, str) and started else stamp
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO run_metrics
                    (run_id, conversation_id, protocol, scenario, mode, transport, language,
                     model_mode, status, final_state, passed, accuracy_score, duration_ms,
                     model_ms, tool_ms, approval_wait_ms, prompt_tokens, completion_tokens,
                     total_tokens, llm_calls, token_available, created_at, updated_at,
                     metric_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    conversation_id=excluded.conversation_id,
                    protocol=excluded.protocol,
                    scenario=excluded.scenario,
                    mode=excluded.mode,
                    transport=excluded.transport,
                    language=excluded.language,
                    model_mode=excluded.model_mode,
                    status=excluded.status,
                    final_state=excluded.final_state,
                    passed=excluded.passed,
                    accuracy_score=excluded.accuracy_score,
                    duration_ms=excluded.duration_ms,
                    model_ms=excluded.model_ms,
                    tool_ms=excluded.tool_ms,
                    approval_wait_ms=excluded.approval_wait_ms,
                    prompt_tokens=excluded.prompt_tokens,
                    completion_tokens=excluded.completion_tokens,
                    total_tokens=excluded.total_tokens,
                    llm_calls=excluded.llm_calls,
                    token_available=excluded.token_available,
                    updated_at=excluded.updated_at,
                    metric_json=excluded.metric_json
                """,
                (
                    str(metrics.get("runId") or ""),
                    metrics.get("conversationId"),
                    str(metrics.get("protocol") or ""),
                    str(metrics.get("scenario") or ""),
                    str(metrics.get("mode") or ""),
                    str(metrics.get("transport") or ""),
                    str(metrics.get("language") or ""),
                    str(metrics.get("modelMode") or ""),
                    str(metrics.get("status") or ""),
                    metrics.get("finalState"),
                    _optional_bool(accuracy.get("passed")),
                    _optional_float(accuracy.get("score")),
                    _optional_float(metrics.get("durationMs")),
                    _optional_float(latency.get("modelMs")),
                    _optional_float(latency.get("toolMs")),
                    _optional_float(latency.get("approvalWaitMs")),
                    _optional_int(tokens.get("prompt")),
                    _optional_int(tokens.get("completion")),
                    _optional_int(tokens.get("total")),
                    _optional_int(tokens.get("llmCalls")),
                    1 if tokens.get("available") is True else 0,
                    created_at,
                    stamp,
                    _dump_json(dict(metrics)),
                ),
            )

    def _get_task_snapshot(self, run_id: str) -> TaskSnapshotRecord | None:
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM task_snapshots WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return TaskSnapshotRecord(
            run_id=str(row["run_id"]),
            task_id=str(row["task_id"]) if row["task_id"] else None,
            context_id=str(row["context_id"]) if row["context_id"] else None,
            state=str(row["state"]) if row["state"] else None,
            updated_at=str(row["updated_at"]),
            payload=_load_json(row["payload_json"]),
        )

    def _mark_running_interrupted(self) -> int:
        """Mark runs whose owning process is gone as ``interrupted``.

        A live second client process (for example the Web UI while the A2A gateway is running) owns
        its own runs; checking the recorded PID keeps its active runs untouched.
        """
        stamp = now_iso()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT run_id, owner_pid FROM runs WHERE status=?",
                (RUN_STATUS_RUNNING,),
            ).fetchall()
            stale = [
                str(run_id)
                for run_id, owner_pid in rows
                if not _pid_alive(int(owner_pid or 0))
            ]
            if not stale:
                return 0
            placeholders = ",".join("?" for _ in stale)
            cursor = connection.execute(
                f"UPDATE runs SET status=?, updated_at=?, finished_at=? "
                f"WHERE run_id IN ({placeholders})",
                (RUN_STATUS_INTERRUPTED, stamp, stamp, *stale),
            )
            return int(cursor.rowcount)
