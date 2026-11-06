"""Minimal SQLite-backed A2A ``TaskStore`` for the sample's RAN NMS task history.

The server persists every Task transition in a single SQLite database; the Web UI process opens the
same file read-only to render the task-status table. The implementation intentionally uses the
standard library only: WAL mode and a busy timeout are enough for the sample's single server writer
and one UI reader.

The full protobuf ``Task`` is stored as a blob so ``get``/``list`` return exactly what the A2A
runtime saved. The summary columns are denormalized for the UI and are refreshed on every save.
"""

from __future__ import annotations

import asyncio
import builtins
import logging
import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import resolve_user_scope
from a2a.server.tasks.task_store import TaskStore
from a2a.types import ListTasksRequest, ListTasksResponse, Task, TaskState
from a2a_t.core.metadata import TASK_T_EXTENSION_URI
from google.protobuf.json_format import MessageToDict
from shared.sqlite import SchemaMigration, apply_migrations, connect, readonly_connection

from protocol.messages import SAMPLE_RUN_ID_METADATA_KEY

_LOGGER = logging.getLogger("ran_energy_saving.sqlite_task_store")

#: Default page size when the caller does not request one.
DEFAULT_LIST_PAGE_SIZE: Final[int] = 50

#: Longest summary shown in the UI/list response.
SUMMARY_LIMIT: Final[int] = 200


_SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS tasks (
  task_id     TEXT PRIMARY KEY,
  owner       TEXT NOT NULL,
  context_id  TEXT NOT NULL DEFAULT '',
  state       TEXT NOT NULL DEFAULT '',
  updated_at  REAL NOT NULL DEFAULT 0,
  run_id      TEXT NOT NULL DEFAULT '',
  summary     TEXT NOT NULL DEFAULT '',
  task_blob   BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_owner_updated ON tasks(owner, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_context ON tasks(context_id);
"""


def _create_schema(connection: sqlite3.Connection) -> None:
    """Create the version-1 Task schema."""
    connection.executescript(_SCHEMA)


#: Push-notification configurations registered by inbound A2A gateway callers.
_PUSH_CONFIG_SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS push_notification_configs (
  task_id     TEXT NOT NULL,
  config_id   TEXT NOT NULL,
  owner       TEXT NOT NULL,
  updated_at  REAL NOT NULL DEFAULT 0,
  config_blob BLOB NOT NULL,
  PRIMARY KEY (task_id, owner, config_id)
);
CREATE INDEX IF NOT EXISTS idx_push_configs_task
  ON push_notification_configs(task_id);
"""


def _add_push_configs(connection: sqlite3.Connection) -> None:
    """Create the version-2 push-notification configuration table."""
    connection.executescript(_PUSH_CONFIG_SCHEMA)


#: Downstream binding of one outer A2A Task to the vendor/NMS Task it tracks. The OSS agent and the
#: Web UI use it to route a push that can arrive days after the creating request has ended.
_BINDING_SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS downstream_bindings (
  inner_task_id     TEXT NOT NULL,
  inner_context_id  TEXT NOT NULL DEFAULT '',
  outer_task_id     TEXT NOT NULL,
  run_id            TEXT NOT NULL DEFAULT '',
  owner             TEXT NOT NULL DEFAULT '',
  protocol          TEXT NOT NULL DEFAULT '',
  state             TEXT NOT NULL DEFAULT 'working',
  created_at        REAL NOT NULL DEFAULT 0,
  updated_at        REAL NOT NULL DEFAULT 0,
  terminal_at       REAL,
  last_event_hash   TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (inner_task_id, inner_context_id)
);
CREATE INDEX IF NOT EXISTS idx_bindings_outer ON downstream_bindings(outer_task_id);
CREATE INDEX IF NOT EXISTS idx_bindings_state ON downstream_bindings(state, updated_at);
"""


def _add_downstream_bindings(connection: sqlite3.Connection) -> None:
    """Create the version-3 downstream-binding table."""
    connection.executescript(_BINDING_SCHEMA)


#: Ordered schema migrations; index ``i`` upgrades version ``i`` to ``i + 1``.
_MIGRATIONS: Final[tuple[SchemaMigration, ...]] = (
    _create_schema,
    _add_push_configs,
    _add_downstream_bindings,
)


def ensure_task_db(path: str | Path) -> None:
    """Create or upgrade the Task database, including the push and downstream-binding tables."""
    with connect(path) as connection:
        apply_migrations(connection, _MIGRATIONS)


class SqliteTaskStore(TaskStore):
    """SQLite implementation of the A2A ``TaskStore`` interface."""

    def __init__(self, path: str | Path) -> None:
        """Open (or create) the task database at ``path``.

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
        ensure_task_db(self._path)

    async def save(self, task: Task, context: ServerCallContext) -> None:
        """Persist or update one Task.

        Summary extraction runs on the event loop; only immutable values cross into the worker
        thread.
        """
        record = _record_from_task(task, resolve_user_scope(context))
        try:
            await asyncio.to_thread(self._upsert, record)
        except sqlite3.Error as exc:  # persistence must never break the protocol
            _LOGGER.warning("task persistence failed for %s: %s", task.id, exc)

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        """Read one Task by id, scoped to the request owner."""
        owner = resolve_user_scope(context)
        try:
            blob = await asyncio.to_thread(self._get_blob, task_id, owner)
        except sqlite3.Error as exc:
            _LOGGER.warning("task read failed for %s: %s", task_id, exc)
            return None
        if blob is None:
            return None
        task = Task()
        task.ParseFromString(blob)
        return task

    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        """List the owner's Tasks with the A2A filter and pagination fields."""
        owner = resolve_user_scope(context)
        try:
            rows = await asyncio.to_thread(self._list_blobs, owner, params.context_id)
        except sqlite3.Error as exc:
            _LOGGER.warning("task list failed: %s", exc)
            return ListTasksResponse()
        tasks = [_task_from_blob(blob) for _task_id, blob in rows]
        filtered = [
            task
            for task in tasks
            if _matches_status(task, params) and _matches_time(task, params)
        ]
        total = len(filtered)
        start = _decode_page_token(params.page_token)
        limit = params.page_size if params.page_size > 0 else DEFAULT_LIST_PAGE_SIZE
        page = filtered[start : start + limit]
        next_token = str(start + limit) if start + limit < total else ""
        response = ListTasksResponse(next_page_token=next_token, page_size=len(page), total_size=total)
        response.tasks.extend(page)
        return response

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        """Delete one owner-scoped Task."""
        owner = resolve_user_scope(context)
        try:
            await asyncio.to_thread(self._delete, task_id, owner)
        except sqlite3.Error as exc:
            _LOGGER.warning("task delete failed for %s: %s", task_id, exc)

    def list_summaries(self, limit: int = 100) -> builtins.list[dict[str, object]]:
        """Return UI rows for the newest Tasks, across owners.

        Args:
            limit: Maximum number of rows (clamped to 1..500).
        """
        resolved_limit = max(1, min(limit, 500))
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT task_id, context_id, state, updated_at, run_id, summary "
                "FROM tasks ORDER BY updated_at DESC, task_id DESC LIMIT ?",
                (resolved_limit,),
            ).fetchall()
        return [_summary_from_row(row) for row in rows]

    def _upsert(self, record: _TaskRecord) -> None:
        """Write one Task record with last-write-wins semantics."""
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO tasks
                    (task_id, owner, context_id, state, updated_at, run_id, summary, task_blob)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    owner=excluded.owner,
                    context_id=excluded.context_id,
                    state=excluded.state,
                    updated_at=excluded.updated_at,
                    run_id=excluded.run_id,
                    summary=excluded.summary,
                    task_blob=excluded.task_blob
                """,
                (
                    record.task_id,
                    record.owner,
                    record.context_id,
                    record.state,
                    record.updated_at,
                    record.run_id,
                    record.summary,
                    record.task_blob,
                ),
            )

    def _get_blob(self, task_id: str, owner: str) -> bytes | None:
        """Read one Task blob by id and owner."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT task_blob FROM tasks WHERE task_id=? AND owner=?",
                (task_id, owner),
            ).fetchone()
        if row is None:
            return None
        return bytes(row[0])

    def _list_blobs(self, owner: str, context_id: str) -> builtins.list[tuple[str, bytes]]:
        """Read all owner/context Task blobs, newest first."""
        query = "SELECT task_id, task_blob FROM tasks WHERE owner=?"
        params: list[object] = [owner]
        if context_id:
            query += " AND context_id=?"
            params.append(context_id)
        query += " ORDER BY updated_at DESC, task_id DESC"
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [(str(task_id), bytes(blob)) for task_id, blob in rows]

    def _delete(self, task_id: str, owner: str) -> None:
        """Delete one owner-scoped Task."""
        with self._connection() as connection:
            connection.execute("DELETE FROM tasks WHERE task_id=? AND owner=?", (task_id, owner))


class _TaskRecord:
    """Immutable values passed from the event loop to the sqlite worker thread."""

    __slots__ = (
        "task_id",
        "owner",
        "context_id",
        "state",
        "updated_at",
        "run_id",
        "summary",
        "task_blob",
    )

    def __init__(
        self,
        *,
        task_id: str,
        owner: str,
        context_id: str,
        state: str,
        updated_at: float,
        run_id: str,
        summary: str,
        task_blob: bytes,
    ) -> None:
        self.task_id = task_id
        self.owner = owner
        self.context_id = context_id
        self.state = state
        self.updated_at = updated_at
        self.run_id = run_id
        self.summary = summary
        self.task_blob = task_blob


def _record_from_task(task: Task, owner: str) -> _TaskRecord:
    """Extract the summary columns and serialized blob of one Task."""
    return _TaskRecord(
        task_id=task.id,
        owner=owner,
        context_id=task.context_id,
        state=TaskState.Name(task.status.state),
        updated_at=time.time(),
        run_id=_task_run_id(task),
        summary=_task_summary(task),
        task_blob=task.SerializeToString(),
    )


def _task_from_blob(blob: bytes) -> Task:
    """Rebuild one Task from its serialized blob."""
    task = Task()
    task.ParseFromString(blob)
    return task


def _task_metadata(task: Task) -> dict[str, object]:
    """Return the Task metadata as a plain mapping."""
    return MessageToDict(task.metadata)


def _task_run_id(task: Task) -> str:
    """Return the Run Explorer run id carried by a Task."""
    value = _task_metadata(task).get(SAMPLE_RUN_ID_METADATA_KEY)
    return value if isinstance(value, str) else ""


def _task_summary(task: Task) -> str:
    """Return the most useful one-line summary of a Task."""
    for artifact in task.artifacts:
        artifact_metadata = MessageToDict(artifact.metadata)
        if "report" not in artifact.name.lower() and TASK_T_EXTENSION_URI not in artifact_metadata:
            continue
        for part in artifact.parts:
            if part.HasField("data"):
                data = MessageToDict(part.data)
                summary = data.get("summary")
                if isinstance(summary, str) and summary.strip():
                    return _truncate(summary)
            if part.HasField("text") and part.text.strip():
                return _truncate(part.text)
    if task.status.HasField("message") and task.status.message.parts:
        return _truncate(task.status.message.parts[0].text)
    return ""


def _truncate(value: str) -> str:
    """Collapse whitespace and truncate one summary line."""
    text = " ".join(value.split())
    return text if len(text) <= SUMMARY_LIMIT else text[: SUMMARY_LIMIT - 1] + "…"


def _matches_status(task: Task, params: ListTasksRequest) -> bool:
    """Return whether the Task matches the request's status filter."""
    if params.status == TaskState.TASK_STATE_UNSPECIFIED:
        return True
    return task.status.state == params.status


def _matches_time(task: Task, params: ListTasksRequest) -> bool:
    """Return whether the Task is newer than the request's status timestamp."""
    if not params.HasField("status_timestamp_after"):
        return True
    threshold = params.status_timestamp_after.seconds + params.status_timestamp_after.nanos / 1e9
    timestamp = task.status.timestamp
    value = timestamp.seconds + timestamp.nanos / 1e9
    return value >= threshold


def _decode_page_token(token: str) -> int:
    """Decode the sample's offset page token."""
    if not token:
        return 0
    try:
        return max(int(token), 0)
    except ValueError:
        return 0


def _summary_from_row(row: sqlite3.Row) -> dict[str, object]:
    """Convert one summary row to the UI's camelCase document."""
    updated_at = float(row["updated_at"])
    return {
        "taskId": str(row["task_id"]),
        "contextId": str(row["context_id"]),
        "state": str(row["state"]),
        "updatedAt": datetime.fromtimestamp(updated_at, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "runId": str(row["run_id"]),
        "summary": str(row["summary"]),
    }


def read_task_summaries(path: str | Path, limit: int = 100) -> list[dict[str, object]]:
    """Read task summaries from the UI process without creating or modifying the database.

    Args:
        path: SQLite file path.
        limit: Maximum number of rows.

    Returns:
        Summary rows, or an empty list when the file/table is absent.
    """
    resolved_limit = max(1, min(limit, 500))
    try:
        connection = readonly_connection(path)
        if connection is None:
            return []
        try:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT task_id, context_id, state, updated_at, run_id, summary "
                "FROM tasks ORDER BY updated_at DESC, task_id DESC LIMIT ?",
                (resolved_limit,),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return []
    return [_summary_from_row(row) for row in rows]
