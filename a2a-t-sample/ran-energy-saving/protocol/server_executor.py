"""LangGraph A2A task executor and the pre-task negotiation wrapper.

The server deliberately uses **no ADK agent**. After the pre-task negotiation handler accepts a
Task-T, the request is handed to :class:`LangGraphTaskExecutor`, which runs a small ReAct graph over
the sample's four RAN NMS tools and publishes a standard A2A Task lifecycle. The protocol layer
stays in :class:`NegotiationAgentExecutor`: it runs the pre-task phase first, enqueues standalone
negotiation messages without creating a Task, and delegates only accepted work to the graph
executor.

The graph is sample business logic, not a protocol rule: it reads cell-001's profile, evaluates and
applies a 30% saving plan in 00:00~06:00, and writes the report. A real deployment replaces the
planner/tool set with its own RAN NMS planning logic.

Offline mode uses the deterministic :class:`~agents.server_agent.planner.ScriptedChatModel`; live
mode uses ``ChatOpenAI``. Both run through the same graph and the same guarded tools.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import Part, Task, TaskState
from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    TASK_T_EXTENSION_URI,
    TEMPLATE_URI_METADATA_KEY,
)
from google.protobuf.json_format import MessageToDict, ParseDict
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from shared.config import SampleConfig
from shared.logging_setup import log_context
from shared.metrics import SERVER_TASKS_TOTAL
from shared.trace import PHASE_EXECUTION, Span, current_trace, emit_span, trace_scope
from shared.trace_propagation import annotate_run_id

from protocol.approval_gate import (
    DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    ApprovalRegistry,
)
from protocol.messages import (
    SAMPLE_PHASE_BASELINE,
    SAMPLE_PHASE_METADATA_KEY,
    SAMPLE_RUN_ID_METADATA_KEY,
    TASK_TEMPLATE_URI,
    format_rate_percent,
    metadata_to_dict,
    negotiation_context_metadata,
    parse_negotiation_context,
    task_prompt_from_metadata,
)
from protocol.mie_client import MIEApprovalClient
from protocol.pre_task_negotiation import NegotiationOutcome
from protocol.server_agent_spec import ServerAgentFactory


class TaskRequestHandler(Protocol):
    """Entry gate of one inbound request (the A2A-T gate or the plain A2A entry)."""

    async def handle(self, context: RequestContext) -> NegotiationOutcome:
        """Classify the request and return the executor outcome."""
        ...

#: Artifact names.
REPORT_ARTIFACT_NAME: Final[str] = "energy saving intent report"
TOOL_ARTIFACT_NAME: Final[str] = "tool result"

#: Business status updates after task creation: strategy generation, strategy ready, then the
#: long-running task start. They mirror the reference demo's SUBMITTED -> WORKING -> strategy ->
#: task started sequence before data collection begins.
STRATEGY_STARTED_TEXT: Final[str] = (
    "Strategy recommendation is being executed; no optimization is applied yet."
)
STRATEGY_READY_TEXT: Final[str] = (
    "Strategy recommendation and parameter setting are complete."
)
TASK_STARTED_TEXT: Final[str] = (
    "The energy-saving task has started; the RAN Agent is now running the long-running "
    "optimization task."
)

#: Business status updates of the compressed baseline-collection phase (days 2-8 in the PoC).
DATA_COLLECTION_STARTED_TEXT: Final[str] = (
    "Data collection started: measuring the cluster baseline (3 to 7 days in the PoC; compressed "
    "here). No optimization is applied yet."
)
DATA_COLLECTION_PROGRESS_TEXT: Final[str] = (
    "Data collection in progress: the cluster is sending measurements; expected to complete on "
    "day 8. No optimization is applied yet."
)
DATA_COLLECTION_COMPLETED_TEXT: Final[str] = (
    "Data collection completed: baseline ready. Preparing optimization suggestions."
)

#: Final text of a completed Task.
FINAL_TASK_TEXT: Final[str] = "RAN energy-saving task completed."

#: Business task label used by every progress notification of the reference demo.
TASK_LABEL: Final[str] = "Multi-RAT Intelligent Network Energy Saving"


def _notification_text(region: object, progress: str) -> str:
    """Build one ``Region; Task; Progress`` status message like the reference demo."""
    region_text = str(region) if region not in (None, "") else "unknown"
    return f"Region: {region_text}; Task: {TASK_LABEL}; Progress: {progress}"


def _rate_text(value: object) -> str:
    """Render a plan rate without doubling a percent sign or printing ``35.0%``."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:g}%"
    return format_rate_percent(value)


def _plan_change_text(result: Mapping[str, object], *, action: str = "applied to") -> str:
    """Render one plan apply/modify result as an A2A status notification."""
    plan_id = str(result.get("plan_id") or "the plan")
    cell_id = str(result.get("cell_id") or "the cell")
    cell_name = result.get("cell_name")
    window = str(result.get("window") or "")
    rate = _rate_text(result.get("target_rate"))
    saved = result.get("estimated_saving_kwh")
    cell = f"{cell_id} ({cell_name})" if isinstance(cell_name, str) and cell_name else cell_id
    progress = f"plan {plan_id} was {action} cell {cell}"
    if window:
        progress += f" for {window}"
    progress += f" at {rate} energy saving"
    if isinstance(saved, (int, float)) and not isinstance(saved, bool):
        progress += f", estimated {float(saved):.2f} kWh saved"
    return _notification_text(result.get("region"), progress + ".")


def _apply_completed_text(result: Mapping[str, object]) -> str:
    """Render the apply result as an A2A status notification."""
    return _plan_change_text(result)


def _modify_completed_text(result: Mapping[str, object]) -> str:
    """Render the Modify result as an A2A status notification."""
    return _plan_change_text(result, action="modified on")


def _report_ready_text(result: Mapping[str, object]) -> str:
    """Render the report result as an A2A status notification."""
    summary = result.get("summary")
    progress = "the energy-saving report is ready."
    if isinstance(summary, str) and summary:
        progress = f"the energy-saving report is ready. {summary}"
    return _notification_text(result.get("region"), progress)


#: Trace stages.
EXECUTOR_STAGE: Final[str] = "server_execution_agent"
PLANNER_STAGE: Final[str] = "server_planner"

#: Default recursion limit when no config is supplied (tests).
DEFAULT_PLANNER_MAX_STEPS: Final[int] = 12

#: Tool whose execution is gated by the engineer's approval.
APPLY_TOOL_NAME: Final[str] = "apply_energy_saving_plan"

#: Tool that updates the existing plan of a Modify Task in place.
MODIFY_TOOL_NAME: Final[str] = "modify_energy_saving_plan"

#: Status published after the baseline report, keeping the Task ``WORKING`` for the Modify follow-up.
BASELINE_HOLD_TEXT: Final[str] = (
    "Baseline plan applied; the task remains on duty and can be modified."
)

#: Status published when a validated Modify Task-T starts regenerating the strategy.
MODIFY_STARTED_TEXT: Final[str] = (
    "Target modified; the energy-saving strategy is being regenerated."
)

#: Data-collection status of a Modify execution (the baseline wording would be misleading).
MODIFY_DATA_COLLECTED_TEXT: Final[str] = (
    "Data collection completed for the modified sites; regenerating the optimization suggestions."
)

#: Trace stage of the approval gate.
APPROVAL_STAGE: Final[str] = "approval_gate"


_LOGGER = logging.getLogger("ran_energy_saving.server_executor")


@dataclass(frozen=True)
class ToolStep:
    """One executed RAN NMS tool step.

    Attributes:
        name: Tool name.
        args: Arguments passed to the tool.
        result: Structured tool result.
        duration_ms: Wall-clock duration of the tool call.
    """

    name: str
    args: dict[str, object]
    result: dict[str, object]
    duration_ms: float = 0.0


@dataclass
class _PendingToolCall:
    """One model-requested tool call awaiting its result."""

    name: str
    args: dict[str, object]
    started: float


def _metadata(context: RequestContext) -> dict[str, object]:
    """Return the inbound message metadata as a plain mapping."""
    message = context.message
    return metadata_to_dict(message.metadata) if message is not None else {}


def _run_id(context: RequestContext) -> str:
    """Return the Run Explorer run id carried by the current trace or message."""
    trace_run_id = current_trace().run_id
    if trace_run_id:
        annotate_run_id(trace_run_id)
        return trace_run_id
    value = _metadata(context).get(SAMPLE_RUN_ID_METADATA_KEY)
    run_id = value if isinstance(value, str) else ""
    # The server trace must be searchable by run even when the client did not send the sample
    # correlation headers (or the server could not join the client trace).
    annotate_run_id(run_id)
    return run_id


#: Process-local task id -> latest applied/modified plan, used by the Modify execution. The
#: artifacts of the Task remain the durable fallback when the server restarts.
_TASK_PLANS: dict[str, dict[str, object]] = {}
_TASK_PLANS_LOCK = threading.Lock()


def _sample_phase(context: RequestContext) -> str | None:
    """Return the sample-only two-phase marker of the inbound message, when present."""
    value = _metadata(context).get(SAMPLE_PHASE_METADATA_KEY)
    return value if isinstance(value, str) else None


def _remember_task_plan(task_id: str, plan: Mapping[str, object]) -> None:
    """Remember the latest plan of one Task so a Modify follow-up can update it in place."""
    if task_id and plan.get("plan_id"):
        with _TASK_PLANS_LOCK:
            _TASK_PLANS[task_id] = dict(plan)


def _plan_from_artifact(task: Task) -> dict[str, object] | None:
    """Read the latest plan payload from the Task's report or tool artifacts."""
    for artifact in reversed(task.artifacts):
        for part in artifact.parts:
            payload: dict[str, object] | None = None
            text = getattr(part, "text", "")
            if text:
                try:
                    parsed = json.loads(text)
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    payload = parsed
            if payload is None:
                data = getattr(part, "data", None)
                if data is not None:
                    try:
                        parsed = MessageToDict(data)
                    except (TypeError, ValueError):
                        parsed = None
                    if isinstance(parsed, dict):
                        payload = parsed
            if payload is not None and payload.get("plan_id"):
                return payload
    return None


def _prior_plan(task: Task | None) -> dict[str, object] | None:
    """Return the plan a Modify Task updates, preferring the in-process apply/modify result."""
    if task is None:
        return None
    with _TASK_PLANS_LOCK:
        remembered = _TASK_PLANS.get(task.id)
    if remembered is not None:
        return dict(remembered)
    return _plan_from_artifact(task)


def _report_artifact_id(task: Task | None) -> str | None:
    """Return the artifact id of the Task's current report, when it has one."""
    if task is None:
        return None
    for artifact in reversed(task.artifacts):
        if artifact.name == REPORT_ARTIFACT_NAME and artifact.artifact_id:
            return artifact.artifact_id
    return None


def _negotiation_context(context: RequestContext) -> dict[str, object] | None:
    """Return the accepted negotiation context of the request, when it has one."""
    session = parse_negotiation_context(_metadata(context))
    return negotiation_context_metadata(session) if session is not None else None


def _report_task_t_text(report: Mapping[str, object]) -> str:
    """Render the report artifact metadata in the reference demo's Task-T report shape."""
    energy = report.get("energy")
    energy_map = energy if isinstance(energy, Mapping) else {}
    kpi = report.get("kpi_impact")
    plan_id = str(report.get("plan_id") or "unknown")
    cell_id = str(report.get("cell_id") or "unknown")
    cell_name = report.get("cell_name")
    cell = f"{cell_id} ({cell_name})" if isinstance(cell_name, str) and cell_name else cell_id
    window = str(report.get("window") or "unknown")
    rate = _rate_text(energy_map.get("saving_percent") or report.get("target_rate"))
    lines = [
        "1. Basic information about the intent execution result",
        f"- Region: {report.get('region') or 'unknown'}",
        f"- Plan: {plan_id} ({report.get('status') or 'unknown'}) for cell {cell}",
        f"- Window: {window}; energy-saving target: {rate}",
        "2. Details",
    ]
    saved = energy_map.get("saved_kwh")
    baseline = energy_map.get("baseline_kwh")
    if isinstance(saved, (int, float)) and isinstance(baseline, (int, float)):
        lines.append(
            f"- Energy saving: {float(saved):.2f} kWh of {float(baseline):.2f} kWh baseline"
        )
    if isinstance(kpi, Mapping) and kpi:
        lines.append(f"- KPI impact: {json.dumps(kpi, ensure_ascii=False, sort_keys=True)}")
    summary = report.get("summary")
    if isinstance(summary, str) and summary:
        lines.extend(["", summary])
    return "\n".join(lines)


def _report_metadata(
    context: RequestContext,
    summary: str,
    negotiation_context: dict[str, object] | None,
    *,
    pure: bool = False,
    report: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the metadata stamped on the final report artifact and status.

    Pure A2A runs must stay free of A2A-T metadata; only the sample's trace correlation id is
    retained.
    """
    metadata: dict[str, object] = {}
    run_id = _run_id(context)
    if run_id:
        metadata[SAMPLE_RUN_ID_METADATA_KEY] = run_id
    if pure:
        return metadata
    metadata[TASK_T_EXTENSION_URI] = _report_task_t_text(report) if report is not None else summary
    metadata[TEMPLATE_URI_METADATA_KEY] = TASK_TEMPLATE_URI
    if negotiation_context is not None:
        metadata[NEGOTIATION_CONTEXT_METADATA_KEY] = negotiation_context
    return metadata


def _execution_span_id(task_id: str) -> str:
    """Return the stable parent span id of one deterministic execution."""
    return f"exec_server_{task_id}"


def _emit_execution_span(context: RequestContext, task_id: str) -> str:
    """Emit the execution parent span and return its id."""
    span_id = _execution_span_id(task_id)
    emit_span(
        Span(
            run_id=_run_id(context),
            span_id=span_id,
            context_id=context.context_id,
            task_id=task_id,
            phase=PHASE_EXECUTION,
            actor="server",
            side="server",
            kind="execution",
            stage=EXECUTOR_STAGE,
            title="RAN NMS task execution",
            purpose="Run the LangGraph ReAct planner over the RAN NMS tools.",
            direction="internal",
        )
    )
    return span_id


def _emit_tool_span(
    context: RequestContext,
    task_id: str,
    parent_span_id: str,
    step: ToolStep,
) -> None:
    """Emit one tool span parented to the execution span."""
    emit_span(
        Span(
            run_id=_run_id(context),
            span_id=f"tool_server_{uuid.uuid4().hex}",
            parent_span_id=parent_span_id,
            context_id=context.context_id,
            task_id=task_id,
            phase=PHASE_EXECUTION,
            actor="tool",
            side="server",
            kind="tool",
            stage=EXECUTOR_STAGE,
            title=step.name,
            purpose="Guarded RAN NMS tool call.",
            status="ok" if step.result.get("ok") is True else "error",
            duration_ms=step.duration_ms,
            detail={
                "toolName": step.name,
                "args": step.args,
                "result": step.result,
            },
        )
    )


def _message_digest(message: BaseMessage, *, limit: int = 2000) -> dict[str, object]:
    """Build a bounded trace digest of one planner message."""
    content = str(message.content)
    if len(content) > limit:
        content = content[: limit - 1] + "…"
    digest: dict[str, object] = {"role": message.type, "content": content}
    calls = getattr(message, "tool_calls", None)
    if calls:
        digest["toolCalls"] = [
            {"name": call.get("name"), "args": call.get("args"), "id": call.get("id")}
            for call in calls
        ]
    return digest


def _model_id(model: BaseChatModel) -> str:
    """Return the display model id of one chat model."""
    for attribute in ("model_name", "model"):
        value = getattr(model, attribute, None)
        if isinstance(value, str) and value:
            return value
    return type(model).__name__


def _token_usage(message: BaseMessage) -> dict[str, object] | None:
    """Return the usage map of one model response, when the provider reports one."""
    usage = getattr(message, "usage_metadata", None)
    return dict(usage) if isinstance(usage, dict) else None


def _emit_planner_span(
    context: RequestContext,
    task_id: str,
    parent_span_id: str,
    *,
    model: BaseChatModel,
    system_prompt: str,
    history: list[BaseMessage],
    response: AIMessage,
    duration_ms: float,
) -> None:
    """Emit one LLM span for a planner turn."""
    emit_span(
        Span(
            run_id=_run_id(context),
            span_id=f"llm_server_{uuid.uuid4().hex}",
            parent_span_id=parent_span_id,
            context_id=context.context_id,
            task_id=task_id,
            phase=PHASE_EXECUTION,
            actor="llm",
            side="server",
            kind="llm",
            stage=PLANNER_STAGE,
            title="Planner turn",
            purpose="Choose the next guarded RAN NMS tool call.",
            status="ok",
            duration_ms=round(duration_ms, 3),
            model=_model_id(model),
            tokens=_token_usage(response),
            detail={
                "provider": "langgraph",
                "request": {
                    "systemPrompt": system_prompt,
                    "messages": [_message_digest(message) for message in history[-8:]],
                },
                "response": _message_digest(response),
            },
        )
    )


def _task_prompt(context: RequestContext) -> str:
    """Return the accepted Task-T prompt, falling back to the inbound text."""
    prompt = task_prompt_from_metadata(_metadata(context))
    if prompt.strip():
        return prompt
    message = context.message
    if message is not None:
        for part in message.parts or ():
            if part.text and part.text.strip():
                return part.text.strip()
    return "Create a RAN energy saving task."


class LangGraphTaskExecutor(AgentExecutor):
    """Execute one accepted Task-T with the LangGraph ReAct planner."""

    def __init__(
        self,
        *,
        agent_factory: ServerAgentFactory,
        config: SampleConfig | None = None,
        approval_registry: ApprovalRegistry | None = None,
        approval_timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
        pure: bool = False,
        mie_client: MIEApprovalClient | None = None,
    ) -> None:
        """Create one executor.

        Args:
            agent_factory: builder of the planner graph and its metadata for one task; supplied by
                the NMS integration (the demo factory lives in :mod:`demo.agents.server_agent`).
            config: resolved sample configuration; defaults to :func:`load_config`.
            approval_registry: optional human gate registry; ``None`` applies plans without a pause.
            approval_timeout_seconds: seconds the gate waits before treating the approval as a
                rejection.
            pure: ``True`` for the pure A2A server; its final artifacts must not carry A2A-T
                metadata.
            mie_client: A2A client of the MIE Agent; when set in pure mode, the gate asks MIE
                instead of waiting on the local HTTP approval registry.
        """
        self._config = config
        self._agent_factory = agent_factory
        self._approval_registry = approval_registry
        self._approval_timeout_seconds = approval_timeout_seconds
        self._pure = pure
        self._mie_client = mie_client

    @property
    def recursion_limit(self) -> int:
        """Return the planner recursion limit of this executor."""
        configured = self._config.planner_max_steps if self._config is not None else None
        return configured if configured is not None else DEFAULT_PLANNER_MAX_STEPS

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Create the Task, run the graph, and publish artifacts/status updates.

        Args:
            context: Gate-approved request; its message already carries the agreed Task-T prompt.
            event_queue: A2A event queue of this request.
        """
        if context.current_task is not None:
            # The five scenarios are single-shot. Re-enqueueing the existing Task keeps a follow-up
            # request from accidentally starting a second plan.
            await event_queue.enqueue_event(context.current_task)
            return
        message = context.message
        if message is None:
            raise ValueError("The approved request carries no A2A message")

        task_id = context.task_id or str(uuid.uuid4())
        context_id = context.context_id or task_id
        task = new_task_from_user_message(message)
        task.id = task_id
        task.context_id = context_id
        run_id = _run_id(context)
        if run_id:
            task.metadata[SAMPLE_RUN_ID_METADATA_KEY] = run_id
        await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        # The reference demo reports Task creation (SUBMITTED), then a generic WORKING update,
        # then the strategy ladder. Keep the generic update: the engineer must see that the RAN
        # Agent started working before the more specific strategy notifications arrive.
        await updater.start_work()
        await self._announce_execution_start(updater)
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=updater.new_agent_message([Part(text=DATA_COLLECTION_PROGRESS_TEXT)]),
        )
        _LOGGER.info("task accepted; running the planner")
        await self._execute_task(
            context,
            updater,
            task.id,
            _task_prompt(context),
            hold_open=_sample_phase(context) == SAMPLE_PHASE_BASELINE,
        )

    async def execute_modify(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        *,
        prompt: str,
        params: Mapping[str, object] | None = None,
    ) -> None:
        """Update an existing ``WORKING`` Task with a validated Modify Task-T.

        The framework has already appended the follow-up message to the Task history. This method
        reuses the Task id/context id, reads the plan created by the baseline phase, runs the
        planner in Modify mode and publishes the updated report artifact and ``COMPLETED`` state on
        the same Task.

        Args:
            context: request context whose ``current_task`` is the baseline Task.
            event_queue: A2A event queue of the follow-up request.
            prompt: validated Modify Task-T prompt.
            params: parameters extracted from the Modify Task-T.
        """
        del params  # The prompt already carries them; the planner reads the Task-T.
        task = context.current_task
        if task is None:
            raise ValueError("Cannot modify a Task that was not loaded")
        prior_plan = _prior_plan(task)
        if prior_plan is None:
            updater = TaskUpdater(event_queue, task.id, task.context_id)
            await updater.failed(
                message=updater.new_agent_message(
                    [Part(text="The Task has no baseline plan to modify.")]
                )
            )
            return
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=updater.new_agent_message([Part(text=MODIFY_STARTED_TEXT)]),
        )
        _LOGGER.info("task modify accepted; running the planner on plan %s", prior_plan.get("plan_id"))
        await self._execute_task(
            context,
            updater,
            task.id,
            prompt,
            operation="Modify",
            existing_plan=prior_plan,
        )

    async def execute_resumed(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        *,
        prompt: str,
    ) -> None:
        """Resume an existing ``INPUT_REQUIRED`` Task with a negotiated prompt.

        The standard A2A server loads the Task and appends the follow-up message to its history
        before calling the executor. This method reuses the Task id/context id, moves it back to
        ``WORKING`` and runs the same planner as a fresh Task.

        Args:
            context: request context whose ``current_task`` is the ``INPUT_REQUIRED`` Task.
            event_queue: A2A event queue of the follow-up request.
            prompt: negotiated natural-language task prompt.
        """
        task = context.current_task
        if task is None:
            raise ValueError("Cannot resume a Task that was not loaded")
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        # Same lifecycle as a fresh Task: a generic WORKING update first, then the strategy
        # ladder. The resume follows an INPUT_REQUIRED pause, so the working report is what tells
        # the engineer the RAN Agent is back on the task.
        await updater.start_work()
        await self._announce_execution_start(updater)
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=updater.new_agent_message([Part(text=DATA_COLLECTION_PROGRESS_TEXT)]),
        )
        _LOGGER.info("task resumed after negotiation; running the planner")
        await self._execute_task(context, updater, task.id, prompt)

    async def _announce_execution_start(self, updater: TaskUpdater) -> None:
        """Publish the reference demo's strategy -> task-started status ladder."""
        for text in (
            STRATEGY_STARTED_TEXT,
            STRATEGY_READY_TEXT,
            TASK_STARTED_TEXT,
            DATA_COLLECTION_STARTED_TEXT,
        ):
            await updater.update_status(
                TaskState.TASK_STATE_WORKING,
                message=updater.new_agent_message([Part(text=text)]),
            )

    async def _execute_task(
        self,
        context: RequestContext,
        updater: TaskUpdater,
        task_id: str,
        prompt: str,
        *,
        operation: str = "Create",
        existing_plan: Mapping[str, object] | None = None,
        hold_open: bool = False,
    ) -> None:
        """Run the planner and map every failure to a terminal Task state.

        ``hold_open`` publishes the baseline report but leaves the Task ``WORKING`` so the next
        Task-T can continue it with a real Modify follow-up.
        """
        execution_span_id = _emit_execution_span(context, task_id)
        negotiation_context = _negotiation_context(context)
        with log_context(
            task_id=task_id,
            context_id=context.context_id,
            run_id=_run_id(context) or None,
            role="server",
        ):
            try:
                await self._run_graph(
                    context,
                    updater,
                    task_id,
                    execution_span_id,
                    negotiation_context,
                    prompt=prompt,
                    approval_gate=self._build_approval_gate(
                        context, updater, task_id, execution_span_id
                    ),
                    operation=operation,
                    existing_plan=existing_plan,
                    hold_open=hold_open,
                )
            except asyncio.CancelledError:
                _LOGGER.info("task cancelled")
                raise
            except GraphRecursionError:
                _LOGGER.warning(
                    "planner exceeded %s steps for task %s", self.recursion_limit, task_id
                )
                SERVER_TASKS_TOTAL.inc({"state": "failed"})
                await updater.failed(
                    message=updater.new_agent_message(
                        [
                            Part(
                                text=(
                                    "The energy-saving planner exceeded "
                                    f"{self.recursion_limit} steps without completing the task."
                                )
                            )
                        ]
                    )
                )
            except Exception as exc:  # noqa: BLE001 - the Task must always reach a terminal state
                _LOGGER.exception("unexpected LangGraph executor failure")
                SERVER_TASKS_TOTAL.inc({"state": "failed"})
                await updater.failed(
                    message=updater.new_agent_message(
                        [Part(text=f"The energy-saving task failed unexpectedly: {exc}")]
                    )
                )

    def _build_approval_gate(
        self,
        context: RequestContext,
        updater: TaskUpdater,
        task_id: str,
        execution_span_id: str,
    ) -> Callable[..., Awaitable[bool]] | None:
        """Build the approval gate used by the planner, or ``None`` when the gate is disabled."""
        if self._pure and self._mie_client is not None:
            return self._build_mie_approval_gate(
                context, updater, task_id, execution_span_id
            )
        if self._approval_registry is None:
            return None

        async def approval_gate(
            cell_id: str,
            window: str,
            target_rate: str,
            evaluation: Mapping[str, object] | None,
        ) -> bool:
            """Publish INPUT_REQUIRED, wait for the engineer, then resume or reject."""
            key = _run_id(context) or task_id
            rate_text = format_rate_percent(target_rate)
            await updater.update_status(
                TaskState.TASK_STATE_INPUT_REQUIRED,
                message=updater.new_agent_message(
                    [
                        Part(
                            text=(
                                "Waiting for the engineer's approval before applying the "
                                f"plan: {cell_id}, {window}, {rate_text}."
                            )
                        )
                    ]
                ),
            )
            emit_span(
                Span(
                    run_id=_run_id(context),
                    span_id=f"approval_server_{uuid.uuid4().hex}",
                    parent_span_id=execution_span_id,
                    context_id=context.context_id,
                    task_id=task_id,
                    phase=PHASE_EXECUTION,
                    actor="human",
                    side="server",
                    kind="approval",
                    stage=APPROVAL_STAGE,
                    title="Approval requested",
                    purpose="Human approval before a network change.",
                    status="pending",
                    detail={
                        "cellId": cell_id,
                        "window": window,
                        "rate": rate_text,
                        "taskId": task_id,
                        "runId": key,
                        "approvalState": "requested",
                        "timeoutSeconds": self._approval_timeout_seconds,
                        "savedPercent": (
                            evaluation.get("estimated_saving_percent")
                            if isinstance(evaluation, Mapping)
                            else None
                        ),
                    },
                )
            )
            approved = await self._approval_registry.wait(
                key, self._approval_timeout_seconds
            )
            emit_span(
                Span(
                    run_id=_run_id(context),
                    span_id=f"approval_server_{uuid.uuid4().hex}",
                    parent_span_id=execution_span_id,
                    context_id=context.context_id,
                    task_id=task_id,
                    phase=PHASE_EXECUTION,
                    actor="human",
                    side="server",
                    kind="approval",
                    stage=APPROVAL_STAGE,
                    title="Approval resolved",
                    purpose="Human approval before a network change.",
                    status="ok" if approved else "error",
                    detail={
                        "cellId": cell_id,
                        "window": window,
                        "rate": rate_text,
                        "taskId": task_id,
                        "runId": key,
                        "approved": approved,
                        "approvalState": "approved" if approved else "rejected",
                    },
                )
            )
            if approved:
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=updater.new_agent_message(
                        [Part(text="Approved. Applying the plan.")]
                    ),
                )
            return approved

        return approval_gate

    def _build_mie_approval_gate(
        self,
        context: RequestContext,
        updater: TaskUpdater,
        task_id: str,
        execution_span_id: str,
    ) -> Callable[..., Awaitable[bool]]:
        """Build the pure A2A gate that asks the MIE Agent over A2A."""
        client = self._mie_client
        if client is None:  # pragma: no cover - guarded by the caller
            raise ValueError("MIE approval client is not configured")

        async def approval_gate(
            cell_id: str,
            window: str,
            target_rate: str,
            evaluation: Mapping[str, object] | None,
        ) -> bool:
            """Publish INPUT_REQUIRED, ask MIE, then resume or reject."""
            key = _run_id(context) or task_id
            rate_text = format_rate_percent(target_rate)
            plan = {
                "cellId": cell_id,
                "window": window,
                "rate": rate_text,
                "savedPercent": (
                    evaluation.get("estimated_saving_percent")
                    if isinstance(evaluation, Mapping)
                    else None
                ),
            }
            await updater.update_status(
                TaskState.TASK_STATE_INPUT_REQUIRED,
                message=updater.new_agent_message(
                    [
                        Part(
                            text=(
                                "Waiting for MIE approval before applying the plan: "
                                f"{cell_id}, {window}, {rate_text}."
                            )
                        )
                    ],
                    metadata={
                        "pure": {
                            "kind": "approval",
                            "phase": "approval",
                            "plan": plan,
                            "round": 0,
                        }
                    },
                ),
            )
            emit_span(
                Span(
                    run_id=_run_id(context),
                    span_id=f"approval_mie_{uuid.uuid4().hex}",
                    parent_span_id=execution_span_id,
                    context_id=context.context_id,
                    task_id=task_id,
                    phase=PHASE_EXECUTION,
                    actor="mie",
                    side="server",
                    kind="approval",
                    stage=APPROVAL_STAGE,
                    title="MIE approval requested",
                    purpose="RAN asks the MIE Agent over standard A2A.",
                    status="pending",
                    detail={
                        **plan,
                        "taskId": task_id,
                        "runId": key,
                        "hop": "ran->mie",
                        "pureKind": "approval",
                        "approvalState": "requested",
                    },
                )
            )
            decision = await client.request_approval(
                cell_id=cell_id,
                window=window,
                target_rate=target_rate,
                evaluation=evaluation,
                run_id=key,
                ran_task_id=task_id,
            )
            emit_span(
                Span(
                    run_id=_run_id(context),
                    span_id=f"approval_mie_{uuid.uuid4().hex}",
                    parent_span_id=execution_span_id,
                    context_id=context.context_id,
                    task_id=task_id,
                    phase=PHASE_EXECUTION,
                    actor="mie",
                    side="server",
                    kind="approval",
                    stage=APPROVAL_STAGE,
                    title="MIE approval resolved",
                    purpose="The MIE Agent returned the engineer's decision over A2A.",
                    status="ok" if decision.approved else "error",
                    detail={
                        **plan,
                        "taskId": task_id,
                        "runId": key,
                        "hop": "mie->ran",
                        "pureKind": "approval",
                        "approved": decision.approved,
                        "reason": decision.reason,
                        "approvalState": "approved" if decision.approved else "rejected",
                    },
                )
            )
            if decision.approved:
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=updater.new_agent_message(
                        [Part(text="MIE approved. Applying the plan.")]
                    ),
                )
            return decision.approved

        return approval_gate

    async def _run_graph(
        self,
        context: RequestContext,
        updater: TaskUpdater,
        task_id: str,
        execution_span_id: str,
        negotiation_context: dict[str, object] | None,
        *,
        prompt: str,
        approval_gate: Callable[..., Awaitable[bool]] | None,
        operation: str = "Create",
        existing_plan: Mapping[str, object] | None = None,
        hold_open: bool = False,
    ) -> None:
        """Run the graph, publishing one artifact per tool result and the final report."""
        factory_kwargs: dict[str, object] = {}
        if operation.strip().lower() == "modify" or existing_plan is not None:
            factory_kwargs["operation"] = operation
            factory_kwargs["existing_plan"] = existing_plan
        if approval_gate is None:
            agent = self._agent_factory(
                self._config,
                task_prompt=prompt,
                negotiation_context=negotiation_context,
                **factory_kwargs,
            )
        else:
            agent = self._agent_factory(
                self._config,
                task_prompt=prompt,
                negotiation_context=negotiation_context,
                approval_gate=approval_gate,
                **factory_kwargs,
            )
        report_tool_name = agent.report_tool_name
        history: list[BaseMessage] = list(agent.initial_messages)
        steps: list[ToolStep] = []
        pending: dict[str, _PendingToolCall] = {}
        final_text = ""
        last_update_at = time.perf_counter()
        collection_completed_sent = False
        report_ready = False

        async for update in agent.graph.astream(
            {"messages": list(agent.initial_messages)},
            config={"recursion_limit": self.recursion_limit},
            stream_mode="updates",
        ):
            for node_update in update.values():
                for item in node_update.get("messages", []):
                    history.append(item)
                    if isinstance(item, AIMessage):
                        now = time.perf_counter()
                        turn_duration_ms = (now - last_update_at) * 1000.0
                        last_update_at = now
                        for call in item.tool_calls or []:
                            call_id = str(call.get("id") or uuid.uuid4().hex)
                            args = call.get("args")
                            pending[call_id] = _PendingToolCall(
                                name=str(call.get("name") or "unknown"),
                                args=dict(args) if isinstance(args, dict) else {},
                                started=now,
                            )
                        if not item.tool_calls and item.content:
                            final_text = str(item.content)
                        _emit_planner_span(
                            context,
                            task_id,
                            execution_span_id,
                            model=agent.model,
                            system_prompt=agent.system_prompt,
                            history=history[:-1],
                            response=item,
                            duration_ms=turn_duration_ms,
                        )
                    elif isinstance(item, ToolMessage):
                        call = pending.pop(str(item.tool_call_id), None)
                        name = call.name if call is not None else str(item.name or "unknown")
                        args = call.args if call is not None else {}
                        duration_ms = (
                            (time.perf_counter() - call.started) * 1000.0
                            if call is not None
                            else 0.0
                        )
                        step = ToolStep(
                            name=name,
                            args=args,
                            result=agent.parse_tool_result(item.content),
                            duration_ms=round(duration_ms, 3),
                        )
                        steps.append(step)
                        _emit_tool_span(context, task_id, execution_span_id, step)
                        if step.result.get("ok") is True and name in (
                            APPLY_TOOL_NAME,
                            MODIFY_TOOL_NAME,
                        ):
                            # The apply/modify tool result is not only an artifact: the reference
                            # demo reports the execution result as an A2A status message, so the
                            # OSS side receives a real notification instead of inferring it from
                            # the tool artifact.
                            _remember_task_plan(task_id, step.result)
                            text = (
                                _modify_completed_text(step.result)
                                if name == MODIFY_TOOL_NAME
                                else _apply_completed_text(step.result)
                            )
                            await updater.update_status(
                                TaskState.TASK_STATE_WORKING,
                                message=updater.new_agent_message([Part(text=text)]),
                            )
                        elif step.result.get("ok") is True and name == report_tool_name:
                            # Same for the final report: publish its summary as a notification
                            # before the report artifact and the terminal status.
                            await updater.update_status(
                                TaskState.TASK_STATE_WORKING,
                                message=updater.new_agent_message(
                                    [Part(text=_report_ready_text(step.result))]
                                ),
                            )
                            # The report is the terminal tool. Stop consuming the graph even if
                            # the live model would keep exploring; otherwise a late extra step can
                            # hit the recursion limit and turn a successful report into a failed
                            # Task (the report artifact is emitted after this loop).
                            report_ready = True
                        if name == "get_cell_energy_profile" and not collection_completed_sent:
                            # The planner may read both cells before moving on; the reference demo
                            # reports "data collection completed" once, not once per profile.
                            collection_completed_sent = True
                            collected_text = (
                                MODIFY_DATA_COLLECTED_TEXT
                                if operation.strip().lower() == "modify"
                                else DATA_COLLECTION_COMPLETED_TEXT
                            )
                            await updater.update_status(
                                TaskState.TASK_STATE_WORKING,
                                message=updater.new_agent_message([Part(text=collected_text)]),
                            )
                        # The apply/modify result is already reported as a status notification.
                        # Do not publish a second, duplicate artifact for it in A2A-T mode; the
                        # pure three-hop comparison keeps it because its presentation drives the
                        # execution beat from the relayed artifact.
                        duplicate_apply = (
                            name in (APPLY_TOOL_NAME, MODIFY_TOOL_NAME) and not self._pure
                        )
                        if name != report_tool_name and not duplicate_apply:
                            await updater.add_artifact(
                                [Part(text=json.dumps(step.result, ensure_ascii=False))],
                                name=TOOL_ARTIFACT_NAME,
                                metadata={"toolName": step.name},
                                last_chunk=True,
                            )
            if report_ready:
                break

        denied = [
            step
            for step in steps
            if step.name in (APPLY_TOOL_NAME, MODIFY_TOOL_NAME)
            and step.result.get("error_code") == "approval_denied"
        ]
        if denied:
            reason = "The engineer rejected the change; nothing was applied."
            _LOGGER.info("task rejected at the approval gate: %s", task_id)
            SERVER_TASKS_TOTAL.inc({"state": "rejected"})
            await updater.update_status(
                TaskState.TASK_STATE_REJECTED,
                message=updater.new_agent_message([Part(text=reason)]),
            )
            return

        report_steps = [
            step
            for step in steps
            if step.name == report_tool_name and step.result.get("ok") is True
        ]
        if not report_steps:
            reason = (
                "The planner stopped before generating a successful report."
                if not final_text
                else "The planner stopped without generating a report."
            )
            _LOGGER.warning("task failed: %s", reason)
            SERVER_TASKS_TOTAL.inc({"state": "failed"})
            await updater.failed(
                message=updater.new_agent_message([Part(text=reason)])
            )
            return

        report = report_steps[-1].result
        summary = str(report.get("summary", ""))
        metadata = _report_metadata(
            context,
            summary,
            negotiation_context,
            pure=self._pure,
            report=report,
        )
        report_part = Part()
        ParseDict(report, report_part.data)
        prior_report_id = _report_artifact_id(context.current_task)
        await updater.add_artifact(
            [report_part, Part(text=summary)],
            name=REPORT_ARTIFACT_NAME,
            metadata=metadata,
            artifact_id=prior_report_id,
            append=False if prior_report_id else None,
            last_chunk=True,
        )
        if hold_open:
            # The baseline report is published, but the Task stays WORKING so the Modify follow-up
            # can continue the same A2A Task.
            _LOGGER.info("task baseline report ready; keeping the Task WORKING for Modify")
            await updater.update_status(
                TaskState.TASK_STATE_WORKING,
                message=updater.new_agent_message([Part(text=BASELINE_HOLD_TEXT)]),
                metadata=metadata,
            )
            return
        final_message = updater.new_agent_message(
            [Part(text=FINAL_TASK_TEXT)], metadata=metadata
        )
        _LOGGER.info("task completed: %s", summary[:120])
        SERVER_TASKS_TOTAL.inc({"state": "completed"})
        await updater.update_status(
            TaskState.TASK_STATE_COMPLETED,
            message=final_message,
            metadata=metadata,
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Mark the Task canceled.

        Args:
            context: Request context of the Task.
            event_queue: A2A event queue of this request.
        """
        updater = TaskUpdater(
            event_queue,
            context.task_id or "",
            context.context_id or "",
        )
        SERVER_TASKS_TOTAL.inc({"state": "cancelled"})
        await updater.cancel()


class NegotiationAgentExecutor(AgentExecutor):
    """Outer executor: the pre-task negotiation handler in front of the graph executor."""

    def __init__(self, *, handler: TaskRequestHandler, inner: AgentExecutor) -> None:
        """Create one executor.

        Args:
            handler: pre-task negotiation handler.
            inner: graph executor that runs an accepted Task.
        """
        self._handler = handler
        self._inner = inner
        # The client sends its run id once, on the initial Task-T message; the follow-up
        # accept/reject/abort messages of the same context reuse it for trace correlation.
        self._run_ids: dict[str, str] = {}
        self._run_ids_lock = threading.Lock()

    @property
    def handler(self) -> TaskRequestHandler:
        """Return the request entry gate (A2A-T pre-task gate or plain entry)."""
        return self._handler

    @property
    def inner(self) -> AgentExecutor:
        """Return the inner graph executor."""
        return self._inner

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Run the pre-task handler, then short-circuit or delegate to the graph executor.

        Args:
            context: inbound A2A request context.
            event_queue: A2A event queue of this request.
        """
        with trace_scope(
            run_id=self._resolve_run_id(context),
            role="server",
            context_id=context.context_id,
            stage="server_execution_agent",
        ):
            outcome = await self._handler.handle(context)
            if outcome.input_required_task is not None:
                # Pure A2A negotiation: the Task is non-terminal. Enqueue it and a status update so
                # a standard A2A client receives the question text and metadata (a bare Task event
                # would be normalized to its state only).
                task = outcome.input_required_task
                await event_queue.enqueue_event(task)
                updater = TaskUpdater(event_queue, task.id, task.context_id)
                status_message = task.status.message if task.status.HasField("message") else None
                await updater.update_status(
                    TaskState.TASK_STATE_INPUT_REQUIRED,
                    message=status_message,
                )
                return
            if outcome.short_circuit:
                if outcome.terminal_task is not None:
                    await event_queue.enqueue_event(outcome.terminal_task)
                    return
                if outcome.message is None:
                    raise ValueError(
                        "The pre-task handler short-circuited without a message or a terminal task"
                    )
                await event_queue.enqueue_event(outcome.message)
                return
            if outcome.task_request is None:
                raise ValueError("The pre-task handler approved execution without a request context")
            if outcome.modify_prompt is not None:
                execute_modify = getattr(self._inner, "execute_modify", None)
                if execute_modify is None:
                    raise ValueError("The inner executor does not support modifying a Task")
                await execute_modify(
                    outcome.task_request,
                    event_queue,
                    prompt=outcome.modify_prompt,
                    params=outcome.modify_params,
                )
                return
            if outcome.resume_prompt is not None:
                execute_resumed = getattr(self._inner, "execute_resumed", None)
                if execute_resumed is None:
                    raise ValueError("The inner executor does not support resuming a Task")
                await execute_resumed(
                    outcome.task_request,
                    event_queue,
                    prompt=outcome.resume_prompt,
                )
                return
            await self._inner.execute(outcome.task_request, event_queue)

    def _resolve_run_id(self, context: RequestContext) -> str | None:
        """Return the client run id advertised on the initial message (sticky per context)."""
        key = context.context_id or context.task_id or ""
        metadata = _metadata(context)
        advertised = metadata.get(SAMPLE_RUN_ID_METADATA_KEY)
        if isinstance(advertised, str) and advertised.strip():
            with self._run_ids_lock:
                self._run_ids[key] = advertised.strip()
            return advertised.strip()
        with self._run_ids_lock:
            return self._run_ids.get(key)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Delegate cancellation to the graph executor."""
        await self._inner.cancel(context, event_queue)


def build_negotiation_executor(
    *,
    handler: TaskRequestHandler,
    agent_factory: ServerAgentFactory,
    config: SampleConfig | None = None,
    approval_registry: ApprovalRegistry | None = None,
    approval_timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    pure: bool = False,
    mie_client: MIEApprovalClient | None = None,
) -> NegotiationAgentExecutor:
    """Build the outer executor with the LangGraph executor inside.

    Args:
        handler: pre-task negotiation handler.
        agent_factory: builder of the planner graph of one accepted task.
        config: resolved sample configuration.
        approval_registry: optional human gate registry.
        approval_timeout_seconds: seconds the gate waits before rejecting.
        pure: ``True`` for the pure A2A server; final artifacts carry no A2A-T metadata.
        mie_client: optional A2A client of the MIE Agent used by the pure approval gate.
    """
    return NegotiationAgentExecutor(
        handler=handler,
        inner=LangGraphTaskExecutor(
            agent_factory=agent_factory,
            config=config,
            approval_registry=approval_registry,
            approval_timeout_seconds=approval_timeout_seconds,
            pure=pure,
            mie_client=mie_client,
        ),
    )
