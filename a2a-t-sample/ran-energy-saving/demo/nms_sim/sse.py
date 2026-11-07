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

from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    TEMPLATE_URI_METADATA_KEY,
)

from demo.nms_sim.config import SimConfig
from demo.nms_sim.tasks import (
    TASK_CANCELED,
    TASK_COMPLETED,
    TASK_WORKING,
    SimTask,
)


async def status_stream(task: SimTask, *, config: SimConfig) -> AsyncIterator[str]:
    """Stream one non-terminal Task state (INPUT_REQUIRED / REJECTED) and keep the SSE open."""
    yield _data({"task": task.to_dict()})
    yield _data(task.status_frame())
    if config.strict:
        while True:
            yield ": heartbeat\n\n"
            await asyncio.sleep(config.heartbeat_seconds or 15.0)
            if task.state == TASK_CANCELED:
                yield _data(task.status_frame())
                return


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
    hold: bool = False,
) -> AsyncIterator[str]:
    """Stream one Task from ``SUBMITTED`` to ``COMPLETED``.

    The Task object is the same instance held by the store, so a concurrent ``GET /tasks/{id}``
    observes the transitions.
    """
    yield _data({"task": task.to_dict()})

    async for chunk in _pause(config.working_delay, config.heartbeat_seconds):
        yield chunk
    if task.state == TASK_CANCELED:
        # The cancel route runs while this generator sleeps; never overwrite the terminal state.
        yield _data(task.status_frame())
        return
    task.state = TASK_WORKING
    task.message = "Task is executing the energy-saving plan."
    yield _data(task.status_frame())

    async for chunk in _pause(config.completion_delay, config.heartbeat_seconds):
        yield chunk
    if task.state == TASK_CANCELED:
        yield _data(task.status_frame())
        return
    task.artifacts.append(artifact)
    yield _data(task.artifact_frame())
    if hold:
        # Local rehearsal aid: the report is published but the Task stays WORKING, so the
        # presentation's Modify picker can select it. Continuing the Task completes it.
        task.state = TASK_WORKING
        task.message = "Task is running; ready to be modified."
        yield _data(task.status_frame())
        # Close the stream even in strict mode: the creating run ends as incomplete instead of
        # hanging, while the Task stays WORKING for the Modify picker.
        return
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
    structured: bool = True,
) -> AsyncIterator[str]:
    """Stream one negotiation message without creating a Task.

    When ``structured`` is true (the request declared Negotiation-T), the full negotiation body
    travels in the ``Negotiation-T`` metadata and the text part is only a pointer, matching the
    vendor. When it is false, the message is a bare Message with empty metadata and the full text
    in its part, which the client must treat as an explicit negotiation-required signal.
    """
    resolved_context = context_id or str(uuid.uuid4())
    metadata: dict[str, object] = {}
    part_text = text
    if structured:
        metadata = {
            NEGOTIATION_T_EXTENSION_URI: text,
            TEMPLATE_URI_METADATA_KEY: "Negotiation-T/info-negotiation/propose/v1",
            NEGOTIATION_CONTEXT_METADATA_KEY: {
                "id": str(uuid.uuid4()),
                "round": 1,
                "maxRounds": 5,
                "performative": "PROPOSE",
            },
        }
        part_text = "See metadata for details."
    message = {
        "messageId": str(uuid.uuid4()),
        "contextId": resolved_context,
        "role": "ROLE_AGENT",
        "parts": [{"text": part_text}],
        "metadata": metadata,
    }
    yield _data({"message": message})
    if config.strict:
        while True:
            yield ": heartbeat\n\n"
            await asyncio.sleep(config.heartbeat_seconds or 15.0)
