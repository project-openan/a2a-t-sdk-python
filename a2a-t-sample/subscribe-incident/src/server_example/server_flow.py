from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping

from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import Task, TaskArtifactUpdateEvent, TaskState
from common.a2a_adapter import build_artifact, build_status, emit_status_update
from common.logging_utils import format_payload_log, format_stage_log
from google.protobuf.json_format import MessageToDict

from server_example.constants_data import (
    ARTIFACT_SEND_INTERVAL_SECONDS,
    INCIDENT_ARTIFACT_DATA,
    NOTIFICATION_T_EXTENSION_URI,
    SUBMITTED_MESSAGE,
    WORKING_MESSAGE,
)

SleepFn = Callable[[float], Awaitable[None]]


def _require_notification_extension(request_context: RequestContext) -> None:
    """Validate that the request carries the Notification-T extension header.

    The A2A-Extensions header must contain the Notification-T extension
    URI; raises ValueError if it is not present.
    """
    ext_set = request_context.call_context.requested_extensions
    if NOTIFICATION_T_EXTENSION_URI not in ext_set:
        raise ValueError("a2a client extensions is not exist.")


def _extract_prompt_text(request_context: RequestContext) -> str:
    """Extract the prompt text from the incoming message metadata.

    Reads from metadata under the Notification-T extension URI; raises ValueError if no
    metadata is present.
    """
    if request_context.message is None or request_context.message.metadata is None:
        raise ValueError("Expected message metadata for Notification-T prompt")
    metadata = MessageToDict(request_context.message.metadata)
    prompt_text = str(metadata.get(NOTIFICATION_T_EXTENSION_URI, ""))
    return prompt_text


async def execute_server_flow(
    *,
    request_context: RequestContext,
    event_queue: EventQueue,
    prompt_server: object,
    max_artifacts: int | None = None,
    sleep_fn: SleepFn | None = None,
    artifact_send_interval_seconds: float = ARTIFACT_SEND_INTERVAL_SECONDS,
    incident_artifact_data: Mapping[str, object] | None = None,
    log_sink: object | None = None,
) -> None:
    """Validate the incoming prompt via SDK, then stream Incident artifacts.

    Flow:
      1. Validate A2A-Extensions header
      2. Extract prompt text from metadata
      3. Emit SUBMITTED
      4. Check compliance — on failure emit REJECTED; on success emit WORKING
      5. Push artifacts every 5s indefinitely until interrupted
      6. Exception → emit FAILED

    Pushes artifacts to the client indefinitely (or until max_artifacts is reached).
    """
    resolved_sleep = sleep_fn or asyncio.sleep
    resolved_artifact_data = INCIDENT_ARTIFACT_DATA if incident_artifact_data is None else incident_artifact_data
    resolved_task_id = request_context.task_id or ""
    resolved_context_id = request_context.context_id or ""

    _require_notification_extension(request_context)

    payload_text = _extract_prompt_text(request_context)
    if log_sink is not None:
        log_sink(
            format_payload_log(
                role="server",
                stage="request-inbound",
                payload={"prompt_text": payload_text},
            )
        )

    check_result = prompt_server.check_task_prompt(processed_prompt_text=payload_text)
    if log_sink is not None:
        log_sink(
            format_stage_log(
                role="server",
                stage="prompt-validation",
                detail="success" if check_result.success else "failure",
            )
        )

    # 1. SUBMITTED — always emitted before validation result
    task = Task(
        id=resolved_task_id,
        context_id=resolved_context_id,
        status=build_status(
            context_id=resolved_context_id,
            task_id=resolved_task_id,
            state=TaskState.TASK_STATE_SUBMITTED,
            text=SUBMITTED_MESSAGE,
        ),
    )
    request_context.current_task = Task()
    request_context.current_task.CopyFrom(task)
    await event_queue.enqueue_event(task)
    if log_sink is not None:
        log_sink(
            format_stage_log(
                role="server",
                stage="task-status",
                detail="TASK_STATE_SUBMITTED",
            )
        )

    # 2. On failure → REJECTED (not an exception)
    if not check_result.success:
        await emit_status_update(
            request_context=request_context,
            event_queue=event_queue,
            context_id=resolved_context_id,
            task_id=resolved_task_id,
            state=TaskState.TASK_STATE_REJECTED,
            text=f"Prompt validation failed: {check_result.failure}",
        )
        if log_sink is not None:
            log_sink(
                format_stage_log(
                    role="server",
                    stage="task-status",
                    detail="TASK_STATE_REJECTED",
                )
            )
        return

    # 3. On success → WORKING + infinite artifacts
    await emit_status_update(
        request_context=request_context,
        event_queue=event_queue,
        context_id=resolved_context_id,
        task_id=resolved_task_id,
        state=TaskState.TASK_STATE_WORKING,
        text=WORKING_MESSAGE,
    )
    if log_sink is not None:
        log_sink(
            format_stage_log(
                role="server",
                stage="task-status",
                detail="TASK_STATE_WORKING",
            )
        )

    artifacts_pushed = 0
    try:
        while max_artifacts is None or artifacts_pushed < max_artifacts:
            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    task_id=resolved_task_id,
                    context_id=resolved_context_id,
                    artifact=build_artifact(artifact_data=resolved_artifact_data),
                    last_chunk=True,
                )
            )
            artifacts_pushed += 1
            if log_sink is not None:
                log_sink(
                    format_stage_log(
                        role="server",
                        stage="artifact-pushed",
                        detail=f"count={artifacts_pushed}",
                    )
                )
                log_sink(
                    json.dumps(
                        dict(resolved_artifact_data),
                        ensure_ascii=False,
                    )
                )
            await resolved_sleep(artifact_send_interval_seconds)
    except Exception as exc:
        # Exception → FAILED
        if log_sink is not None:
            log_sink(
                format_stage_log(
                    role="server",
                    stage="task-status",
                    detail=f"TASK_STATE_FAILED: {exc}",
                )
            )
        await emit_status_update(
            request_context=request_context,
            event_queue=event_queue,
            context_id=resolved_context_id,
            task_id=resolved_task_id,
            state=TaskState.TASK_STATE_FAILED,
            text=f"Mock incident stream failed: {exc}",
        )
        raise
