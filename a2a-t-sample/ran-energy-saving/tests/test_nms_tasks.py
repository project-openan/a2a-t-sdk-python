"""Tests of the NMS task picker projection used by the presentation."""

from __future__ import annotations

from dataclasses import replace

import pytest
from a2a_t.core.metadata import TASK_T_EXTENSION_URI
from demo.web import nms_tasks
from demo.web.nms_tasks import (
    NMS_SUBMITTED_STATE,
    NMS_WORKING_STATE,
    cancel_nms_task,
    list_active_tasks,
    list_modifiable_tasks,
    read_task,
)
from protocol.nms_client import NmsProtocolError
from shared.config import DISCOVERY_NMS, SampleConfig


def _config(base: SampleConfig) -> SampleConfig:
    return replace(
        base,
        discovery=DISCOVERY_NMS,
        nms_a2a_base="http://nms.example/a2a/json",
        nms_token_url="http://nms.example/sessions",
        nms_token_user="user",
        nms_token_value="secret",
    )


def _working_task(task_id: str = "task-9", context_id: str = "ctx-9") -> dict[str, object]:
    return {
        "id": task_id,
        "contextId": context_id,
        "status": {
            "state": NMS_WORKING_STATE,
            "message": {"parts": [{"text": "Task is executing the energy-saving plan."}]},
        },
        "history": [
            {
                "role": "ROLE_USER",
                "metadata": {
                    TASK_T_EXTENSION_URI: (
                        "## 操作类型(Operation Type)\nModify\n\n"
                        "## 任务对象(Task Object)\n区域为 Region A。"
                        "站点列表1为[站点1, 站点2]。"
                    )
                },
            }
        ],
    }


class FakeNmsClient:
    """NMS client double returning one list or one task document."""

    tasks: list[dict[str, object]] = []
    task: dict[str, object] | None = None
    cancelled: list[str] = []
    cancel_result: bool = True
    cancel_error: Exception | None = None
    closed = False

    def __init__(self, config: object) -> None:
        del config
        type(self).closed = False

    async def list_tasks(self) -> dict[str, object]:
        return {"tasks": list(type(self).tasks)}

    async def get_task(self, task_id: str) -> dict[str, object]:
        del task_id
        if type(self).task is None:
            raise AssertionError("no task configured")
        return type(self).task  # type: ignore[return-value]

    async def cancel_task(self, task_id: str) -> bool:
        if type(self).cancel_error is not None:
            raise type(self).cancel_error  # type: ignore[misc]
        type(self).cancelled.append(task_id)
        return type(self).cancel_result

    async def aclose(self) -> None:
        type(self).closed = True


@pytest.fixture(autouse=True)
def fake_client(monkeypatch: pytest.MonkeyPatch) -> type[FakeNmsClient]:
    monkeypatch.setattr(nms_tasks, "NmsClient", FakeNmsClient)
    FakeNmsClient.tasks = []
    FakeNmsClient.task = None
    FakeNmsClient.cancelled = []
    FakeNmsClient.cancel_result = True
    FakeNmsClient.cancel_error = None
    return FakeNmsClient


async def test_list_modifiable_tasks_keeps_submitted_and_working_tasks(
    offline_config: SampleConfig,
) -> None:
    FakeNmsClient.tasks = [
        _working_task(),
        {
            **_working_task(task_id="task-8", context_id="ctx-8"),
            "status": {"state": NMS_SUBMITTED_STATE},
        },
        {
            "id": "done-1",
            "contextId": "ctx-done",
            "status": {"state": "TASK_STATE_COMPLETED"},
        },
        {"contextId": "ctx-without-id", "status": {"state": NMS_WORKING_STATE}},
    ]

    rows = await list_modifiable_tasks(_config(offline_config))

    assert [row["taskId"] for row in rows] == ["task-9", "task-8"]
    assert rows[0]["contextId"] == "ctx-9"
    assert rows[0]["state"] == NMS_WORKING_STATE
    assert rows[0]["region"] == "Region A"
    assert rows[0]["sites"] == ["站点1", "站点2"]
    assert "executing" in str(rows[0]["summary"])
    assert rows[1]["state"] == NMS_SUBMITTED_STATE
    assert FakeNmsClient.closed is True


async def test_read_task_returns_the_compact_row(offline_config: SampleConfig) -> None:
    FakeNmsClient.task = _working_task(task_id="task-7", context_id="ctx-7")

    row = await read_task(_config(offline_config), "task-7")

    assert row is not None
    assert row["taskId"] == "task-7"
    assert row["contextId"] == "ctx-7"
    assert row["state"] == NMS_WORKING_STATE
    assert FakeNmsClient.closed is True


async def test_list_modifiable_tasks_ignores_a_malformed_document(
    offline_config: SampleConfig,
) -> None:
    FakeNmsClient.tasks = ["not-a-task", {}]  # type: ignore[list-item]

    assert await list_modifiable_tasks(_config(offline_config)) == []


async def test_list_active_tasks_keeps_input_required_for_cancel_only(
    offline_config: SampleConfig,
) -> None:
    """A Task waiting for input still holds NMS quota, so the picker must list it."""
    FakeNmsClient.tasks = [
        _working_task(),
        {
            "id": "task-waiting",
            "contextId": "ctx-waiting",
            "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
        },
        {
            "id": "done-1",
            "contextId": "ctx-done",
            "status": {"state": "TASK_STATE_COMPLETED"},
        },
    ]

    rows = await list_active_tasks(_config(offline_config))

    assert [row["taskId"] for row in rows] == ["task-9", "task-waiting"]
    assert rows[1]["state"] == "TASK_STATE_INPUT_REQUIRED"


async def test_cancel_nms_task_reports_success(offline_config: SampleConfig) -> None:
    FakeNmsClient.cancelled = []

    assert await cancel_nms_task(_config(offline_config), "task-9") is True
    assert FakeNmsClient.cancelled == ["task-9"]
    assert FakeNmsClient.closed is True


async def test_cancel_nms_task_reports_the_known_503_as_unsupported(
    offline_config: SampleConfig,
) -> None:
    FakeNmsClient.cancel_result = False

    assert await cancel_nms_task(_config(offline_config), "task-9") is False


async def test_cancel_nms_task_propagates_other_failures(
    offline_config: SampleConfig,
) -> None:
    FakeNmsClient.cancel_error = NmsProtocolError("NMS cancel returned HTTP 500: boom")

    with pytest.raises(NmsProtocolError):
        await cancel_nms_task(_config(offline_config), "task-9")
