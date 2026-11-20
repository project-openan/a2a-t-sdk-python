"""SQLite-backed push-notification configuration store of the inbound A2A gateway.

The gateway Task database also owns the ``push_notification_configs`` table (schema version 2 of
:mod:`protocol.sqlite_task_store`), so a webhook registered by an external caller survives a restart
together with the Task it belongs to. Reads are owner-scoped; the dispatch path sees every owner,
matching the A2A ``PushNotificationConfigStore`` contract.

The full protobuf ``TaskPushNotificationConfig`` is stored as a blob, exactly like the Task store
stores a Task.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Final

from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.server.tasks.push_notification_config_store import PushNotificationConfigStore
from a2a.types import TaskPushNotificationConfig
from shared.sqlite import connect

from protocol.sqlite_task_store import ensure_task_db

_LOGGER = logging.getLogger("ran_energy_saving.sqlite_push_config_store")

#: Tables owned by the gateway Task database.
_UPSERT_SQL: Final[str] = """
INSERT INTO push_notification_configs
    (task_id, owner, config_id, updated_at, config_blob)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(task_id, owner, config_id) DO UPDATE SET
    updated_at = excluded.updated_at,
    config_blob = excluded.config_blob
"""


class SqlitePushNotificationConfigStore(PushNotificationConfigStore):
    """Persist inbound gateway webhook registrations in the gateway Task database."""

    def __init__(
        self,
        path: str | Path,
        *,
        owner_resolver: OwnerResolver = resolve_user_scope,
    ) -> None:
        """Open (or create) the gateway Task database at ``path``.

        Args:
            path: Absolute SQLite path of the gateway Task database.
            owner_resolver: maps a request context to an owner partition.
        """
        self._path = Path(path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._owner_resolver = owner_resolver
        ensure_task_db(self._path)

    @property
    def path(self) -> Path:
        """Return the backing SQLite file path."""
        return self._path

    async def set_info(
        self,
        task_id: str,
        notification_config: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> None:
        """Persist or replace one webhook registration."""
        if not notification_config.id:
            notification_config.id = task_id
        owner = self._owner_resolver(context)
        await asyncio.to_thread(
            self._set_info,
            task_id,
            owner,
            notification_config.id,
            notification_config.SerializeToString(),
        )

    async def get_info(
        self,
        task_id: str,
        context: ServerCallContext,
    ) -> list[TaskPushNotificationConfig]:
        """Return the caller-owned webhook registrations of one task."""
        owner = self._owner_resolver(context)
        try:
            blobs = await asyncio.to_thread(self._get_blobs, task_id, owner)
        except sqlite3.Error as exc:
            _LOGGER.warning("push config read failed for %s: %s", task_id, exc)
            return []
        return [_parse(blob) for blob in blobs]

    async def get_info_for_dispatch(
        self,
        task_id: str,
    ) -> list[TaskPushNotificationConfig]:
        """Return every webhook registration of one task, across owners."""
        try:
            blobs = await asyncio.to_thread(self._get_blobs, task_id, None)
        except sqlite3.Error as exc:
            _LOGGER.warning("push dispatch read failed for %s: %s", task_id, exc)
            return []
        return [_parse(blob) for blob in blobs]

    async def delete_info(
        self,
        task_id: str,
        context: ServerCallContext,
        config_id: str | None = None,
    ) -> None:
        """Delete one caller-owned registration, or every registration of the task."""
        owner = self._owner_resolver(context)
        await asyncio.to_thread(self._delete_info, task_id, owner, config_id)

    def _set_info(self, task_id: str, owner: str, config_id: str, blob: bytes) -> None:
        with connect(self._path) as connection:
            connection.execute(_UPSERT_SQL, (task_id, owner, config_id, time.time(), blob))

    def _get_blobs(self, task_id: str, owner: str | None) -> list[bytes]:
        query = "SELECT config_blob FROM push_notification_configs WHERE task_id=?"
        parameters: list[object] = [task_id]
        if owner is not None:
            query += " AND owner=?"
            parameters.append(owner)
        query += " ORDER BY config_id"
        with connect(self._path) as connection:
            return [
                bytes(row[0])
                for row in connection.execute(query, parameters).fetchall()
            ]

    def _delete_info(self, task_id: str, owner: str, config_id: str | None) -> None:
        query = "DELETE FROM push_notification_configs WHERE task_id=? AND owner=?"
        parameters: list[object] = [task_id, owner]
        if config_id is not None:
            query += " AND config_id=?"
            parameters.append(config_id)
        with connect(self._path) as connection:
            connection.execute(query, parameters)


def _parse(blob: bytes) -> TaskPushNotificationConfig:
    """Rebuild one configuration from its stored protobuf blob."""
    config = TaskPushNotificationConfig()
    config.ParseFromString(blob)
    return config


__all__ = ["SqlitePushNotificationConfigStore"]
