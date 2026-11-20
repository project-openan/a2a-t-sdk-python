"""Persistent binding between an outer A2A Task and the downstream Task it tracks.

The OSS Agent owns an outer A2A Task but the vendor NMS owns the Task that actually runs for hours
or days. A webhook push only carries the NMS ``taskId``/``contextId``, so the process needs a
durable lookup from that identity back to the outer Task, its Run Explorer run id and its owner.
The mapping lives in the same SQLite database as the Task store (schema version 3); the webhook
pump and the UI watcher can therefore resume after a process restart.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from shared.sqlite import connect

from protocol.sqlite_task_store import ensure_task_db

#: Binding states. A ``working`` binding is still tracking its downstream Task.
WORKING_STATE: Final[str] = "working"
TERMINAL_STATE: Final[str] = "terminal"

_UPSERT_SQL: Final[str] = """
INSERT INTO downstream_bindings
    (inner_task_id, inner_context_id, outer_task_id, run_id, owner, protocol, state,
     created_at, updated_at, terminal_at, last_event_hash)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(inner_task_id, inner_context_id) DO UPDATE SET
    outer_task_id = excluded.outer_task_id,
    run_id = excluded.run_id,
    owner = excluded.owner,
    protocol = excluded.protocol,
    state = excluded.state,
    updated_at = excluded.updated_at,
    terminal_at = excluded.terminal_at,
    last_event_hash = excluded.last_event_hash
"""

_SELECT_COLUMNS: Final[str] = (
    "inner_task_id, inner_context_id, outer_task_id, run_id, owner, protocol, state, "
    "created_at, updated_at, terminal_at, last_event_hash"
)


@dataclass(frozen=True)
class DownstreamBinding:
    """One outer-Task ↔ downstream-Task binding.

    Attributes:
        inner_task_id: Vendor/NMS Task id (the id carried by its webhook pushes).
        outer_task_id: A2A Task id exposed by the OSS Agent (or the UI run's task id).
        inner_context_id: Vendor/NMS context id, empty when the downstream has none.
        run_id: Run Explorer run id used for late trace spans.
        owner: A2A owner scope of the outer Task.
        protocol: Interaction protocol (``pure`` for the standard A2A hop).
        state: ``working`` while tracking, ``terminal`` once the downstream finished.
        created_at: Epoch seconds when the binding was first written.
        updated_at: Epoch seconds of the last update.
        terminal_at: Epoch seconds when the downstream reached a terminal state.
        last_event_hash: Fingerprint of the last applied downstream event (idempotency).
    """

    inner_task_id: str
    outer_task_id: str
    inner_context_id: str = ""
    run_id: str = ""
    owner: str = ""
    protocol: str = ""
    state: str = WORKING_STATE
    created_at: float = 0.0
    updated_at: float = 0.0
    terminal_at: float | None = None
    last_event_hash: str = ""

    @property
    def terminal(self) -> bool:
        """Whether the downstream Task already reached a terminal state."""
        return self.state == TERMINAL_STATE


class DownstreamBindingStore:
    """SQLite persistence of the outer ↔ downstream Task bindings."""

    def __init__(self, path: str | Path) -> None:
        """Open (or create) the binding database at ``path``.

        Args:
            path: Absolute SQLite path; usually the OSS Task database.
        """
        self._path = Path(path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        ensure_task_db(self._path)

    @property
    def path(self) -> Path:
        """Return the backing SQLite file path."""
        return self._path

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection]:
        """Yield one writable connection with row access by column name."""
        with connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            yield connection

    async def save(self, binding: DownstreamBinding) -> None:
        """Insert or update one binding.

        An update keeps the original ``created_at`` so a resumed pump does not reset the task age.
        """
        await asyncio.to_thread(self._save, binding)

    def _save(self, binding: DownstreamBinding) -> None:
        """Synchronous upsert used by :meth:`save`."""
        now = time.time()
        created_at = binding.created_at or now
        updated_at = binding.updated_at or now
        with self._connection() as connection:
            connection.execute(
                _UPSERT_SQL,
                (
                    binding.inner_task_id,
                    binding.inner_context_id,
                    binding.outer_task_id,
                    binding.run_id,
                    binding.owner,
                    binding.protocol,
                    binding.state,
                    created_at,
                    updated_at,
                    binding.terminal_at,
                    binding.last_event_hash,
                ),
            )

    async def get(
        self,
        inner_task_id: str,
        inner_context_id: str = "",
    ) -> DownstreamBinding | None:
        """Return one binding by downstream identity, ``None`` when absent."""
        return await asyncio.to_thread(self._get, inner_task_id, inner_context_id)

    def _get(self, inner_task_id: str, inner_context_id: str) -> DownstreamBinding | None:
        """Synchronous binding lookup."""
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {_SELECT_COLUMNS} FROM downstream_bindings WHERE inner_task_id = ? AND inner_context_id = ?",
                (inner_task_id, inner_context_id),
            ).fetchone()
        return _binding_from_row(row) if row is not None else None

    async def get_by_outer_task(self, outer_task_id: str) -> DownstreamBinding | None:
        """Return the active binding of one outer Task, ``None`` when absent or terminal.

        A pure-A2A continuation arrives addressed to the outer Task the caller owns, not to the
        vendor Task id. The OSS Agent uses this lookup to find the downstream identity it must
        forward the message to.
        """
        return await asyncio.to_thread(self._get_by_outer_task, outer_task_id)

    def _get_by_outer_task(self, outer_task_id: str) -> DownstreamBinding | None:
        """Synchronous outer-Task lookup."""
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {_SELECT_COLUMNS} FROM downstream_bindings "
                "WHERE outer_task_id = ? AND state = ? ORDER BY updated_at DESC LIMIT 1",
                (outer_task_id, WORKING_STATE),
            ).fetchone()
        return _binding_from_row(row) if row is not None else None

    async def list_active(self) -> list[DownstreamBinding]:
        """Return every binding still tracking a downstream Task."""
        return await asyncio.to_thread(self._list_active)

    def _list_active(self) -> list[DownstreamBinding]:
        """Synchronous active-binding list."""
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {_SELECT_COLUMNS} FROM downstream_bindings WHERE state = ? ORDER BY updated_at, inner_task_id",
                (WORKING_STATE,),
            ).fetchall()
        return [_binding_from_row(row) for row in rows]

    async def mark_terminal(
        self,
        inner_task_id: str,
        inner_context_id: str = "",
        *,
        event_hash: str = "",
    ) -> bool:
        """Mark one binding terminal; return whether a row was updated."""
        return await asyncio.to_thread(self._mark_terminal, inner_task_id, inner_context_id, event_hash)

    def _mark_terminal(
        self,
        inner_task_id: str,
        inner_context_id: str,
        event_hash: str,
    ) -> bool:
        """Synchronous terminal transition."""
        now = time.time()
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE downstream_bindings SET state = ?, updated_at = ?, terminal_at = ?, "
                "last_event_hash = CASE WHEN ? = '' THEN last_event_hash ELSE ? END "
                "WHERE inner_task_id = ? AND inner_context_id = ?",
                (
                    TERMINAL_STATE,
                    now,
                    now,
                    event_hash,
                    event_hash,
                    inner_task_id,
                    inner_context_id,
                ),
            )
            return cursor.rowcount > 0

    async def delete_expired(self, *, before: float) -> int:
        """Delete terminal bindings whose ``terminal_at`` is older than ``before``."""
        return await asyncio.to_thread(self._delete_expired, before)

    def _delete_expired(self, before: float) -> int:
        """Synchronous retention cleanup."""
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM downstream_bindings WHERE state = ? AND terminal_at IS NOT NULL AND terminal_at < ?",
                (TERMINAL_STATE, before),
            )
            return cursor.rowcount


def _binding_from_row(row: sqlite3.Row) -> DownstreamBinding:
    """Map one SQLite row to a binding."""
    return DownstreamBinding(
        inner_task_id=str(row["inner_task_id"]),
        inner_context_id=str(row["inner_context_id"]),
        outer_task_id=str(row["outer_task_id"]),
        run_id=str(row["run_id"]),
        owner=str(row["owner"]),
        protocol=str(row["protocol"]),
        state=str(row["state"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        terminal_at=float(row["terminal_at"]) if row["terminal_at"] is not None else None,
        last_event_hash=str(row["last_event_hash"]),
    )


__all__ = [
    "DownstreamBinding",
    "DownstreamBindingStore",
    "TERMINAL_STATE",
    "WORKING_STATE",
]
