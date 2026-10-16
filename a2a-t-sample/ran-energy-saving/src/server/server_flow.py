from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import Task, TaskArtifactUpdateEvent, TaskState
from a2a_t.core.errors.exceptions import ContentValidationError, SlotValidationError
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.negotiation.content.enums import NegotiationAction
from a2a_t.negotiation.content.models import (
    FeasibilityProposeContent,
    InformationProposeContent,
    NegotiationItem,
    NegotiationProposeData,
    TargetProposeContent,
)
from client import scenario_data
from common.a2a_adapter import build_metadata_artifact, build_status, emit_status_update
from common.logging_utils import format_payload_log, format_stage_log
from google.protobuf.json_format import MessageToDict

if TYPE_CHECKING:
    from a2a_t.server.a2at_server import A2ATServer

from server.constants_data import (
    ARTIFACT_SEND_INTERVAL_SECONDS,
    CHANNEL_DATA,
    CHANNEL_TEXT,
    COMPLETED_MESSAGE,
    CONTENT_ERROR_CODE_FEASIBILITY,
    CONTENT_ERROR_CODE_INFORMATION,
    CONTENT_ERROR_CODE_TARGET,
    ENERGY_SAVING_TEMPLATE_URI,
    FEASIBILITY_NEGOTIATION_PROPOSE_TEMPLATE_URI,
    INPUT_REQUIRED_MESSAGE,
    NEGOTIATION_MISSING_ITEM_HINT,
    NEGOTIATION_PROPOSE_TEMPLATE_URI,
    NEGOTIATION_RELATIONSHIP,
    NEGOTIATION_T_EXTENSION_URI,
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
#: -> target, ``content.semantic_conflict`` -> feasibility. This is a sample convention, not an SDK
#: rule — customers edit their copy of this table to their own error codes (see the integration
#: guide); codes absent from the table keep the plain rejection.
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

    The Negotiation-T URI in the message metadata marks the negotiation round-2 continuation: the
    client re-sends the Task-T prompt with the data the server's information propose asked for.
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
    ``content.value_not_allowed`` selects the target propose and ``content.semantic_conflict`` the
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
    """Render items as one numbered list: ``N. name：value；`` per item (the negotiation demo rule).

    The value is omitted when blank; the full-width separators keep the rendering identical to the
    ``a2a-t-sample/negotiation`` demo's ``_numbered_items`` for both languages.
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
        confirm_request = scenario_data.negotiation_target_confirm_request(env_path=env_path)
        return f"{description} {confirm_request}"
    if negotiation_type == NEGOTIATION_TYPE_FEASIBILITY:
        description = scenario_data.negotiation_feasibility_description(env_path=env_path)
        contents = scenario_data.negotiation_feasibility_contents(env_path=env_path)
        return f"{description} Contents to evaluate: {_numbered_items(contents)}"
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


def _negotiation_propose_data(
    negotiation_type: str,
    missing_items: list[NegotiationItem],
    context: NegotiationContext,
    *,
    env_path: Path | None,
) -> NegotiationProposeData:
    """Build the typed propose content of one negotiation type (plan R3 scenario semantics).

    Target proposes the clarified-target confirm-request round and feasibility proposes the
    feasibility evaluation request; both fill their scenario-owned texts from the
    ``client.scenario_data`` accessors. Information keeps the v1 missing-item propose.
    """
    if negotiation_type == NEGOTIATION_TYPE_TARGET:
        return NegotiationProposeData(
            context=context,
            content=TargetProposeContent(
                target_negotiation_description=scenario_data.negotiation_target_description(env_path=env_path),
                intent_understanding=None,
                alignment_and_clarification=None,
                request_for_clarification=None,
                target_confirm_request=scenario_data.negotiation_target_confirm_request(env_path=env_path),
            ),
        )
    if negotiation_type == NEGOTIATION_TYPE_FEASIBILITY:
        return NegotiationProposeData(
            context=context,
            content=FeasibilityProposeContent(
                feasibility_negotiation_description=scenario_data.negotiation_feasibility_description(
                    env_path=env_path
                ),
                action=NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
                contents_to_evaluate=[
                    NegotiationItem(name=name, value=value)
                    for name, value in scenario_data.negotiation_feasibility_contents(env_path=env_path)
                ],
                infeasibility_details_and_proposal=None,
                feasibility_confirm_request=None,
            ),
        )
    relationship = NEGOTIATION_RELATIONSHIP if len(missing_items) > 1 else None
    return NegotiationProposeData(
        context=context,
        content=InformationProposeContent(items=missing_items, relationship=relationship),
    )


async def _emit_negotiation_propose(
    *,
    request_context: RequestContext,
    event_queue: EventQueue,
    prompt_server: A2ATServer,
    missing_items: list[NegotiationItem],
    negotiation_type: str = NEGOTIATION_TYPE_INFORMATION,
    channel: str = CHANNEL_DATA,
    env_path: Path | None = None,
    task_id: str,
    context_id: str,
    log_sink: object | None,
) -> None:
    """Generate the Negotiation-T propose for the negotiation type and emit INPUT_REQUIRED.

    The type selects the propose template URI and the content (plan R1/R3). The channel switches
    only the generation call: ``data`` builds the typed content and calls the deterministic
    from-data API (zero LLM calls), ``text`` renders the same content as a natural-language message
    and calls the from-text API, whose extraction step parses it back into the canonical typed
    content (one LLM call) — both channels then render the addressed template from equivalent
    content, so the produced messages are equivalent. The propose travels on the INPUT_REQUIRED
    status event's message metadata (``propose.build_metadata_content()``); the templateUri inside
    carries the per-type propose template URI, which is what the client dispatches its accept on.
    """
    propose_context = NegotiationContext(
        id=str(uuid.uuid4()),
        round=1,
        max_rounds=NegotiationContext.DEFAULT_MAX_ROUNDS,
        performative=NegotiationPerformative.PROPOSE,
    )
    template_uri = _negotiation_propose_template_uri(negotiation_type)
    if channel == CHANNEL_TEXT:
        text = _negotiation_propose_text(negotiation_type, missing_items, env_path=env_path)
        propose = prompt_server.generate_negotiation_propose_prompt_from_text(text, propose_context, template_uri)
    else:
        data = _negotiation_propose_data(negotiation_type, missing_items, propose_context, env_path=env_path)
        propose = prompt_server.generate_negotiation_propose_prompt_from_data(data, template_uri)
    if log_sink is not None:
        log_sink(
            format_stage_log(
                role="server",
                stage="negotiation-propose",
                detail=(
                    f"generated (type={negotiation_type} channel={channel} "
                    f"round={propose_context.round}) "
                    f"items={[(item.name, item.value) for item in missing_items]}"
                ),
            )
        )
    await emit_status_update(
        request_context=request_context,
        event_queue=event_queue,
        context_id=context_id,
        task_id=task_id,
        state=TaskState.TASK_STATE_INPUT_REQUIRED,
        text=INPUT_REQUIRED_MESSAGE,
        metadata=propose.build_metadata_content(),
    )
    if log_sink is not None:
        log_sink(format_stage_log(role="server", stage="task-status", detail="TASK_STATE_INPUT_REQUIRED"))


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
    """Execute the negotiated task through the ``TaskExecutionService`` seam and push its artifact.

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
    channel: str = CHANNEL_DATA,
    env_path: Path | None = None,
    execution_service: TaskExecutionService | None = None,
) -> None:
    """Validate the incoming Task-T prompt with A2A-T SDK, then stream the UC1 energy-saving flow.

    ``execution_service`` is the server-side INTEGRATION POINT
    (:class:`server.task_execution.TaskExecutionService`): the negotiated task itself is executed
    through it and the flow only emits the A2A events. ``None`` (the default) keeps the demo
    behavior — an internal :class:`Uc1DemoExecutionService` is constructed from the legacy
    ``progress_steps`` / ``intent_report`` / ``sleep_fn`` / ``artifact_send_interval_seconds``
    parameters, whose defaults preserve the pre-seam behavior for existing call sites and tests.

    Flow (RAN energy-saving interface spec, UC1):
      1. Validate the A2A-Extensions header (Task-T) and extract the prompt text
      2. ``A2ATServer.validate_task_prompt_and_data_filling`` (A2A-T server SDK) validates the
         prompt and extracts the filled parameters
      3. Negotiation continuation (Negotiation-T URI in the message metadata): no SUBMITTED —
         validate, execute the task through the seam (progress messages and report artifact),
         then COMPLETED
      4. Default flow: emit SUBMITTED, then on failure REJECTED — or, when the first validation
         error's code selects a negotiation type (plan R1: ``content.param_missing`` information
         with slot-named errors, ``content.value_not_allowed`` target, ``content.semantic_conflict``
         feasibility), a Negotiation-T propose of that type on an INPUT_REQUIRED status update,
         generated from typed data or from a natural-language rendering per the configured channel
         (data-driven trigger, the server reads no scenario flag)
      5. Default success: execute the negotiated task through the seam — each progress line
         becomes a ``TASK_STATE_WORKING`` status update and the ``TaskExecutionResult`` becomes
         the report artifact as one ``TaskArtifactUpdateEvent`` (the report body travels in
         ``artifact.metadata[Task-T/v1]``); the task then stays long-running — no terminal state
         is emitted
      6. Exception during execution or artifact pushing -> emit FAILED (generic text) and re-raise
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
    # The object parameter mapping the execution seam consumes; set on validation success. The
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

    # Negotiation continuation (round 2): no SUBMITTED — the completed Task-T prompt is validated,
    # the task executes through the seam, and the task closes with COMPLETED.
    if negotiation_continuation:
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
        await _execute_and_emit_result(
            request_context=request_context,
            event_queue=event_queue,
            execution_service=resolved_execution_service,
            params=filled_params_data,
            task_id=resolved_task_id,
            context_id=resolved_context_id,
            log_sink=log_sink,
        )
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
        return

    # 1. SUBMITTED — always emitted before the validation result (default flow)
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

    # 2. On failure → Negotiation-T propose selected by the first error's code (plan R1),
    #    otherwise REJECTED
    if validation_failure is not None:
        negotiation_type = _resolve_negotiation_type(content_errors, missing_items)
        if negotiation_type is not None:
            await _emit_negotiation_propose(
                request_context=request_context,
                event_queue=event_queue,
                prompt_server=prompt_server,
                missing_items=missing_items,
                negotiation_type=negotiation_type,
                channel=channel,
                env_path=env_path,
                task_id=resolved_task_id,
                context_id=resolved_context_id,
                log_sink=log_sink,
            )
            return
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

    # 3. On success → execute the negotiated task through the seam (default flow stays long-running)
    await _execute_and_emit_result(
        request_context=request_context,
        event_queue=event_queue,
        execution_service=resolved_execution_service,
        params=filled_params_data,
        task_id=resolved_task_id,
        context_id=resolved_context_id,
        log_sink=log_sink,
    )
