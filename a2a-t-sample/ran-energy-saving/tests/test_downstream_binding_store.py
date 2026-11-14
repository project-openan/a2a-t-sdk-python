"""Tests of the persistent outer-Task ↔ downstream-Task binding store."""

from __future__ import annotations

import time
from pathlib import Path

from protocol.downstream_binding_store import (
    TERMINAL_STATE,
    WORKING_STATE,
    DownstreamBinding,
    DownstreamBindingStore,
)


def _binding(
    inner_task_id: str = "nms-task-1",
    *,
    inner_context_id: str = "nms-ctx-1",
    outer_task_id: str = "outer-task-1",
    run_id: str = "run-1",
    state: str = WORKING_STATE,
    created_at: float = 0.0,
    terminal_at: float | None = None,
) -> DownstreamBinding:
    """Build one binding with stable defaults."""
    return DownstreamBinding(
        inner_task_id=inner_task_id,
        inner_context_id=inner_context_id,
        outer_task_id=outer_task_id,
        run_id=run_id,
        owner="",
        protocol="pure",
        state=state,
        created_at=created_at,
        terminal_at=terminal_at,
    )


async def test_save_and_get_round_trip(tmp_path: Path) -> None:
    store = DownstreamBindingStore(tmp_path / "tasks.sqlite3")

    await store.save(_binding())

    binding = await store.get("nms-task-1", "nms-ctx-1")
    assert binding is not None
    assert binding.outer_task_id == "outer-task-1"
    assert binding.run_id == "run-1"
    assert binding.protocol == "pure"
    assert binding.state == WORKING_STATE
    assert binding.created_at > 0
    assert binding.updated_at >= binding.created_at


async def test_save_updates_an_existing_binding_and_keeps_created_at(tmp_path: Path) -> None:
    store = DownstreamBindingStore(tmp_path / "tasks.sqlite3")
    original = _binding(created_at=time.time() - 60)
    await store.save(original)

    await store.save(_binding(outer_task_id="outer-task-2", run_id="run-2", created_at=original.created_at))

    binding = await store.get("nms-task-1", "nms-ctx-1")
    assert binding is not None
    assert binding.outer_task_id == "outer-task-2"
    assert binding.run_id == "run-2"
    assert binding.created_at == original.created_at


async def test_get_by_outer_task_returns_the_active_binding(tmp_path: Path) -> None:
    """A continuation addressed to the outer Task must find its downstream identity."""
    store = DownstreamBindingStore(tmp_path / "tasks.sqlite3")
    await store.save(_binding(outer_task_id="outer-task-1"))
    await store.save(
        _binding("nms-task-2", inner_context_id="nms-ctx-2", outer_task_id="outer-task-2")
    )

    binding = await store.get_by_outer_task("outer-task-2")

    assert binding is not None
    assert binding.inner_task_id == "nms-task-2"
    assert binding.inner_context_id == "nms-ctx-2"
    assert await store.get_by_outer_task("missing") is None


async def test_get_by_outer_task_ignores_terminal_bindings(tmp_path: Path) -> None:
    """A finished downstream Task cannot be continued."""
    store = DownstreamBindingStore(tmp_path / "tasks.sqlite3")
    await store.save(_binding(state=TERMINAL_STATE, terminal_at=time.time()))

    assert await store.get_by_outer_task("outer-task-1") is None


async def test_list_active_excludes_terminal_bindings(tmp_path: Path) -> None:
    store = DownstreamBindingStore(tmp_path / "tasks.sqlite3")
    await store.save(_binding("active-1"))
    await store.save(_binding("active-2"))
    await store.save(_binding("done-1", state=TERMINAL_STATE, terminal_at=time.time()))

    active = await store.list_active()

    assert [binding.inner_task_id for binding in active] == ["active-1", "active-2"]


async def test_mark_terminal_sets_the_terminal_timestamp_and_hash(tmp_path: Path) -> None:
    store = DownstreamBindingStore(tmp_path / "tasks.sqlite3")
    await store.save(_binding())

    assert await store.mark_terminal("nms-task-1", "nms-ctx-1", event_hash="abc") is True

    binding = await store.get("nms-task-1", "nms-ctx-1")
    assert binding is not None
    assert binding.state == TERMINAL_STATE
    assert binding.terminal is True
    assert binding.terminal_at is not None
    assert binding.last_event_hash == "abc"
    assert await store.mark_terminal("missing") is False


async def test_delete_expired_only_removes_old_terminal_bindings(tmp_path: Path) -> None:
    store = DownstreamBindingStore(tmp_path / "tasks.sqlite3")
    old = time.time() - 8 * 24 * 60 * 60
    await store.save(_binding("old", state=TERMINAL_STATE, terminal_at=old))
    await store.save(_binding("new", state=TERMINAL_STATE, terminal_at=time.time()))

    deleted = await store.delete_expired(before=time.time() - 7 * 24 * 60 * 60)

    assert deleted == 1
    assert await store.get("old", "nms-ctx-1") is None
    assert await store.get("new", "nms-ctx-1") is not None
