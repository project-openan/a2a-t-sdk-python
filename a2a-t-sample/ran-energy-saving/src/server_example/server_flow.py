from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import Task, TaskArtifactUpdateEvent, TaskState
from a2a_t.core.errors.exceptions import ContentValidationError
from common.a2a_adapter import build_artifact, build_status, emit_status_update
from common.logging_utils import format_payload_log, format_stage_log
from google.protobuf.json_format import MessageToDict

from server_example.constants_data import (
    ARTIFACT_SEND_INTERVAL_SECONDS,
    COMPLETED_MESSAGE,
    ENERGY_SAVING_TEMPLATE_URI,
    SUBMITTED_MESSAGE,
    TASK_T_EXTENSION_URI,
    WORKING_MESSAGE,
    get_energy_saving_task_steps,
)
from server_example.task_schema import TASK_PARAM_SCHEMA

SleepFn = Callable[[float], Awaitable[None]]


def _require_task_extension(request_context: RequestContext) -> None:
    """Validate that the request carries the Task-T extension header.

    The A2A-Extensions header must contain the Task-T extension URI; raises ValueError if it is
    not present.
    """
    ext_set = request_context.call_context.requested_extensions
    if TASK_T_EXTENSION_URI not in ext_set:
        raise ValueError("a2a client extensions is not exist.")


def _extract_prompt_text(request_context: RequestContext) -> str:
    """Extract the prompt text from metadata under the Task-T extension URI."""
    if request_context.message is None or request_context.message.metadata is None:
        raise ValueError("Expected message metadata for Task-T prompt")
    metadata = MessageToDict(request_context.message.metadata)
    return str(metadata.get(TASK_T_EXTENSION_URI, ""))


async def execute_server_flow(
    *,
    request_context: RequestContext,
    event_queue: EventQueue,
    prompt_server: object,
    max_artifacts: int | None = None,
    sleep_fn: SleepFn | None = None,
    artifact_send_interval_seconds: float = ARTIFACT_SEND_INTERVAL_SECONDS,
    task_steps: list[dict[str, object]] | None = None,
    log_sink: object | None = None,
) -> None:
    """Validate the incoming Task-T prompt with A2A-T SDK, then stream the energy-saving steps.

    Flow:
      1. Validate the A2A-Extensions header (Task-T) and extract the prompt text
      2. ``A2ATServer.validate_task_prompt_and_data_filling`` (A2A-T server SDK) validates the
         prompt and extracts the filled parameters
      3. Emit SUBMITTED, then REJECTED on failure or WORKING on success
      4. Stream the ordered energy-saving task steps as artifacts (plan -> cell selection ->
         activation -> intent report), then emit COMPLETED
      5. Exception during pushing -> emit FAILED
    """
    resolved_sleep = sleep_fn or asyncio.sleep
    resolved_steps = get_energy_saving_task_steps() if task_steps is None else task_steps
    resolved_task_id = request_context.task_id or ""
    resolved_context_id = request_context.context_id or ""

    _require_task_extension(request_context)

    payload_text = _extract_prompt_text(request_context)
    if log_sink is not None:
        log_sink(
            format_payload_log(
                role="server",
                stage="request-inbound",
                payload={"prompt_text": payload_text},
            )
        )
        log_sink(
            format_stage_log(
                role="server",
                stage="sdk-call",
                detail=(
                    "A2ATServer.validate_task_prompt_and_data_filling("
                    f"template_uri={ENERGY_SAVING_TEMPLATE_URI}, prompt_chars={len(payload_text)})"
                ),
            )
        )

    validation_failure: str | None = None
    try:
        filled = prompt_server.validate_task_prompt_and_data_filling(
            prompt=payload_text,
            schema=TASK_PARAM_SCHEMA,
            template_uri=ENERGY_SAVING_TEMPLATE_URI,
        )
        if log_sink is not None:
            log_sink(
                format_stage_log(
                    role="server",
                    stage="sdk-result",
                    detail=f"success extracted_params={getattr(filled, 'data', {})}",
                )
            )
    except ContentValidationError as exc:
        validation_failure = str(exc)
        if log_sink is not None:
            log_sink(
                format_stage_log(role="server", stage="sdk-result", detail=f"failure {validation_failure}")
            )
    except ValueError as exc:
        validation_failure = str(exc)
        if log_sink is not None:
            log_sink(
                format_stage_log(role="server", stage="sdk-result", detail=f"invalid input {validation_failure}")
            )

    # 1. SUBMITTED — always emitted before the validation result
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
        log_sink(format_stage_log(role="server", stage="task-status", detail="TASK_STATE_SUBMITTED"))

    # 2. On failure → REJECTED (not an exception)
    if validation_failure is not None:
        await emit_status_update(
            request_context=request_context,
            event_queue=event_queue,
            context_id=resolved_context_id,
            task_id=resolved_task_id,
            state=TaskState.TASK_STATE_REJECTED,
            text=f"Prompt validation failed: {validation_failure}",
        )
        if log_sink is not None:
            log_sink(format_stage_log(role="server", stage="task-status", detail="TASK_STATE_REJECTED"))
        return

    # 3. On success → WORKING + stream the task-execution steps
    await emit_status_update(
        request_context=request_context,
        event_queue=event_queue,
        context_id=resolved_context_id,
        task_id=resolved_task_id,
        state=TaskState.TASK_STATE_WORKING,
        text=WORKING_MESSAGE,
    )
    if log_sink is not None:
        log_sink(format_stage_log(role="server", stage="task-status", detail="TASK_STATE_WORKING"))

    artifacts_pushed = 0
    try:
        for step in resolved_steps:
            if max_artifacts is not None and artifacts_pushed >= max_artifacts:
                break
            artifact_name = str(step.get("name", "energySaving.Step"))
            step_label = str(step.get("label", ""))
            step_data = step.get("data", {})
            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    task_id=resolved_task_id,
                    context_id=resolved_context_id,
                    artifact=build_artifact(artifact_data=step_data, name=artifact_name),
                    last_chunk=True,
                )
            )
            artifacts_pushed += 1
            if log_sink is not None:
                log_sink(
                    format_stage_log(
                        role="server",
                        stage="artifact-pushed",
                        detail=f"count={artifacts_pushed} name={artifact_name} step={step_label}",
                    )
                )
                log_sink(json.dumps(dict(step_data), ensure_ascii=False))
            # Pace the stream so the client observes the task executing step by step.
            await resolved_sleep(artifact_send_interval_seconds)

        await emit_status_update(
            request_context=request_context,
            event_queue=event_queue,
            context_id=resolved_context_id,
            task_id=resolved_task_id,
            state=TaskState.TASK_STATE_COMPLETED,
            text=COMPLETED_MESSAGE,
        )
        if log_sink is not None:
            log_sink(format_stage_log(role="server", stage="task-status", detail="TASK_STATE_COMPLETED"))
    except Exception as exc:
        if log_sink is not None:
            log_sink(format_stage_log(role="server", stage="task-status", detail=f"TASK_STATE_FAILED: {exc}"))
        await emit_status_update(
            request_context=request_context,
            event_queue=event_queue,
            context_id=resolved_context_id,
            task_id=resolved_task_id,
            state=TaskState.TASK_STATE_FAILED,
            text=f"Mock energy saving stream failed: {exc}",
        )
        raise
