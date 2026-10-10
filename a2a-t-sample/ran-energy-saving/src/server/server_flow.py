from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import Task, TaskArtifactUpdateEvent, TaskState
from a2a_t.core.errors.exceptions import ContentValidationError
from common.a2a_adapter import build_metadata_artifact, build_status, emit_status_update
from common.logging_utils import format_payload_log, format_stage_log
from google.protobuf.json_format import MessageToDict

from server.constants_data import (
    ARTIFACT_SEND_INTERVAL_SECONDS,
    ENERGY_SAVING_TEMPLATE_URI,
    SUBMITTED_MESSAGE,
    TASK_T_EXTENSION_URI,
    get_energy_saving_intent_report,
    get_energy_saving_progress_steps,
)
from server.task_schema import TASK_PARAM_SCHEMA

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
    progress_steps: list[str] | None = None,
    intent_report: dict[str, str] | None = None,
    sleep_fn: SleepFn | None = None,
    artifact_send_interval_seconds: float = ARTIFACT_SEND_INTERVAL_SECONDS,
    log_sink: object | None = None,
) -> None:
    """Validate the incoming Task-T prompt with A2A-T SDK, then stream the UC1 energy-saving flow.

    Flow (RAN energy-saving interface spec, UC1):
      1. Validate the A2A-Extensions header (Task-T) and extract the prompt text
      2. ``A2ATServer.validate_task_prompt_and_data_filling`` (A2A-T server SDK) validates the
         prompt and extracts the filled parameters
      3. Emit SUBMITTED, then REJECTED on failure
      4. Stream the UC1 progress messages as ``TASK_STATE_WORKING`` status updates
      5. Stream the energy-saving intent report as one ``TaskArtifactUpdateEvent`` (the report body
         travels in ``artifact.metadata[Task-T/v1]``); the task then stays long-running — no
         terminal state is emitted
      6. Exception during pushing -> emit FAILED
    """
    resolved_sleep = sleep_fn or asyncio.sleep
    resolved_progress_steps = get_energy_saving_progress_steps() if progress_steps is None else progress_steps
    resolved_report = get_energy_saving_intent_report() if intent_report is None else intent_report
    resolved_task_id = request_context.task_id or ""
    resolved_context_id = request_context.context_id or ""

    _require_task_extension(request_context)

    prompt_text = _extract_prompt_text(request_context)
    if log_sink is not None:
        log_sink(
            format_payload_log(
                role="server",
                stage="request-inbound",
                payload={"prompt_text": prompt_text},
            )
        )
        log_sink(
            format_stage_log(
                role="server",
                stage="sdk-call",
                detail=(
                    "A2ATServer.validate_task_prompt_and_data_filling("
                    f"template_uri={ENERGY_SAVING_TEMPLATE_URI}, prompt_chars={len(prompt_text)})"
                ),
            )
        )

    validation_failure: str | None = None
    try:
        filled_params = prompt_server.validate_task_prompt_and_data_filling(
            prompt=prompt_text,
            schema=TASK_PARAM_SCHEMA,
            template_uri=ENERGY_SAVING_TEMPLATE_URI,
        )
        if log_sink is not None:
            log_sink(
                format_stage_log(
                    role="server",
                    stage="sdk-result",
                    detail=f"success extracted_params={getattr(filled_params, 'data', {})}",
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

    # 3. On success → stream the UC1 progress messages (each a TASK_STATE_WORKING status update)
    try:
        for step_text in resolved_progress_steps:
            await emit_status_update(
                request_context=request_context,
                event_queue=event_queue,
                context_id=resolved_context_id,
                task_id=resolved_task_id,
                state=TaskState.TASK_STATE_WORKING,
                text=step_text,
            )
            if log_sink is not None:
                log_sink(format_stage_log(role="server", stage="task-progress", detail=step_text))
            # Pace the stream so the client observes the task progressing step by step.
            await resolved_sleep(artifact_send_interval_seconds)

        report_name = str(resolved_report.get("name", "energy saving intent report"))
        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=resolved_task_id,
                context_id=resolved_context_id,
                artifact=build_metadata_artifact(
                    name=report_name,
                    text=str(resolved_report.get("text", report_name)),
                    extension_uri=TASK_T_EXTENSION_URI,
                    metadata_text=str(resolved_report.get("metadata", "")),
                ),
                last_chunk=True,
            )
        )
        if log_sink is not None:
            log_sink(
                format_stage_log(
                    role="server",
                    stage="artifact-pushed",
                    detail=f"name={report_name} metadata[{TASK_T_EXTENSION_URI}]",
                )
            )
            log_sink(json.dumps(dict(resolved_report), ensure_ascii=False))
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
