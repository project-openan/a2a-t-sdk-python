from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import Message, Role, Task, TaskArtifactUpdateEvent, TaskState
from a2a_t.core.errors.exceptions import ContentValidationError, SlotValidationError
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.negotiation.content.models import NegotiationItem
from client import scenario_data
from common.a2a_adapter import build_metadata_artifact, build_status, emit_status_update
from common.logging_utils import format_payload_log, format_stage_log
from google.protobuf.json_format import MessageToDict

if TYPE_CHECKING:
    from a2a_t.server.a2at_server import A2ATServer

from server.constants_data import (
    ARTIFACT_SEND_INTERVAL_SECONDS,
    CONTENT_ERROR_CODE_FEASIBILITY,
    CONTENT_ERROR_CODE_INFORMATION,
    CONTENT_ERROR_CODE_TARGET,
    ENERGY_SAVING_TEMPLATE_URI,
    FEASIBILITY_NEGOTIATION_PROPOSE_TEMPLATE_URI,
    NEGOTIATION_MISSING_ITEM_HINT,
    NEGOTIATION_PROPOSE_TEMPLATE_URI,
    NEGOTIATION_RELATIONSHIP,
    NEGOTIATION_T_EXTENSION_URI,
    PROPOSE_MESSAGE,
    SUBMITTED_MESSAGE,
    TARGET_NEGOTIATION_PROPOSE_TEMPLATE_URI,
    TASK_T_EXTENSION_URI,
)
from server.task_execution import SleepFn, TaskExecutionService, Uc1DemoExecutionService
from server.task_schema import TASK_PARAM_SCHEMA

#: Negotiation types the server-side propose generation dispatches on. The first content-validation
#: error's code selects the type (plan R1); the type picks the propose template URI, the typed
#: content and the natural-language rendering of the from-text channel.
NEGOTIATION_TYPE_INFORMATION = "information"
NEGOTIATION_TYPE_TARGET = "target"
NEGOTIATION_TYPE_FEASIBILITY = "feasibility"

#: Sample-level convention mapping the first content-validation error's code to the Negotiation-T
#: type it proposes (plan R6): ``content.param_missing`` -> information, ``content.value_not_allowed``
#: -> target, ``content.rule_violation`` -> feasibility. This is a sample convention, not an SDK
#: rule — customers edit their copy of this table to their own error codes (see the sample README,
#: Negotiation-T chapter); codes absent from the table keep the plain rejection.
_NEGOTIATION_TYPE_BY_ERROR_CODE: dict[str, str] = {
    CONTENT_ERROR_CODE_INFORMATION: NEGOTIATION_TYPE_INFORMATION,
    CONTENT_ERROR_CODE_TARGET: NEGOTIATION_TYPE_TARGET,
    CONTENT_ERROR_CODE_FEASIBILITY: NEGOTIATION_TYPE_FEASIBILITY,
}


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


def _is_negotiation_continuation(request_context: RequestContext) -> bool:
    """Report whether the inbound message metadata carries the Negotiation-T extension URI.

    The Negotiation-T URI in the message metadata marks the negotiation continuation: the client
    re-sends the Task-T prompt with the data the server's propose asked for.
    """
    if request_context.message is None or request_context.message.metadata is None:
        return False
    metadata = MessageToDict(request_context.message.metadata)
    return NEGOTIATION_T_EXTENSION_URI in metadata


def _slot_description(schema: dict[str, object], slot_name: str) -> str | None:
    """Look up the description of one slot in the caller parameter schema (demo ``_slot_description``)."""
    properties = schema.get("properties")
    if isinstance(properties, dict):
        slot = properties.get(slot_name)
        if isinstance(slot, dict):
            description = slot.get("description")
            if isinstance(description, str) and description.strip():
                return description
    return None


def _missing_item(slot_name: str) -> NegotiationItem:
    """Build one missing-item entry from the schema description and the hint template."""
    description = _slot_description(TASK_PARAM_SCHEMA, slot_name)
    hint = NEGOTIATION_MISSING_ITEM_HINT.replace("{slot}", slot_name).replace("{description}", description or "")
    return NegotiationItem(name=slot_name, value=hint)


def _missing_items_from_errors(errors: Sequence[SlotValidationError] | None) -> list[NegotiationItem]:
    """Build the Negotiation-T missing-item list from the slot-named validation errors.

    Errors without a slot name (e.g. ``llm.invocation_failed``) contribute nothing; an empty
    result keeps the caller on the plain REJECTED branch (data-driven trigger, plan R3).
    """
    missing: list[NegotiationItem] = []
    for error in errors or []:
        slot_name = getattr(error, "slot_name", None)
        if isinstance(slot_name, str) and slot_name.strip():
            missing.append(_missing_item(slot_name))
    return missing


def _resolve_negotiation_type(
    errors: Sequence[SlotValidationError] | None, missing_items: list[NegotiationItem]
) -> str | None:
    """Return the negotiation type the failed Task-T validation proposes, ``None`` to reject.

    The FIRST error entry's code selects the type through the sample-level
    ``_NEGOTIATION_TYPE_BY_ERROR_CODE`` table (plan R6): ``content.param_missing`` keeps the v1
    information path (which still proposes only when the errors name slots),
    ``content.value_not_allowed`` selects the target propose and ``content.rule_violation`` the
    feasibility propose. Any other code — or a codeless error — selects no negotiation type, so the
    caller rejects the task exactly as v1 did (including the slotless case).
    """
    if not errors:
        return None
    code = str(getattr(errors[0], "code", "") or "")
    negotiation_type = _NEGOTIATION_TYPE_BY_ERROR_CODE.get(code)
    if negotiation_type == NEGOTIATION_TYPE_INFORMATION and not missing_items:
        return None
    return negotiation_type


def _numbered_items(items: Iterable[tuple[str, str | None]]) -> str:
    """Render items as one numbered list: ``N. name：value；`` per item.

    The value is omitted when blank; the full-width separators render the list the same way in
    both languages.
    """
    lines: list[str] = []
    for index, (name, value) in enumerate(items, start=1):
        line = f"{index}. {name}"
        if value is not None and value.strip():
            line += f"：{value}"
        lines.append(f"{line}；")
    return "".join(lines)


def _negotiation_propose_text(
    negotiation_type: str,
    missing_items: list[NegotiationItem],
    *,
    env_path: Path | None,
) -> str:
    """Render the natural-language input of the from-text propose generation for one type.

    The rendering carries the same content as the typed propose builders (the scenario accessors
    mirror the canned extraction responses), so the extraction step of the from-text channel
    returns the canonical content and both channels produce equivalent messages.
    """
    if negotiation_type == NEGOTIATION_TYPE_TARGET:
        description = scenario_data.negotiation_target_description(env_path=env_path)
        intent = scenario_data.negotiation_target_intent_understanding(env_path=env_path)
        clarifications = scenario_data.negotiation_target_clarifications(env_path=env_path)
        return f"{description} {_numbered_items(intent)} {_numbered_items(clarifications)}"
    if negotiation_type == NEGOTIATION_TYPE_FEASIBILITY:
        description = scenario_data.negotiation_feasibility_description(env_path=env_path)
        infeasibility = scenario_data.negotiation_feasibility_infeasibility(env_path=env_path)
        return f"{description} {_numbered_items(infeasibility)}"
    text = _numbered_items((item.name, item.value) for item in missing_items)
    relationship = NEGOTIATION_RELATIONSHIP if len(missing_items) > 1 else None
    if relationship:
        text += relationship
    return text


def _negotiation_propose_template_uri(negotiation_type: str) -> str:
    """Return the Negotiation-T propose template URI addressed for one negotiation type."""
    if negotiation_type == NEGOTIATION_TYPE_TARGET:
        return TARGET_NEGOTIATION_PROPOSE_TEMPLATE_URI
    if negotiation_type == NEGOTIATION_TYPE_FEASIBILITY:
        return FEASIBILITY_NEGOTIATION_PROPOSE_TEMPLATE_URI
    return NEGOTIATION_PROPOSE_TEMPLATE_URI


async def _emit_negotiation_propose(
    *,
    request_context: RequestContext,
    event_queue: EventQueue,
    prompt_server: A2ATServer,
    missing_items: list[NegotiationItem],
    negotiation_type: str = NEGOTIATION_TYPE_INFORMATION,
    env_path: Path | None = None,
    context_id: str,
    log_sink: object | None,
) -> None:
    """Generate the Negotiation-T propose for the negotiation type and emit it as a Message.

    The negotiation runs before the task is created: the propose is a standalone A2A ``Message``
    (role ``ROLE_AGENT``, same ``contextId`` as the request, no ``taskId``), not a Task status
    event. The type selects the propose template URI and the content (plan R1/R3). The propose is
    written as a business message and generated through the from-text API: the SDK first extracts
    the canonical typed content (one LLM call), then renders the addressed template. The accept is
    the client's decision; how it is generated is the client's choice. The propose content travels
    in the message metadata (``propose.build_metadata_content()``); the templateUri inside carries
    the per-type propose template URI, which is what the client dispatches its accept on.
    """
    propose_context = NegotiationContext(
        id=str(uuid.uuid4()),
        round=1,
        max_rounds=NegotiationContext.DEFAULT_MAX_ROUNDS,
        performative=NegotiationPerformative.PROPOSE,
    )
    template_uri = _negotiation_propose_template_uri(negotiation_type)
    propose_text = _negotiation_propose_text(negotiation_type, missing_items, env_path=env_path)
    propose = prompt_server.generate_negotiation_propose_prompt_from_text(
        propose_text, propose_context, template_uri
    )
    if log_sink is not None:
        log_sink(
            format_stage_log(
                role="server",
                stage="negotiation-propose",
                detail=(
                    f"generated (type={negotiation_type} round={propose_context.round}) "
                    f"items={[(item.name, item.value) for item in missing_items]}"
                ),
            )
        )
    propose_message = Message(context_id=context_id, role=Role.ROLE_AGENT)
    propose_message.message_id = str(uuid.uuid4())
    propose_message.parts.add(text=PROPOSE_MESSAGE)
    for key, value in propose.build_metadata_content().items():
        propose_message.metadata[key] = value
    await event_queue.enqueue_event(propose_message)
    if log_sink is not None:
        log_sink(format_stage_log(role="server", stage="negotiation-propose-sent", detail="message emitted"))


async def _execute_and_emit_result(
    *,
    request_context: RequestContext,
    event_queue: EventQueue,
    execution_service: TaskExecutionService,
    params: dict[str, object],
    task_id: str,
    context_id: str,
    log_sink: object | None,
) -> None:
    """Execute the negotiated task through the ``TaskExecutionService`` integration point and push its artifact.

    INTEGRATION POINT (server): the flow owns the A2A emission — each ``emit_progress`` call is a
    ``TASK_STATE_WORKING`` status update, the returned ``TaskExecutionResult`` becomes the report
    artifact, and an exception of the service is caught here, emitted as ``TASK_STATE_FAILED`` with
    a generic text and re-raised. The service only produces the progress texts and the result.
    """

    async def emit_progress(text: str) -> None:
        await emit_status_update(
            request_context=request_context,
            event_queue=event_queue,
            context_id=context_id,
            task_id=task_id,
            state=TaskState.TASK_STATE_WORKING,
            text=text,
        )
        if log_sink is not None:
            log_sink(format_stage_log(role="server", stage="task-progress", detail=text))

    try:
        result = await execution_service.execute(params, emit_progress=emit_progress)
        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                artifact=build_metadata_artifact(
                    name=result.report_name,
                    text=result.report_text,
                    extension_uri=TASK_T_EXTENSION_URI,
                    metadata_text=result.report_metadata,
                ),
                last_chunk=True,
            )
        )
        if log_sink is not None:
            log_sink(
                format_stage_log(
                    role="server",
                    stage="artifact-pushed",
                    detail=f"name={result.report_name} metadata[{TASK_T_EXTENSION_URI}]",
                )
            )
            # Same report shape the flow used to log from the demo's report dict.
            log_sink(
                json.dumps(
                    {
                        "name": result.report_name,
                        "text": result.report_text,
                        "metadata": result.report_metadata,
                    },
                    ensure_ascii=False,
                )
            )
    except Exception as exc:
        if log_sink is not None:
            log_sink(format_stage_log(role="server", stage="task-status", detail=f"TASK_STATE_FAILED: {exc}"))
        await emit_status_update(
            request_context=request_context,
            event_queue=event_queue,
            context_id=context_id,
            task_id=task_id,
            state=TaskState.TASK_STATE_FAILED,
            text=f"Task execution failed: {exc}",
        )
        raise


async def execute_server_flow(
    *,
    request_context: RequestContext,
    event_queue: EventQueue,
    prompt_server: A2ATServer,
    progress_steps: list[str] | None = None,
    intent_report: dict[str, str] | None = None,
    sleep_fn: SleepFn | None = None,
    artifact_send_interval_seconds: float = ARTIFACT_SEND_INTERVAL_SECONDS,
    log_sink: object | None = None,
    env_path: Path | None = None,
    execution_service: TaskExecutionService | None = None,
) -> None:
    """Validate the incoming Task-T prompt with A2A-T SDK, then stream the UC1 energy-saving flow.

    ``execution_service`` is the server-side INTEGRATION POINT
    (:class:`server.task_execution.TaskExecutionService`): the negotiated task itself is executed
    through it and the flow only emits the A2A events. ``None`` (the default) keeps the demo
    behavior — an internal :class:`Uc1DemoExecutionService` is constructed from the legacy
    ``progress_steps`` / ``intent_report`` / ``sleep_fn`` / ``artifact_send_interval_seconds``
    parameters, whose defaults preserve the pre-integration-point behavior for existing call sites and tests.

    Flow (RAN energy-saving interface spec, UC1):
      1. Validate the A2A-Extensions header (Task-T) and extract the prompt text
      2. ``A2ATServer.validate_task_prompt_and_data_filling`` (A2A-T server SDK) validates the
         prompt and extracts the filled parameters
      3. Pre-task negotiation: when the first validation error's code selects a negotiation type
         (plan R1: ``content.param_missing`` information with slot-named errors,
         ``content.value_not_allowed`` target, ``content.rule_violation`` feasibility), emit a
         standalone Negotiation-T propose ``Message`` and return — the task is not created. The
         propose is generated from a natural-language business message through the from-text API
         (data-driven trigger, the server reads no scenario flag)
      4. The client's accept arrives as a new message (Negotiation-T URI in the metadata); its
         Task-T prompt is validated, then the task is created (SUBMITTED) and executed
      5. On success: execute the task through the integration point — each progress line becomes a
         ``TASK_STATE_WORKING`` status update and the ``TaskExecutionResult`` becomes the report
         artifact as one ``TaskArtifactUpdateEvent`` (the report body travels in
         ``artifact.metadata[Task-T/v1]``); the task then stays long-running — no terminal state
         is emitted
      6. A failure that does not lead to a propose (or a failed accept) -> SUBMITTED then REJECTED
      7. Exception during execution or artifact pushing -> emit FAILED (generic text) and re-raise
    """
    resolved_execution_service = execution_service or Uc1DemoExecutionService(
        progress_steps=progress_steps,
        intent_report=intent_report,
        sleep_fn=sleep_fn,
        artifact_send_interval_seconds=artifact_send_interval_seconds,
    )
    resolved_task_id = request_context.task_id or ""
    resolved_context_id = request_context.context_id or ""

    _require_task_extension(request_context)

    negotiation_continuation = _is_negotiation_continuation(request_context)
    if negotiation_continuation and log_sink is not None:
        log_sink(
            format_stage_log(
                role="server",
                stage="negotiation-continuation",
                detail="Negotiation-T message detected, continuing the accepted propose",
            )
        )

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
    content_errors: Sequence[SlotValidationError] | None = None
    missing_items: list[NegotiationItem] = []
    # The object parameter mapping the execution integration point consumes; set on validation success. The
    # sample schema is object-shaped, so the filled data is always a mapping — the empty mapping
    # only keeps the (unused by this sample) array-shaped corner type-safe.
    filled_params_data: dict[str, object] = {}
    try:
        filled_params = prompt_server.validate_task_prompt_and_data_filling(
            prompt=prompt_text,
            schema=TASK_PARAM_SCHEMA,
            template_uri=ENERGY_SAVING_TEMPLATE_URI,
        )
        filled_data = filled_params.data
        filled_params_data = filled_data if isinstance(filled_data, dict) else {}
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
        content_errors = exc.errors
        missing_items = _missing_items_from_errors(exc.errors)
        if log_sink is not None:
            log_sink(format_stage_log(role="server", stage="sdk-result", detail=f"failure {validation_failure}"))
    except ValueError as exc:
        validation_failure = str(exc)
        if log_sink is not None:
            log_sink(format_stage_log(role="server", stage="sdk-result", detail=f"invalid input {validation_failure}"))

    # Pre-task negotiation: a failed validation that maps to a negotiation type is answered with a
    # standalone propose Message — no Task is created. The client's accept comes back through this
    # same flow (negotiation_continuation) and creates the task once it validates.
    if validation_failure is not None and not negotiation_continuation:
        negotiation_type = _resolve_negotiation_type(content_errors, missing_items)
        if negotiation_type is not None:
            await _emit_negotiation_propose(
                request_context=request_context,
                event_queue=event_queue,
                prompt_server=prompt_server,
                missing_items=missing_items,
                negotiation_type=negotiation_type,
                env_path=env_path,
                context_id=resolved_context_id,
                log_sink=log_sink,
            )
            return

    # The task is created only after the request (or the negotiation accept) validates.
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

    # A failure that does not lead to a propose (or a failed accept) → REJECTED.
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

    # Success → execute through the integration point; the task stays long-running (WORKING) after
    # the report artifact — no terminal state is emitted.
    await _execute_and_emit_result(
        request_context=request_context,
        event_queue=event_queue,
        execution_service=resolved_execution_service,
        params=filled_params_data,
        task_id=resolved_task_id,
        context_id=resolved_context_id,
        log_sink=log_sink,
    )
