"""Tests of the SQLite push-notification configuration store."""

from __future__ import annotations

from pathlib import Path

from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.types import TaskPushNotificationConfig
from protocol.sqlite_push_config_store import SqlitePushNotificationConfigStore
from shared.sqlite import connect, schema_version


class _NamedUser(User):
    """Authenticated user double with a stable owner name."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


def _context(user: str) -> ServerCallContext:
    return ServerCallContext(user=_NamedUser(user))


def _config(url: str = "https://caller.example/hook", token: str = "hook-token") -> TaskPushNotificationConfig:
    config = TaskPushNotificationConfig()
    config.task_id = "task-1"
    config.url = url
    config.token = token
    return config


async def test_set_get_and_owner_scoping(tmp_path: Path) -> None:
    path = tmp_path / "gateway.sqlite3"
    store = SqlitePushNotificationConfigStore(path)

    await store.set_info("task-1", _config(), _context("alice"))

    alice = await store.get_info("task-1", _context("alice"))
    bob = await store.get_info("task-1", _context("bob"))
    dispatched = await store.get_info_for_dispatch("task-1")

    assert [config.url for config in alice] == ["https://caller.example/hook"]
    assert alice[0].token == "hook-token"
    assert bob == []
    assert [config.url for config in dispatched] == ["https://caller.example/hook"]


async def test_config_survives_a_new_store_instance(tmp_path: Path) -> None:
    path = tmp_path / "gateway.sqlite3"
    await SqlitePushNotificationConfigStore(path).set_info(
        "task-1", _config(), _context("alice")
    )

    reopened = SqlitePushNotificationConfigStore(path)
    configs = await reopened.get_info_for_dispatch("task-1")

    assert [config.url for config in configs] == ["https://caller.example/hook"]


async def test_upsert_and_delete(tmp_path: Path) -> None:
    store = SqlitePushNotificationConfigStore(tmp_path / "gateway.sqlite3")
    context = _context("alice")
    await store.set_info("task-1", _config(), context)
    await store.set_info("task-1", _config(url="https://caller.example/v2"), context)
    configs = await store.get_info("task-1", context)
    assert [config.url for config in configs] == ["https://caller.example/v2"]

    await store.delete_info("task-1", context, configs[0].id)
    assert await store.get_info("task-1", context) == []

    await store.set_info("task-1", _config(), context)
    await store.set_info("task-2", _config(), context)
    await store.delete_info("task-1", context)
    assert await store.get_info("task-1", context) == []
    assert len(await store.get_info("task-2", context)) == 1


def test_the_gateway_database_is_at_schema_version_three(tmp_path: Path) -> None:
    path = tmp_path / "gateway.sqlite3"
    SqlitePushNotificationConfigStore(path)

    with connect(path) as connection:
        assert schema_version(connection) == 3
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"tasks", "push_notification_configs", "downstream_bindings"} <= tables
