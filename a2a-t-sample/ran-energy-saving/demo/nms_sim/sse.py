"""Server-sent-event streams of the simulator.

Strict mode reproduces the vendor behavior the client must survive: the stream never closes on its
own and emits a ``: heartbeat`` comment every 15 s. Fast mode keeps the same frames but removes
the wall-clock delays so tests finish immediately.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from demo.nms_sim.config import SimConfig
from demo.nms_sim.tasks import (
    TASK_COMPLETED,
    TASK_WORKING,
    SimTask,
)


def _data(payload: dict[str, object]) -> str:
    """Render one SSE ``data:`` frame."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _pause(seconds: float, heartbeat_seconds: float) -> AsyncIterator[str]:
    """Sleep for ``seconds``, yielding heartbeat comments every ``heartbeat_seconds``."""
    if seconds <= 0:
        return
    if heartbeat_seconds <= 0:
        await asyncio.sleep(seconds)
        return
    remaining = seconds
    while remaining > 0:
        step = min(heartbeat_seconds, remaining)
        await asyncio.sleep(step)
        remaining -= step
        yield ": heartbeat\n\n"


async def task_stream(
    task: SimTask,
    *,
    report: dict[str, object],
    artifact: dict[str, object],
    config: SimConfig,
) -> AsyncIterator[str]:
    """Stream one Task from ``SUBMITTED`` to ``COMPLETED``.

    The Task object is the same instance held by the store, so a concurrent ``GET /tasks/{id}``
    observes the transitions.
    """
    yield _data({"task": task.to_dict()})

    async for chunk in _pause(config.working_delay, config.heartbeat_seconds):
        yield chunk
    task.state = TASK_WORKING
    task.message = "Task is executing the energy-saving plan."
    yield _data(task.status_frame())

    async for chunk in _pause(config.completion_delay, config.heartbeat_seconds):
        yield chunk
    task.artifacts.append(artifact)
    yield _data(task.artifact_frame())
    task.state = TASK_COMPLETED
    task.message = "Task completed."
    yield _data(task.status_frame())

    if config.strict:
        # The vendor stream never closes on its own; the client breaks at the terminal status.
        while True:
            yield ": heartbeat\n\n"
            await asyncio.sleep(config.heartbeat_seconds or 15.0)


async def message_stream(
    text: str,
    *,
    context_id: str | None,
    config: SimConfig,
) -> AsyncIterator[str]:
    """Stream one negotiation message without creating a Task."""
    resolved_context = context_id or str(uuid.uuid4())
    message = {
        "messageId": str(uuid.uuid4()),
        "contextId": resolved_context,
        "role": "ROLE_AGENT",
        "parts": [{"text": text}],
    }
    yield _data({"message": message})
    if config.strict:
        while True:
            yield ": heartbeat\n\n"
            await asyncio.sleep(config.heartbeat_seconds or 15.0)
