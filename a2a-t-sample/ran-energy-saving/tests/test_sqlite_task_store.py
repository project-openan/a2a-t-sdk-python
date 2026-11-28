"""Tests of the sample's SQLite A2A TaskStore."""

from __future__ import annotations

import asyncio
from pathlib import Path

from a2a.server.context import ServerCallContext, UnauthenticatedUser
from a2a.types import (
    Artifact,
    ListTasksRequest,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from protocol.messages import SAMPLE_RUN_ID_METADATA_KEY
from protocol.sqlite_task_store import SqliteTaskStore, read_task_summaries


def _context() -> ServerCallContext:
    """Build one unauthenticated request context."""
    return ServerCallContext(state={}, user=UnauthenticatedUser())


def _task(
    task_id: str,
    *,
    context_id: str = "ctx-1",
    state: TaskState = TaskState.TASK_STATE_COMPLETED,
    summary: str = "Plan applied to cell-001.",
    run_id: str = "run-1",
) -> Task:
    """Build one Task with a report artifact."""
    task = Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=state),
        artifacts=[
            Artifact(
                artifact_id=f"artifact-{task_id}",
                name="energy saving intent report",
                parts=[Part(text=summary)],
            )
        ],
    )
    if run_id:
        task.metadata[SAMPLE_RUN_ID_METADATA_KEY] = run_id
    return task


async def test_save_get_and_delete_round_trip(tmp_path: Path) -> None:
    store = SqliteTaskStore(tmp_path / "tasks.sqlite3")
    task = _task("task-1")

    await store.save(task, _context())
    loaded = await store.get("task-1", _context())

    assert loaded is not None
    assert loaded.id == "task-1"
    assert loaded.context_id == "ctx-1"
    assert loaded.status.state == TaskState.TASK_STATE_COMPLETED
    assert loaded.artifacts[0].parts[0].text == "Plan applied to cell-001."

    await store.delete("task-1", _context())
    assert await store.get("task-1", _context()) is None


async def test_list_filters_context_and_paginates(tmp_path: Path) -> None:
    store = SqliteTaskStore(tmp_path / "tasks.sqlite3")
    for index in range(3):
        await store.save(_task(f"task-{index}", context_id="ctx-1"), _context())
    await store.save(_task("task-other", context_id="ctx-2"), _context())

    first = await store.list(
        ListTasksRequest(context_id="ctx-1", page_size=2),
        _context(),
    )

    assert [task.id for task in first.tasks] == ["task-2", "task-1"]
    assert first.total_size == 3
    assert first.next_page_token

    second = await store.list(
        ListTasksRequest(context_id="ctx-1", page_size=2, page_token=first.next_page_token),
        _context(),
    )
    assert [task.id for task in second.tasks] == ["task-0"]
    assert second.next_page_token == ""


async def test_list_filters_status(tmp_path: Path) -> None:
    store = SqliteTaskStore(tmp_path / "tasks.sqlite3")
    await store.save(_task("task-done"), _context())
    await store.save(_task("task-failed", state=TaskState.TASK_STATE_FAILED), _context())

    response = await store.list(
        ListTasksRequest(status=TaskState.TASK_STATE_FAILED),
        _context(),
    )

    assert [task.id for task in response.tasks] == ["task-failed"]


def test_list_summaries_extracts_report_and_run_id(tmp_path: Path) -> None:
    path = tmp_path / "tasks.sqlite3"
    store = SqliteTaskStore(path)
    asyncio.run(store.save(_task("task-1", summary="A clean report."), _context()))

    rows = store.list_summaries()

    assert rows[0]["taskId"] == "task-1"
    assert rows[0]["state"] == "TASK_STATE_COMPLETED"
    assert rows[0]["summary"] == "A clean report."
    assert rows[0]["runId"] == "run-1"
    updated_at = rows[0]["updatedAt"]
    assert isinstance(updated_at, str)
    assert updated_at.endswith("Z")


async def test_concurrent_saves_do_not_lock(tmp_path: Path) -> None:
    store = SqliteTaskStore(tmp_path / "tasks.sqlite3")

    await asyncio.gather(
        *(store.save(_task(f"task-{index}"), _context()) for index in range(12))
    )

    assert len(store.list_summaries(limit=20)) == 12


def test_read_task_summaries_is_empty_and_read_only_for_a_missing_database(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing.sqlite3"

    assert read_task_summaries(path) == []
    assert not path.exists()
