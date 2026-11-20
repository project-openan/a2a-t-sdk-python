"""Read and cancel vendor NMS Tasks for the presentation's Running-tasks picker.

Two state sets matter. A2A refuses to append a message to a terminal Task, so a Modify can
continue only ``SUBMITTED`` (accepted, not started yet) and ``WORKING``. A Task waiting for input
(``INPUT_REQUIRED``) cannot be modified by this sample but still holds NMS quota, so the picker can
also list and cancel it. The mapping is deliberately tolerant: the vendor and the local simulator
expose the same Task envelope but may fill different optional fields.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

from protocol.nms_client import NmsClient, NmsConfig, NmsProtocolError
from shared.config import SampleConfig

#: Non-terminal states a Modify can continue (the vendor spelling).
NMS_WORKING_STATE: Final[str] = "TASK_STATE_WORKING"
NMS_SUBMITTED_STATE: Final[str] = "TASK_STATE_SUBMITTED"
#: A Task waiting for input is not modifiable by this sample but still holds NMS quota.
NMS_INPUT_REQUIRED_STATE: Final[str] = "TASK_STATE_INPUT_REQUIRED"
MODIFIABLE_STATES: Final[frozenset[str]] = frozenset({NMS_SUBMITTED_STATE, NMS_WORKING_STATE})
#: States that keep a Task alive on the NMS; all of them can be cancelled.
ACTIVE_STATES: Final[frozenset[str]] = MODIFIABLE_STATES | {NMS_INPUT_REQUIRED_STATE}

_EXCERPT_LENGTH: Final[int] = 180
_REGION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:区域为|区域\s*[:：]|The area is)\s*([^。.\n;；]+)",
    re.IGNORECASE,
)
_SITE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:站点列表[^[]*\[|Site list[^[]*\[)([^\]]+)\]",
    re.IGNORECASE,
)
_TARGET_RE: Final[re.Pattern[str]] = re.compile(r"\{[^}]+\}")


async def list_modifiable_tasks(config: SampleConfig) -> list[dict[str, object]]:
    """Return the NMS Tasks a Modify can continue, newest first.

    Raises:
        protocol.nms_client.NmsError: when the NMS is unreachable or rejects the request.
        protocol.nms_client.NmsConfigError: when the NMS settings are incomplete.
    """
    return await _list_tasks(config, MODIFIABLE_STATES)


async def list_active_tasks(config: SampleConfig) -> list[dict[str, object]]:
    """Return every NMS Task that still holds quota, newest first.

    ``INPUT_REQUIRED`` Tasks are listed so the operator can cancel them; the picker keeps
    Modify disabled for those rows.

    Raises:
        protocol.nms_client.NmsError: when the NMS is unreachable or rejects the request.
        protocol.nms_client.NmsConfigError: when the NMS settings are incomplete.
    """
    return await _list_tasks(config, ACTIVE_STATES)


async def _list_tasks(config: SampleConfig, states: frozenset[str]) -> list[dict[str, object]]:
    """Return the NMS Tasks whose state is in ``states``, newest first."""
    client = NmsClient(NmsConfig.from_config(config))
    try:
        document = await client.list_tasks()
    finally:
        await client.aclose()
    tasks = document.get("tasks")
    if not isinstance(tasks, list):
        return []
    rows: list[dict[str, object]] = []
    for task in tasks:
        row = _task_row(task)
        if row is not None and row["state"] in states:
            rows.append(row)
    return rows


async def cancel_nms_task(config: SampleConfig, task_id: str) -> bool:
    """Cancel one NMS Task.

    Returns:
        ``True`` when the NMS accepted the cancellation, ``False`` when it answered the known
        HTTP 503 (cancellation unsupported).

    Raises:
        protocol.nms_client.NmsError: when the NMS is unreachable or rejects the request.
        protocol.nms_client.NmsConfigError: when the NMS settings are incomplete.
    """
    client = NmsClient(NmsConfig.from_config(config))
    try:
        return await client.cancel_task(task_id)
    finally:
        await client.aclose()


async def read_task(config: SampleConfig, task_id: str) -> dict[str, object] | None:
    """Return one NMS Task row, or ``None`` when the NMS does not know it."""
    client = NmsClient(NmsConfig.from_config(config))
    try:
        try:
            task = await client.get_task(task_id)
        except NmsProtocolError as exc:
            # A Task that disappeared between the list and the send is simply missing; every
            # other NMS failure stays an error the caller must show.
            if "HTTP 404" in str(exc):
                return None
            raise
    finally:
        await client.aclose()
    return _task_row(task)


def _task_row(task: object) -> dict[str, object] | None:
    """Map one Task document to the compact row the picker shows."""
    if not isinstance(task, Mapping):
        return None
    task_id = _first_string(task, "id", "taskId", "task_id")
    if not task_id:
        return None
    task_t = _last_task_t(task)
    text = task_t or _task_text(task)
    return {
        "taskId": task_id,
        "contextId": _first_string(task, "contextId", "context_id"),
        "state": _state(task),
        "summary": _excerpt(_task_text(task)),
        "region": _match(_REGION_RE, text),
        "sites": _sites(text),
        "target": _excerpt(" ".join(_TARGET_RE.findall(text))),
    }


def _state(task: Mapping[str, object]) -> str:
    status = task.get("status")
    if not isinstance(status, Mapping):
        return ""
    state = status.get("state")
    return state.strip() if isinstance(state, str) else ""


def _task_text(task: Mapping[str, object]) -> str:
    """Return the newest human-readable message of the Task."""
    status = task.get("status")
    if isinstance(status, Mapping):
        message = _message_text(status.get("message"))
        if message:
            return message
    history = task.get("history")
    if isinstance(history, list):
        for entry in reversed(history):
            message = _message_text(entry)
            if message:
                return message
    return ""


def _message_text(message: object) -> str:
    if not isinstance(message, Mapping):
        return ""
    parts = message.get("parts")
    if not isinstance(parts, list):
        return ""
    texts = [
        part["text"].strip()
        for part in parts
        if isinstance(part, Mapping) and isinstance(part.get("text"), str) and part["text"].strip()
    ]
    return " ".join(texts)


def _last_task_t(task: Mapping[str, object]) -> str:
    """Return the newest Task-T payload carried by the Task history, or ``""``."""
    history = task.get("history")
    if not isinstance(history, list):
        return ""
    for entry in reversed(history):
        if not isinstance(entry, Mapping):
            continue
        metadata = entry.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        for key, value in metadata.items():
            if isinstance(key, str) and "Task-T" in key and isinstance(value, str) and value.strip():
                return value
    return ""


def _first_string(document: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    if match is None:
        return ""
    return match.group(1).strip()


def _sites(text: str) -> list[str]:
    match = _SITE_RE.search(text)
    if match is None:
        return []
    return [item.strip().strip('"“”') for item in re.split(r"[,，、]", match.group(1)) if item.strip()]


def _excerpt(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= _EXCERPT_LENGTH:
        return collapsed
    return f"{collapsed[:_EXCERPT_LENGTH - 1]}…"


__all__ = [
    "ACTIVE_STATES",
    "MODIFIABLE_STATES",
    "NMS_INPUT_REQUIRED_STATE",
    "NMS_SUBMITTED_STATE",
    "NMS_WORKING_STATE",
    "cancel_nms_task",
    "list_active_tasks",
    "list_modifiable_tasks",
    "read_task",
]
