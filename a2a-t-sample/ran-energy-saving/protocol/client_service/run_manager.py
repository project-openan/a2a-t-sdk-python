"""Run lifecycle management: conversations, persistence and per-conversation concurrency."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, cast

from a2a.client.client import ClientCallContext
from a2a.types import GetTaskRequest, TaskState
from a2a_t.core.metadata import NegotiationContext
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message
from shared.config import (
    DISCOVERY_NMS,
    DISCOVERY_REGISTRY,
    PRE_SEND_VALIDATION_ALWAYS,
    PRE_SEND_VALIDATION_ON,
    SUPPORTED_INTENT_MODES,
    SampleConfig,
)
from shared.langfuse_tracing import LangfuseRunMetadata, create_langfuse_exporter
from shared.logging_setup import log_context
from shared.metrics import (
    CLIENT_RUN_DURATION_SECONDS,
    CLIENT_RUNS_IN_FLIGHT,
    CLIENT_RUNS_TOTAL,
    NEGOTIATION_ROUNDS_TOTAL,
)
from shared.models import NegotiationDecision, NegotiationProposeView
from shared.run_constants import (
    FINAL_STATE_DECISION_UNGROUNDED,
    FINAL_STATE_INCOMPLETE,
    FINAL_STATE_INPUT_INVALID,
    FINAL_STATE_TASK_COMPLETED,
    FINAL_STATE_TASK_FAILED,
    FINAL_STATE_TASK_REJECTED,
    PROTOCOL_A2A_T,
    PROTOCOL_PURE,
    RUN_MODE_CREATE,
    RUN_MODE_MODIFY,
    reports_failed_completion,
)
from shared.run_source import normalize_run_source
from shared.trace import (
    PHASE_DISPATCH,
    PHASE_NEGOTIATION,
)
from shared.trace_propagation import reset_active_trace, set_active_trace

from protocol.client_flow import ClientFlowResult
from protocol.client_runtime import MODE_CONFIRM, MODE_MANUAL, build_poll_runtime
from protocol.client_service.run_state import (
    SESSION_SWEEP_INTERVAL_SECONDS,
    STATUS_FAILED,
    STATUS_FINISHED,
    STATUS_INTERRUPTED,
    STATUS_RUNNING,
    RunConflictError,
    RunState,
    RunStatus,
    RuntimeFactory,
)
from protocol.client_store import (
    RUN_STATUS_RUNNING,
    TURN_ROLE_ASSISTANT,
    TURN_ROLE_USER,
    ClientRunRecord,
    ClientStore,
    RunAlreadyActiveError,
    now_iso,
)
from protocol.client_store.store import _pid_alive
from protocol.decision_provider import DecisionTimeoutError, sweep_stale_sessions
from protocol.manual_answer import missing_item_labels
from protocol.pure_client_flow import build_follow_up_request
from protocol.run_metrics import append_metrics_jsonl, build_run_metrics
from protocol.run_spec import RunCatalog
from protocol.transcript import DEFAULT_RUNS_DIR, NegotiationTranscript

_LOGGER = logging.getLogger("ran_energy_saving.client_service")

#: Human budget of approval 1 (the OSS intent review) before the Task-T is sent.
INTENT_REVIEW_TIMEOUT_SECONDS = 300.0

#: Best-effort budget for the remote Task cancel before the local run is stopped anyway.
REMOTE_CANCEL_TIMEOUT_SECONDS: Final[float] = 5.0

#: How long a cancel request waits for the run task to unwind.
CANCEL_UNWIND_TIMEOUT_SECONDS: Final[float] = 5.0

#: Cancellation reasons recorded in the transcript.
CANCELED_BY_OPERATOR: Final[str] = "The run was cancelled by the operator."
CANCELED_BY_SHUTDOWN: Final[str] = "The run was cancelled because the process is shutting down."
HANDED_OVER: Final[str] = "The run was handed over to the run that continued the same Task."


def _langfuse_disabled() -> dict[str, object]:
    """Return the Langfuse snapshot of a run that does not export to Langfuse."""
    return {
        "enabled": False,
        "traceId": None,
        "url": None,
        "status": "disabled",
        "warning": None,
    }


#: How long a presentation run may stay unwatched before its remote tracker stops.
_DETACHED_TRACKING_GRACE_SECONDS: Final[float] = 30.0

#: Remote Task states that end a long-running engineer run.
_TASK_STATE_FINAL_STATES: Final[dict[str, str]] = {
    "TASK_STATE_COMPLETED": FINAL_STATE_TASK_COMPLETED,
    "TASK_STATE_REJECTED": FINAL_STATE_TASK_REJECTED,
    "TASK_STATE_FAILED": FINAL_STATE_TASK_FAILED,
    "TASK_STATE_CANCELED": FINAL_STATE_TASK_FAILED,
}


@dataclass(frozen=True)
class _PolledTask:
    """One polled remote Task observed by the client.

    A poll is a local snapshot, not a wire message: it yields the authoritative state and the
    report, never synthetic protocol events.
    """

    state_name: str
    context_id: str
    reports: list[dict[str, object]]
    report: dict[str, object] | None
    #: Status message of an ``INPUT_REQUIRED`` Task (the vendor question, or the OSS relayed one).
    text: str = ""
    #: Metadata of that status message; the pure relay keeps ``round``/``recommendation`` here.
    metadata: dict[str, object] | None = None


def _cancel_label(run: RunState) -> str:
    """Return the error label recorded on a cancelled run."""
    if run.cancel_reason:
        return run.cancel_reason
    return CANCELED_BY_OPERATOR if run.cancel_requested else CANCELED_BY_SHUTDOWN


def _should_watch_long_task(run: RunState, result: ClientFlowResult | None) -> bool:
    """Whether a finished client flow really left a remote Task still executing."""
    return (
        result is not None
        and result.final_state == FINAL_STATE_INCOMPLETE
        and bool(result.task_id)
        and run.protocol in (PROTOCOL_A2A_T, PROTOCOL_PURE)
    )


def _final_state_for_task_state(
    state_name: str,
    text: object = "",
    *,
    has_report: bool = False,
) -> str | None:
    """Map one remote A2A Task state name to a run final state.

    The vendor NMS reports a refused request as ``COMPLETED`` plus an error sentence, so a polling
    watcher must apply the same rule as the streamed flow: an artifact-less completion whose status
    text carries a refusal phrase is a failure, not a success.
    """
    if state_name == "TASK_STATE_COMPLETED" and not has_report:
        if reports_failed_completion(text):
            return FINAL_STATE_TASK_FAILED
    return _TASK_STATE_FINAL_STATES.get(state_name)


def _question_round(metadata: Mapping[str, object] | None) -> int:
    """Return the negotiation round of one polled status message (0 when it carries none)."""
    pure = metadata.get("pure") if isinstance(metadata, Mapping) else None
    raw = pure.get("round") if isinstance(pure, Mapping) else None
    try:
        return int(raw) if raw is not None else 0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _artifact_payload(artifact: object, *, task_id: str, context_id: str) -> dict[str, object]:
    """Render one proto or JSON artifact as a normalized Run Explorer event."""
    if isinstance(artifact, Mapping):
        raw = dict(artifact)
        name = str(raw.get("name") or "")
        artifact_id = str(raw.get("artifactId") or raw.get("artifact_id") or "")
        parts = raw.get("parts")
    else:
        raw = MessageToDict(cast(Message, artifact))
        name = str(raw.get("name") or "")
        artifact_id = str(raw.get("artifactId") or "")
        parts = raw.get("parts")
    text_parts: list[str] = []
    data: dict[str, object] = {}
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, Mapping):
                continue
            text = part.get("text")
            if isinstance(text, str) and text:
                text_parts.append(text)
            payload = part.get("data")
            if isinstance(payload, Mapping):
                data = dict(payload)
    return {
        "kind": "artifact",
        "task_id": task_id,
        "context_id": context_id,
        "artifact_id": artifact_id,
        "name": name,
        "text": " ".join(text_parts).strip() or None,
        "data": data or None,
    }


def _polled_task_from_events(
    *,
    state_name: str,
    context_id: str,
    artifacts: list[dict[str, object]],
    text: str = "",
    metadata: dict[str, object] | None = None,
) -> _PolledTask:
    """Build the report of one polled Task.

    ``artifacts`` is the rendered snapshot of the remote Task. Polling must not turn that snapshot
    into ``status``/``artifact`` events: those look exactly like wire messages the peer never sent.
    The run timeline records a local ``task_poll`` row instead.
    """
    reports = [
        {
            "name": event.get("name"),
            "text": event.get("text"),
            "data": event.get("data"),
        }
        for event in artifacts
        if isinstance(event.get("name"), str) and "report" in str(event.get("name")).lower()
    ]
    if not reports and artifacts:
        last = artifacts[-1]
        reports = [{"name": last.get("name"), "text": last.get("text"), "data": last.get("data")}]
    return _PolledTask(
        state_name=state_name,
        context_id=context_id,
        reports=reports,
        report=reports[-1] if reports else None,
        text=text,
        metadata=metadata,
    )


def _polled_task_from_a2a(task: object, task_id: str) -> _PolledTask:
    """Render one proto Task returned by ``Client.get_task``."""
    context_id = str(getattr(task, "context_id", "") or "")
    status = getattr(task, "status", None)
    state_name = TaskState.Name(status.state) if status is not None else ""
    text = ""
    metadata: dict[str, object] | None = None
    status_message = getattr(status, "message", None) if status is not None else None
    if status_message is not None:
        text = " ".join(part.text for part in getattr(status_message, "parts", ()) or () if part.text).strip()
        try:
            raw_metadata = MessageToDict(status_message.metadata)
        except Exception:  # noqa: BLE001 - metadata is an observability aid, not the state
            raw_metadata = {}
        metadata = raw_metadata or None
    artifacts = [
        _artifact_payload(artifact, task_id=task_id, context_id=context_id)
        for artifact in getattr(task, "artifacts", []) or []
    ]
    return _polled_task_from_events(
        state_name=state_name,
        context_id=context_id,
        artifacts=artifacts,
        text=text,
        metadata=metadata,
    )


def _polled_task_from_nms(task: Mapping[str, object], task_id: str) -> _PolledTask:
    """Render one vendor Task document returned by ``NmsClient.get_task``."""
    status = task.get("status")
    state_name = str(status.get("state") or "") if isinstance(status, Mapping) else ""
    context_id = str(task.get("contextId") or task.get("context_id") or "")
    text = ""
    metadata: dict[str, object] | None = None
    message = status.get("message") if isinstance(status, Mapping) else None
    if isinstance(message, Mapping):
        parts = message.get("parts")
        text = " ".join(
            str(part.get("text") or "")
            for part in (parts if isinstance(parts, list) else [])
            if isinstance(part, Mapping) and part.get("text")
        ).strip()
        raw_metadata = message.get("metadata")
        metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else None
    raw_artifacts = task.get("artifacts")
    artifacts = [
        _artifact_payload(artifact, task_id=task_id, context_id=context_id)
        for artifact in (raw_artifacts if isinstance(raw_artifacts, list) else [])
    ]
    return _polled_task_from_events(
        state_name=state_name,
        context_id=context_id,
        artifacts=artifacts,
        text=text,
        metadata=metadata,
    )


def _recommendation_frame(
    *,
    mode: str,
    propose: NegotiationProposeView,
    session: NegotiationContext,
    recommendation: NegotiationDecision | None,
    recommendation_error: str | None,
    timeout_seconds: float | None,
    requested_at: str,
) -> dict[str, object]:
    """Build the confirm-mode ``decision_requested`` frame.

    A failed recommendation is published as ``recommendationUnavailable`` instead of a decision:
    the proposal still opens the page's manual controls, so an agent failure never takes the
    engineer's decision away.
    """
    frame: dict[str, object] = {
        "type": "decision_requested",
        "mode": mode,
        "proposal": propose.model_dump(),
        "round": session.round,
        "maxRounds": session.max_rounds,
        "performative": session.performative.value,
        "phase": "negotiation",
        "timeoutSeconds": timeout_seconds,
        "requestedAt": requested_at,
        # What the information proposal asks the engineer to supply. The page uses it for the card
        # text and to pick a matching manual example; it is empty for target/feasibility.
        "missingItems": list(missing_item_labels(propose.content)),
    }
    if recommendation is not None:
        frame["recommendation"] = recommendation.model_dump()
    else:
        frame["recommendationUnavailable"] = True
        frame["recommendationError"] = (
            recommendation_error or "The OSS Agent produced no recommendation for this proposal."
        )
    return frame


def _decision_origin(mode: str) -> str:
    """Return who produced a decision in this mode (``human`` or ``agent``)."""
    return "human" if mode in (MODE_MANUAL, MODE_CONFIRM) else "agent"


def _waits_for_recommendation(run: RunState) -> bool:
    """Whether this run's confirmation waits for an agent recommendation before the page.

    A pure confirmation owns its own answer surface and publishes synchronously, so it must not
    receive the A2A-T pending frame.
    """
    return run.mode == MODE_CONFIRM and run.protocol != PROTOCOL_PURE


class RunManager:
    """Owns client runs and conversations of one process.

    One conversation has at most one active run; different conversations may run concurrently.
    When a :class:`ClientStore` is configured, every lifecycle transition is persisted, the recent
    turns are injected into the next run, and a duplicate request carrying the same idempotency key
    returns the existing run instead of starting a second one.
    """

    def __init__(
        self,
        *,
        config: SampleConfig,
        runtime_factory: RuntimeFactory,
        catalog: RunCatalog,
        runs_dir: Path | None = None,
        store: ClientStore | None = None,
        memory_turns: int = 6,
        intent_review: bool = True,
        metrics_grace_seconds: float = 0.0,
    ) -> None:
        self._config = config
        self._runtime_factory = runtime_factory
        self._catalog = catalog
        self._runs_dir = runs_dir
        self._store = store
        self._intent_review = intent_review
        self._memory_turns = max(0, memory_turns)
        # The server trace emitter flushes in batches; waiting briefly before aggregating keeps
        # the final planner/tool spans in the token and latency totals.
        self._metrics_grace_seconds = max(0.0, metrics_grace_seconds)
        self._runs: dict[str, RunState] = {}
        self._active_by_conversation: dict[str, RunState] = {}
        self._last_session_sweep = 0.0
        self._metric_tasks: set[asyncio.Task[None]] = set()
        self._langfuse_tasks: set[asyncio.Task[None]] = set()

    @property
    def store(self) -> ClientStore | None:
        """Return the configured client store."""
        return self._store

    def get(self, run_id: str) -> RunState | None:
        """Return one run by id, ``None`` when unknown."""
        return self._runs.get(run_id)

    async def cancel(self, run_id: str, *, remote: bool = True, reason: str | None = None) -> bool:
        """Cancel one active run, asking the remote side to cancel its Task first.

        The remote cancel is best effort: the vendor NMS may answer HTTP 503, and the local run
        must stop regardless. The terminal ``finished`` frame is still published by the run task.

        Args:
            run_id: run to cancel.
            remote: also ask the transport to cancel its Task. The Running-tasks picker already
                cancelled the NMS Task and passes ``False`` to avoid a second ``:cancel``.
            reason: error label recorded on the run; the default distinguishes an operator cancel
                from a process shutdown. A handover passes :data:`HANDED_OVER`.

        Returns:
            ``True`` when a running run was cancelled, ``False`` for an unknown or finished run.
        """
        run = self._runs.get(run_id)
        if run is None or run.finished():
            return False
        run.cancel_requested = True
        run.cancel_reason = reason
        if remote:
            await self._cancel_remote_task(run)
        task = run.task
        if task is not None and not task.done():
            task.cancel()
            _, pending = await asyncio.wait({task}, timeout=CANCEL_UNWIND_TIMEOUT_SECONDS)
            if pending:
                _LOGGER.warning(
                    "run %s did not unwind within %.0fs after cancel",
                    run.run_id,
                    CANCEL_UNWIND_TIMEOUT_SECONDS,
                )
        return True

    def _task_handover_candidates(self, task_id: str) -> list[RunState]:
        """Return the live runs a Modify of ``task_id`` supersedes.

        Raises:
            RunConflictError: when a live run targets the Task but has not observed it yet. The
                create is still in flight, so there is no server-side Task to continue; the
                operator has to wait until the NMS accepts it (SUBMITTED) before modifying it.
        """
        owners = self.active_runs_for_task(task_id)
        if owners and not any(run.remote_task_id == task_id for run in owners):
            raise RunConflictError(
                f"Task {task_id} has not reached SUBMITTED yet; wait for the NMS to accept it "
                "before modifying it."
            )
        return owners

    async def _hand_over_task(self, task_id: str, new_run_id: str, superseded: Sequence[RunState]) -> None:
        """Stop the local runs that tracked a Task this Modify run now continues.

        The remote Task is untouched — the new run owns it from here. Stopping the previous run
        first releases its callback subscription and re-buffers any push it had not consumed, so a
        push that arrives before the new run binds the Task is handed to the new run instead of
        being answered by the run the operator just replaced.
        """
        for stale in superseded:
            _LOGGER.info(
                "run %s continues task %s; stopping run %s (handover)",
                new_run_id,
                task_id,
                stale.run_id,
            )
            await self.cancel(stale.run_id, remote=False, reason=HANDED_OVER)

    async def _cancel_remote_task(self, run: RunState) -> None:
        """Ask the runtime transport to cancel the remote Task, when it exposes one."""
        runtime = run.runtime
        transport = getattr(runtime, "transport", None) if runtime is not None else None
        cancel = getattr(transport, "cancel", None)
        if run.remote_task_id is None or not callable(cancel):
            return
        try:
            await asyncio.wait_for(
                cancel(run.remote_task_id),
                timeout=REMOTE_CANCEL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning("remote cancel timed out for task %s", run.remote_task_id)
        except Exception:  # noqa: BLE001 - cancel must never block the local stop
            _LOGGER.warning("remote cancel failed for task %s", run.remote_task_id, exc_info=True)

    def active_runs_for_task(self, task_id: str) -> list[RunState]:
        """Return the live runs whose remote or target Task is ``task_id``.

        The Running-tasks picker cancels an NMS Task, then stops every local run that still
        tracks it. Only exact ids match: a Task id is never inferred from another run.
        """
        if not task_id:
            return []
        return [
            run
            for run in self._runs.values()
            if not run.finished() and (run.remote_task_id == task_id or run.target_task_id == task_id)
        ]

    def active_for_conversation(self, conversation_id: str) -> RunState | None:
        """Return the active run of one conversation, when there is one."""
        run = self._active_by_conversation.get(conversation_id)
        return run if run is not None and not run.finished() else None

    def route_external_span(self, span: Mapping[str, object]) -> None:
        """Route a span emitted outside a run's task to the run named on the span.

        The Web UI installs this as the process sink: in-process servers and other producers emit
        from threads/tasks that cannot see a run-local sink, and the span's ``runId`` is the only
        reliable router. Unknown or missing run ids are dropped.
        """
        run_id = span.get("runId")
        if not isinstance(run_id, str) or not run_id:
            return
        run = self._runs.get(run_id)
        if run is not None:
            run.record_trace(span)

    async def mark_interrupted(self) -> int:
        """Mark runs left ``running`` by a previous process as ``interrupted``."""
        if self._store is None:
            return 0
        return await self._store.mark_running_interrupted()

    async def recover_runs(self, *, kinds: set[str] | None = None) -> int:
        """Resume the runs that still own a remote Task after a process restart.

        A run whose process died with a ``server_task_id`` is not a lost run: the Task may keep
        executing for days. Rehydrate it from the client store, rebuild a poll-only runtime, and
        keep tracking it. Runs without a remote Task (or with a still-live owner) are left to the
        previous behavior.

        Args:
            kinds: source kinds this process owns (``{"gateway"}`` for the gateway). ``None``
                means every non-gateway run, so the UI never steals an inbound gateway Task.
        """
        if self._store is None:
            return 0
        recovered = 0
        for record in await self._store.list_running_runs():
            source_kind = str(record.source.get("kind") or "")
            if kinds is None:
                if source_kind == "gateway":
                    continue
            elif source_kind not in kinds:
                continue
            if record.owner_pid and _pid_alive(record.owner_pid):
                # Another live client process (UI + gateway, two UIs) owns this run.
                continue
            if not record.server_task_id:
                await self._store.mark_run_interrupted(record.run_id)
                continue
            try:
                run = await self._rehydrate_run(record)
            except Exception:  # noqa: BLE001 - one bad run must not block the others
                _LOGGER.exception("failed to recover run %s", record.run_id)
                await self._store.mark_run_interrupted(record.run_id)
                continue
            await self._store.update_run(run.run_id, owner_pid=os.getpid())
            self._runs[run.run_id] = run
            if run.conversation_id:
                self._active_by_conversation[run.conversation_id] = run
            run.task = asyncio.create_task(self._resume_run(run), name=f"ran-client-resume-{run.run_id}")
            recovered += 1
            _LOGGER.info(
                "recovered run %s: task=%s conversation=%s",
                run.run_id,
                record.server_task_id,
                run.conversation_id or "-",
            )
        return recovered

    async def _rehydrate_run(self, record: ClientRunRecord) -> RunState:
        """Rebuild the in-memory view of one persisted running run."""
        run = RunState(
            run_id=record.run_id,
            # Rows written before the trace_id column fall back to the run id.
            trace_id=record.trace_id or record.run_id,
            scenario=record.scenario,
            mode=record.mode,
            transport=record.transport,
            protocol=record.protocol,
            discovery=record.discovery,
            user_request=record.user_request,
            status=STATUS_RUNNING,
            detached=True,
            conversation_id=record.conversation_id,
            chain_id=record.chain_id or record.conversation_id or record.run_id,
            parent_run_id=record.parent_run_id,
            idempotency_key=record.idempotency_key,
            downstream_task_id=record.downstream_task_id,
            downstream_context_id=record.downstream_context_id,
            downstream_hop=record.downstream_hop,
            store=self._store,
            source=dict(record.source),
        )
        run.transcript = NegotiationTranscript(
            scenario=record.scenario,
            language=self._config.language,
            model_mode=self._config.model_mode,
            mode=record.mode,
            transport=record.transport,
            trace_id=run.trace_id,
            runs_dir=self._runs_dir or DEFAULT_RUNS_DIR,
            source=dict(record.source),
        )
        run.result = ClientFlowResult(
            final_state=FINAL_STATE_INCOMPLETE,
            task_id=record.server_task_id,
            context_id=record.server_context_id or "",
        )
        run.runtime = await build_poll_runtime(self._config, discovery=record.discovery)
        run.emit_trace(
            kind="task_tracking",
            phase=PHASE_DISPATCH,
            title="Run recovered after a UI restart",
            detail={"taskId": record.server_task_id, "protocol": record.protocol},
        )
        return run

    async def _resume_run(self, run: RunState) -> None:
        """Poll a recovered run until its remote Task reaches a terminal state."""
        transcript = run.transcript
        assert transcript is not None
        try:
            result = await self._watch_long_task(run, run.result or ClientFlowResult())
            run.result = result
            transcript.record_final(result)
            run.status = STATUS_FINISHED
        except asyncio.CancelledError:
            run.status = STATUS_INTERRUPTED
            run.error = _cancel_label(run)
            transcript.record_error(run.error)
            raise
        except Exception as exc:  # noqa: BLE001 - a failed recovery must reach the UI
            _LOGGER.exception("recovered run %s failed", run.run_id)
            run.status = STATUS_FAILED
            run.error = f"{type(exc).__name__}: {exc}"
            transcript.record_error(run.error)
        finally:
            run.pending_decision = False
            run.pending_intent = False
            run.intent_future = None
            if run.runtime is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await run.runtime.aclose()
                run.runtime = None
            transcript.intent = run.processed_intent
            try:
                run.paths = transcript.write(self._runs_dir)
            except OSError as exc:
                run.transcript_error = f"{type(exc).__name__}: {exc}"
            await run.drain_persistence()
            await self._finalize_persistence(run)
            self._active_by_conversation.pop(run.conversation_id or "", None)
            run.emit_finished()

    async def start(
        self,
        scenario: str,
        mode: str,
        transport: str,
        discovery: str = DISCOVERY_REGISTRY,
        user_request: str | None = None,
        *,
        protocol: str = PROTOCOL_A2A_T,
        conversation_id: str | None = None,
        external_conversation_id: str | None = None,
        trace_id: str | None = None,
        chain_id: str | None = None,
        parent_run_id: str | None = None,
        user_id: str = "web-user",
        idempotency_key: str | None = None,
        intent_mode: str | None = None,
        source: Mapping[str, object] | None = None,
        target_task_id: str | None = None,
        target_context_id: str | None = None,
        target_server_task_id: str | None = None,
        target_server_context_id: str | None = None,
        scripted_decisions: Sequence[NegotiationDecision] | None = None,
        pure_answers: Sequence[str] | None = None,
        validate_before_send: bool | None = None,
        language: str | None = None,
    ) -> RunState:
        """Start one background run and broadcast ``run_started`` before the task is scheduled.

        ``user_request`` overrides the scenario's canned request when the caller supplied one.

        Args:
            scenario: scenario name.
            mode: decision mode.
            transport: event transport.
            discovery: discovery mode.
            user_request: optional natural-language request.
            protocol: interaction protocol (``a2a-t`` / ``plain``); keyword-only.
            conversation_id: client conversation; generated when omitted.
            external_conversation_id: caller-side conversation id (Gemini Enterprise, console).
            trace_id: stable vendor-facing trace id; defaults to the new run id.
            chain_id: stable id of one operator's correction chain; defaults to the new
                conversation id. A retry of a blocked attempt passes the chain id so Langfuse
                groups the attempts as one session, while each attempt keeps its own trace.
            parent_run_id: immediately preceding attempt of the same chain, when this run retries
                a blocked request.
            user_id: caller identity.
            idempotency_key: optional key that deduplicates a repeated request.
            intent_mode: per-run override of the process intent mode (``processed`` / ``raw``).
            source: optional request-origin document (page/caller/remote address); normalized
                through :func:`shared.run_source.normalize_run_source`.
            target_task_id: existing running Task a Modify-only run continues.
            target_context_id: context of the Task a Modify-only run continues.
            target_server_task_id: A2A peer's Task a pure Modify continues; the pair above is the
                vendor-facing picker identity and this is what the A2A message carries.
            target_server_context_id: context paired with ``target_server_task_id``.
            scripted_decisions: per-run A2A-T decision script used by ``MODE_SCRIPTED``; ``None``
                keeps the scenario's catalog script.
            pure_answers: per-run answers for the pure A2A ``INPUT_REQUIRED`` rounds, consumed in
                order; ``None`` keeps the process-level pure policy.
            validate_before_send: force the pre-send Task-T validation on or off; ``None`` uses the
                process setting and the source kind.
            language: per-run scenario language; ``None``/empty keeps the process language.

        Raises:
            RunConflictError: when the same conversation already has an active run.
            ValueError: when ``intent_mode`` is unsupported.
        """
        resolved_source = normalize_run_source(source)
        resolved_target_task = target_task_id.strip() if isinstance(target_task_id, str) else ""
        resolved_target_context = target_context_id.strip() if isinstance(target_context_id, str) else ""
        if bool(resolved_target_task) != bool(resolved_target_context):
            raise ValueError("target_task_id and target_context_id must be provided together.")
        resolved_server_task = (
            target_server_task_id.strip() if isinstance(target_server_task_id, str) else ""
        )
        resolved_server_context = (
            target_server_context_id.strip() if isinstance(target_server_context_id, str) else ""
        )
        if bool(resolved_server_task) != bool(resolved_server_context):
            raise ValueError(
                "target_server_task_id and target_server_context_id must be provided together."
            )
        run_mode = RUN_MODE_MODIFY if resolved_target_task or resolved_server_task else RUN_MODE_CREATE
        if run_mode == RUN_MODE_MODIFY:
            if protocol == PROTOCOL_PURE:
                if not resolved_server_task:
                    raise ValueError(
                        "A pure A2A Modify needs the outer Task id and context of the A2A peer."
                    )
            elif protocol != PROTOCOL_A2A_T:
                raise ValueError("A Modify-only run requires A2A-T or pure A2A.")
            elif not resolved_target_task:
                raise ValueError(
                    "A Modify-only run needs both the target Task id and its context id."
                )
        resolved_validate_before_send = (
            _should_validate_before_send(self._config, resolved_source)
            if validate_before_send is None
            else validate_before_send
        )
        resolved_intent_mode = (intent_mode or self._config.intent_mode).strip()
        if resolved_intent_mode not in SUPPORTED_INTENT_MODES:
            raise ValueError(
                f"Unsupported intent mode: {resolved_intent_mode}; expected one of {SUPPORTED_INTENT_MODES}"
            )
        resolved_trace_id = (trace_id or "").strip().lower()
        if resolved_trace_id and (
            len(resolved_trace_id) != 32
            or resolved_trace_id == "0" * 32
            or any(char not in "0123456789abcdef" for char in resolved_trace_id)
        ):
            raise ValueError("trace_id must be 32 lowercase hex digits.")
        resolved_conversation = conversation_id or f"conv-{uuid.uuid4().hex}"
        # The correction chain outlives a single run: every retry keeps this id while getting a
        # new run/conversation/trace. Defaulting it to the conversation id leaves a run without a
        # retry indistinguishable from today's runs.
        resolved_chain_id = (chain_id or "").strip() or resolved_conversation
        resolved_parent_run_id = (parent_run_id or "").strip()
        active = self.active_for_conversation(resolved_conversation)
        if active is not None:
            raise RunConflictError(
                f"Conversation {resolved_conversation} already has an active run "
                f"({active.run_id}); wait for it to finish."
            )
        resolved_key = idempotency_key.strip() if isinstance(idempotency_key, str) else ""
        if self._store is not None and resolved_key:
            existing = await self._store.find_run_by_idempotency(resolved_conversation, resolved_key)
            if existing is not None:
                reused = self._reuse_run(existing)
                self._runs[reused.run_id] = reused
                return reused

        custom_request = user_request.strip() if isinstance(user_request, str) else None
        if not custom_request:
            custom_request = None
        spec = self._catalog.spec(scenario, language=language or self._config.language)
        # The scenario request is the text the client sends; a Modify-only run sends the operator's
        # Modify text, so any A2A-T scenario can target an existing Task.
        raw_request = custom_request or spec.user_request
        run_uuid = uuid.uuid4().hex
        run = RunState(
            run_id=run_uuid,
            trace_id=resolved_trace_id or run_uuid,
            chain_id=resolved_chain_id,
            parent_run_id=resolved_parent_run_id,
            scenario=scenario,
            mode=mode,
            transport=transport,
            discovery=discovery,
            protocol=protocol,
            user_request=custom_request,
            conversation_id=resolved_conversation,
            external_conversation_id=external_conversation_id,
            user_id=user_id,
            idempotency_key=resolved_key,
            store=self._store,
            intent_mode=resolved_intent_mode,
            validate_before_send=resolved_validate_before_send,
            raw_request=raw_request,
            source=dict(resolved_source),
            run_mode=run_mode,
            target_task_id=resolved_target_task or None,
            target_context_id=resolved_target_context or None,
            target_server_task_id=resolved_server_task or None,
            target_server_context_id=resolved_server_context or None,
            scripted_decisions=tuple(scripted_decisions or ()),
            pure_answers=tuple(pure_answers or ()),
        )
        run.transcript = NegotiationTranscript(
            scenario=scenario,
            language=self._config.language,
            model_mode=self._config.model_mode,
            mode=mode,
            transport=transport,
            trace_id=run.trace_id,
            runs_dir=self._runs_dir or DEFAULT_RUNS_DIR,
            source=dict(resolved_source),
            run_mode=run_mode,
            target_task_id=resolved_target_task or None,
            target_context_id=resolved_target_context or None,
        )
        run.langfuse_exporter = create_langfuse_exporter(
            self._config,
            LangfuseRunMetadata(
                run_id=run.run_id,
                scenario=run.scenario,
                conversation_id=run.conversation_id,
                chain_id=run.chain_id,
                parent_run_id=run.parent_run_id,
                user_id=run.user_id,
                language=self._config.language,
                mode=run.mode,
                protocol=run.protocol,
                transport=run.transport,
                discovery=run.discovery,
                intent_mode=run.intent_mode,
                model_mode=self._config.model_mode,
                model_label=self._config.model_label,
                run_mode=run.run_mode,
                source_kind=str(resolved_source.get("kind") or ""),
                case_id=str(resolved_source.get("caseId") or ""),
                case_name=str(resolved_source.get("caseName") or ""),
                batch_id=str(resolved_source.get("batchId") or ""),
                received_at=str(resolved_source.get("receivedAt") or ""),
                user_request=raw_request or spec.user_request,
            ),
            # Give the run its own trace id as the Langfuse trace id: a UI-started run is then
            # findable by run id, and a retry that reuses the id shares one trace. The run trace id
            # is derived from the caller's traceparent when one is present, so a caller-joined
            # trace keeps working (it wins inside the exporter; the OTel parent does not).
            parent_trace_id=run.trace_id,
        )
        run.langfuse = run.langfuse_exporter.snapshot() if run.langfuse_exporter is not None else _langfuse_disabled()
        run.emit_run_started(
            description=spec.description,
            user_request=raw_request or spec.user_request,
            language=self._config.language,
            model_mode=self._config.model_mode,
            model_label=self._config.model_label,
            expected_sequence=[
                {"actor": step.actor, "performative": step.performative, "round": step.round}
                for step in spec.expected_sequence
            ],
        )
        try:
            # Claim the conversation before scheduling any work: another process may hold the
            # same conversation, and the database is the only authority both processes share.
            await self._prepare_persistence(run)
        except RunAlreadyActiveError as conflict:
            reused = await self._reuse_idempotent_run(conflict, resolved_key)
            if reused is not None:
                self._runs[reused.run_id] = reused
                return reused
            if self._store is not None:
                await self._store.mark_running_interrupted()
            try:
                await self._prepare_persistence(run)
            except RunAlreadyActiveError as exc:
                raise RunConflictError(
                    f"Conversation {resolved_conversation} already has an active run "
                    f"({exc.run_id or 'another process'}); wait for it to finish."
                ) from exc

        if run_mode == RUN_MODE_MODIFY:
            # Refuse before registering anything: a Task no run has observed yet has no server-side
            # identity to continue. Then stop the run the operator replaced before the new one
            # starts, so its callback subscription is released and its unread pushes are buffered.
            # A pure Modify names the vendor Task in the picker but continues the A2A peer's outer
            # Task; either id identifies a run that must hand the Task over.
            handover_ids = [
                candidate
                for candidate in (resolved_target_task, resolved_server_task)
                if candidate
            ]
            handover_ids = list(dict.fromkeys(handover_ids))
            superseded: list[RunState] = []
            seen: set[str] = set()
            for handover_id in handover_ids:
                for candidate in self._task_handover_candidates(handover_id):
                    if candidate.run_id in seen:
                        continue
                    seen.add(candidate.run_id)
                    superseded.append(candidate)
            handover_id = resolved_target_task or resolved_server_task
            try:
                await self._hand_over_task(handover_id, run.run_id, superseded)
            except Exception:  # noqa: BLE001 - the new run is already accepted
                _LOGGER.warning(
                    "handover of task %s to run %s did not stop the previous run",
                    handover_id,
                    run.run_id,
                    exc_info=True,
                )
        self._runs[run.run_id] = run
        self._active_by_conversation[resolved_conversation] = run
        # The trace id is already known, but the browser URL needs one Langfuse lookup. Resolve it
        # in the background so the page can link to the live trace instead of waiting for the end,
        # and so a slow Langfuse endpoint never delays the run itself.
        self._schedule_langfuse_url(run)
        run.task = asyncio.create_task(self._execute_in_context(run), name=f"ran-client-run-{run.run_id}")
        return run

    async def _reuse_idempotent_run(
        self,
        conflict: RunAlreadyActiveError,
        idempotency_key: str,
    ) -> RunState | None:
        """Return the existing run when a concurrent request reused the same idempotency key."""
        if (
            not idempotency_key
            or self._store is None
            or not conflict.run_id
            or conflict.idempotency_key != idempotency_key
        ):
            return None
        record = await self._store.get_run(conflict.run_id)
        return self._reuse_run(record) if record is not None else None

    async def _execute_in_context(self, run: RunState) -> None:
        """Run one client run with its correlation fields bound for every log record."""
        exporter = run.langfuse_exporter
        # Bind inside the task so the run owns the correlation for its whole life. A binding
        # around ``asyncio.create_task`` would leak into the caller's context and tie the run to
        # whatever snapshot the caller happened to hold at creation. Every coroutine of the run
        # inherits this binding, including the local A2A calls that carry it as traceparent.
        trace_tokens = set_active_trace(
            trace_id=getattr(exporter, "trace_id", "") if exporter is not None else "",
            parent_span_id=(getattr(exporter, "root_span_id", "") if exporter is not None else ""),
            run_id=run.run_id,
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            downstream_trace_id=run.trace_id,
        )
        try:
            await self._run_with_log_context(run)
        finally:
            reset_active_trace(trace_tokens)

    async def _run_with_log_context(self, run: RunState) -> None:
        """Run the flow under the run's log correlation context."""
        with log_context(
            run_id=run.run_id,
            conversation_id=run.conversation_id,
            role="client",
        ):
            _LOGGER.info(
                "run started: scenario=%s mode=%s transport=%s",
                run.scenario,
                run.mode,
                run.transport,
            )
            CLIENT_RUNS_IN_FLIGHT.inc()
            started = time.monotonic()
            try:
                await self._execute(run)
            finally:
                duration = time.monotonic() - started
                CLIENT_RUNS_IN_FLIGHT.dec()
                CLIENT_RUN_DURATION_SECONDS.observe(duration)
                CLIENT_RUNS_TOTAL.inc(
                    {
                        "status": run.status,
                        "final_state": (run.result.final_state if run.result is not None else "none"),
                    }
                )
                _LOGGER.info(
                    "run finished: status=%s final_state=%s task_id=%s duration=%.2fs",
                    run.status,
                    run.result.final_state if run.result is not None else "-",
                    run.result.task_id if run.result is not None and run.result.task_id else "-",
                    duration,
                )
                self._schedule_metrics(run)

    @property
    def metrics_grace_seconds(self) -> float:
        """Return how long a finished run waits for the last server trace spans."""
        return self._metrics_grace_seconds

    def metrics_for(self, run_id: str) -> dict[str, object] | None:
        """Return the live metrics of one run, ``None`` for an unknown run.

        A live run is the authority: the Web UI may ask for its metrics before the delayed
        persistence task has written them, and the in-memory spans are complete for that process.
        """
        run = self._runs.get(run_id)
        if run is None:
            return None
        return self._build_metrics(run)

    def _schedule_langfuse_url(self, run: RunState) -> None:
        """Resolve the Langfuse trace URL off the run path and publish it to the page."""
        if run.langfuse_exporter is None or run.langfuse.get("url"):
            return
        try:
            task = asyncio.create_task(self._resolve_langfuse_url(run), name=f"ran-langfuse-{run.run_id}")
        except RuntimeError:
            # The event loop is already closing (a synchronous test helper or shutdown).
            return
        self._langfuse_tasks.add(task)
        task.add_done_callback(self._langfuse_tasks.discard)

    async def _resolve_langfuse_url(self, run: RunState) -> None:
        """Turn the run's trace id into a clickable Langfuse URL as early as possible."""
        exporter = run.langfuse_exporter
        if exporter is None:
            return
        try:
            snapshot = await asyncio.to_thread(exporter.snapshot, resolve_url=True)
        except Exception as exc:  # noqa: BLE001 - observability must never break the run
            _LOGGER.warning("Langfuse trace URL resolution failed: %s: %s", type(exc).__name__, exc)
            return
        url = snapshot.get("url")
        if not url or url == run.langfuse.get("url"):
            return
        run.langfuse = snapshot
        run.broadcast({"type": "langfuse", "runId": run.run_id, "langfuse": snapshot})

    def _schedule_metrics(self, run: RunState) -> None:
        """Aggregate and persist one finished run's metrics after the trace flush window."""
        try:
            task = asyncio.create_task(self._persist_metrics(run), name=f"ran-metrics-{run.run_id}")
        except RuntimeError:
            # The event loop is already closing (a synchronous test helper or shutdown).
            return
        self._metric_tasks.add(task)
        task.add_done_callback(self._metric_tasks.discard)

    async def _persist_metrics(self, run: RunState) -> None:
        """Build, persist and mirror the metrics of one finished run."""
        try:
            if self._metrics_grace_seconds:
                await asyncio.sleep(self._metrics_grace_seconds)
            metrics = self._build_metrics(run)
            if self._store is not None:
                await self._store.save_run_metrics(metrics)
            await asyncio.to_thread(
                append_metrics_jsonl,
                self._runs_dir or DEFAULT_RUNS_DIR,
                metrics,
            )
        except Exception:  # noqa: BLE001 - metrics must never break a run
            _LOGGER.exception("run metrics aggregation failed for %s", run.run_id)

    def _build_metrics(self, run: RunState) -> dict[str, object]:
        """Build the metrics document of one run from its result, transcript and spans."""
        try:
            expected = self._catalog.spec(run.scenario, language=self._config.language)
        except KeyError:
            expected = None
        return build_run_metrics(
            run_id=run.run_id,
            conversation_id=run.conversation_id,
            protocol=run.protocol,
            scenario=run.scenario,
            mode=run.mode,
            transport=run.transport,
            language=self._config.language,
            model_mode=self._config.model_mode,
            model_label=self._config.model_label,
            status=run.status,
            transcript=run.transcript,
            result=run.result,
            spans=run.spans.values(),
            expected=expected,
            error=run.error,
            source=run.source,
            run_mode=run.run_mode,
            target_task_id=run.target_task_id,
            target_context_id=run.target_context_id,
        )

    def _reuse_run(self, record: ClientRunRecord) -> RunState:
        """Build a read-only run view for an idempotent replay."""
        run = RunState(
            run_id=record.run_id,
            # Rows written before the trace_id column fall back to the run id.
            trace_id=record.trace_id or record.run_id,
            scenario=record.scenario,
            mode=record.mode,
            transport=record.transport,
            discovery=record.discovery,
            protocol=record.protocol,
            user_request=record.user_request,
            status=cast(RunStatus, record.status),
            conversation_id=record.conversation_id,
            chain_id=record.chain_id or record.conversation_id or record.run_id,
            parent_run_id=record.parent_run_id,
            idempotency_key=record.idempotency_key,
            downstream_task_id=record.downstream_task_id,
            downstream_context_id=record.downstream_context_id,
            downstream_hop=record.downstream_hop,
            store=self._store,
            source=dict(record.source),
        )
        if record.status != STATUS_RUNNING:
            run.finished_at = time.monotonic()
        return run

    async def sweep_sessions(self, *, force: bool = True) -> None:
        """Delete decision sessions past the retention window.

        Called at startup (``force``) and before a run. A run is the only thing that grows the
        session table, so a run-triggered sweep is enough for a long-lived process; the hourly
        guard keeps it from scanning the table on every run.
        """
        now = time.monotonic()
        if not force and now - self._last_session_sweep < SESSION_SWEEP_INTERVAL_SECONDS:
            return
        self._last_session_sweep = now
        await sweep_stale_sessions(
            self._config.client_db_path,
            retention_days=self._config.adk_session_retention_days,
        )

    async def _execute(self, run: RunState) -> None:
        """Build the runtime, run the client flow, persist the outcome and publish the result."""
        transcript = run.transcript
        assert transcript is not None
        await self.sweep_sessions(force=False)

        def event_sink(event: dict[str, object]) -> None:
            run.record_event(event)

        #: Round whose operator wait is currently surfaced as an activity. The wait itself lives in
        #: the provider; this only mirrors it to late subscribers so a reload still shows why the
        #: run is idle.
        decision_wait_round: int | None = None

        def _decision_wait_detail(
            round_number: int,
            *,
            status: str,
            timeout_seconds: float | None,
            expired_windows: int,
        ) -> dict[str, object]:
            return {
                "activityId": f"decision-wait:{round_number}",
                "activityKind": "decision_wait",
                "labelKey": "waitingOperator",
                "stage": "waiting_operator",
                "status": status,
                "round": round_number,
                "timeoutSeconds": timeout_seconds,
                "expiredWindows": expired_windows,
            }

        def _show_decision_wait(round_number: int, timeout_seconds: float | None) -> None:
            nonlocal decision_wait_round
            decision_wait_round = round_number
            try:
                run.record_activity(
                    _decision_wait_detail(
                        round_number,
                        status="running",
                        timeout_seconds=timeout_seconds,
                        expired_windows=0,
                    )
                )
            except Exception:  # noqa: BLE001 - observation must not change the protocol result
                _LOGGER.warning("decision wait activity failed for run %s", run.run_id, exc_info=True)

        def _clear_decision_wait() -> None:
            nonlocal decision_wait_round
            round_number = decision_wait_round
            if round_number is None:
                return
            decision_wait_round = None
            try:
                run.record_activity(
                    _decision_wait_detail(
                        round_number,
                        status="done",
                        timeout_seconds=None,
                        expired_windows=0,
                    )
                )
            except Exception:  # noqa: BLE001 - observation must not change the protocol result
                _LOGGER.warning("decision wait cleanup failed for run %s", run.run_id, exc_info=True)

        def on_decision_window_expired(expired: DecisionTimeoutError) -> None:
            """Mirror one expired local window; never produce or send a decision."""
            try:
                if expired.expired_windows == 1:
                    run.emit_trace(
                        kind="decision_wait_expired",
                        phase=PHASE_NEGOTIATION,
                        title="Decision window expired; still waiting",
                        actor="system",
                        round_number=expired.round_number,
                        detail={
                            "expiredWindows": expired.expired_windows,
                            "timeoutSeconds": expired.window_seconds,
                            "decidedBy": "timeout",
                            "sentToPeer": False,
                        },
                    )
                run.record_activity(
                    _decision_wait_detail(
                        expired.round_number,
                        status="running",
                        timeout_seconds=expired.window_seconds,
                        expired_windows=expired.expired_windows,
                    )
                )
            except Exception:  # noqa: BLE001 - observation must not change the protocol result
                _LOGGER.warning("decision wait expiry was not recorded for run %s", run.run_id, exc_info=True)

        def on_decision_requested(propose: NegotiationProposeView, session: NegotiationContext) -> None:
            # The proposals are recorded in both modes because the finished run pairs them with the
            # provider decisions; a manual decision panel only exists in manual mode.
            run.record_proposal(propose, session)
            run.proposals.append(propose)
            NEGOTIATION_ROUNDS_TOTAL.inc({"role": "server", "round": session.round})
            if _waits_for_recommendation(run):
                # Open the decision surface immediately, before the model answers. The operator
                # can decide now; a later recommendation is advisory and is dropped once the
                # operator has submitted.
                run.pending_decision = True
                provider = getattr(run.runtime, "provider", None)
                recommendation_timeout = getattr(provider, "recommendation_timeout_seconds", None)
                run.broadcast(
                    {
                        "type": "decision_pending",
                        "mode": run.mode,
                        "proposal": propose.model_dump(),
                        "round": session.round,
                        "maxRounds": session.max_rounds,
                        "performative": session.performative.value,
                        "phase": "negotiation",
                        "recommendationTimeoutSeconds": (
                            float(recommendation_timeout)
                            if isinstance(recommendation_timeout, (int, float))
                            else None
                        ),
                        "requestedAt": now_iso(),
                        "missingItems": list(missing_item_labels(propose.content)),
                    }
                )
                _show_decision_wait(
                    session.round,
                    float(recommendation_timeout)
                    if isinstance(recommendation_timeout, (int, float))
                    else None,
                )
                return
            if run.mode != MODE_MANUAL:
                return
            run.pending_decision = True
            timeout_seconds = getattr(run.manual_provider, "timeout_seconds", None)
            run.broadcast(
                {
                    "type": "decision_requested",
                    "mode": run.mode,
                    "proposal": propose.model_dump(),
                    "round": session.round,
                    "maxRounds": session.max_rounds,
                    "performative": session.performative.value,
                    "phase": "negotiation",
                    # Tiny UI-support fields: the manual countdown would otherwise have to invent
                    # the provider budget, and a replay after a reload would restart it.
                    "timeoutSeconds": (float(timeout_seconds) if isinstance(timeout_seconds, (int, float)) else None),
                    "requestedAt": now_iso(),
                }
            )
            _show_decision_wait(
                session.round,
                float(timeout_seconds) if isinstance(timeout_seconds, (int, float)) else None,
            )

        def on_decision_made(
            decision: NegotiationDecision,
            propose: NegotiationProposeView,
            session: NegotiationContext,
        ) -> None:
            run.record_decision(
                decision,
                propose,
                session=session,
                decided_by=_decision_origin(run.mode),
            )
            # The decision is consumed: the run is no longer waiting for a submission.
            run.pending_decision = False
            _clear_decision_wait()
            NEGOTIATION_ROUNDS_TOTAL.inc({"role": "client", "round": session.round})

        def on_decision_recommended(
            decision: NegotiationDecision | None,
            propose: NegotiationProposeView,
            session: NegotiationContext,
        ) -> None:
            """Publish the recommendation, or an unavailable notice, and let the engineer decide."""
            if _waits_for_recommendation(run) and not run.pending_decision:
                # The operator already answered while the model was thinking. Publishing the late
                # recommendation would reopen a card they already resolved.
                return
            run.pending_decision = True
            timeout_seconds = getattr(run.manual_provider, "timeout_seconds", None)
            _show_decision_wait(
                session.round,
                float(timeout_seconds) if isinstance(timeout_seconds, (int, float)) else None,
            )
            # A pure run has no negotiation provider: its confirmation answer owns the human
            # moment and reports why a relayed recommendation is missing.
            provider = getattr(run.runtime, "provider", None) or getattr(run.runtime, "pure_answer", None)
            error = getattr(provider, "last_recommendation_error", None)
            frame = _recommendation_frame(
                mode=run.mode,
                propose=propose,
                session=session,
                recommendation=decision,
                recommendation_error=error if isinstance(error, str) and error else None,
                timeout_seconds=(float(timeout_seconds) if isinstance(timeout_seconds, (int, float)) else None),
                requested_at=now_iso(),
            )
            if decision is None:
                run.emit_trace(
                    kind="decision_recommendation_unavailable",
                    phase=PHASE_NEGOTIATION,
                    title="Decision recommendation unavailable",
                    round_number=session.round,
                    detail={
                        "reason": frame["recommendationError"],
                        "negotiationKind": propose.kind,
                    },
                )
            run.broadcast(frame)

        async def on_intent_ready(prompt_text: str) -> bool:
            """Ask the engineer to confirm the structured intent before the Task-T is sent."""
            future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            run.pending_intent = True
            run.intent_future = future
            run.emit_trace(
                kind="intent_review_requested",
                phase=PHASE_DISPATCH,
                title="Intent review requested",
                detail={"promptText": prompt_text, "timeoutSeconds": INTENT_REVIEW_TIMEOUT_SECONDS},
            )
            run.broadcast(
                {
                    "type": "intent_review_requested",
                    "promptText": prompt_text,
                    "timeoutSeconds": INTENT_REVIEW_TIMEOUT_SECONDS,
                    "requestedAt": now_iso(),
                }
            )
            try:
                return await asyncio.wait_for(future, timeout=INTENT_REVIEW_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                _LOGGER.info("intent review timed out for run %s", run.run_id)
                return False
            finally:
                run.pending_intent = False
                run.intent_future = None

        def trace_sink(span: dict[str, object]) -> None:
            run.record_trace(span)

        def activity_sink(payload: dict[str, object]) -> None:
            """Route one liveness envelope to this run's ephemeral wait-state channel."""
            detail = payload.get("detail")
            if isinstance(detail, Mapping):
                run.record_activity(detail)

        try:
            runtime_kwargs: dict[str, object] = {}
            if run.scripted_decisions:
                runtime_kwargs["scripted_decisions"] = run.scripted_decisions
            if run.pure_answers:
                runtime_kwargs["pure_answers"] = run.pure_answers
            run.runtime = await self._runtime_factory(
                # This manager keeps an incomplete remote Task active in the background, so the
                # NMS transport may hand the task over after a short silence instead of waiting
                # for the full HTTP budget.
                replace(self._config, intent_mode=run.intent_mode, defer_to_tracker=True),
                run.scenario,
                run.mode,
                run.transport,
                run.discovery,
                protocol=run.protocol,
                run_id=run.run_id,
                **runtime_kwargs,
            )
            run.emit_trace(
                kind="runtime_ready",
                phase=PHASE_DISPATCH,
                title="Client runtime ready",
                detail={
                    "mode": run.mode,
                    "transport": run.transport,
                    "discovery": run.discovery,
                    "language": self._config.language,
                    "modelMode": self._config.model_mode,
                    "modelLabel": self._config.model_label,
                },
            )
            discovered = getattr(run.runtime, "discovered", None)
            card = getattr(discovered, "agent_card", None)
            if run.mode == MODE_CONFIRM:
                set_recommendation_sink = getattr(run.runtime, "set_recommendation_sink", None)
                if callable(set_recommendation_sink):
                    set_recommendation_sink(on_decision_recommended)
            if run.mode in (MODE_MANUAL, MODE_CONFIRM) and run.protocol == PROTOCOL_A2A_T:
                set_wait_sink = getattr(run.runtime, "set_decision_wait_sink", None)
                if callable(set_wait_sink) and not set_wait_sink(on_decision_window_expired):
                    _LOGGER.warning(
                        "decision provider of run %s cannot report wait windows; the first expired "
                        "window will fail the run instead of sending an abort",
                        run.run_id,
                    )
            run.emit_trace(
                kind="discovery",
                phase=PHASE_DISPATCH,
                title="Server discovered",
                detail={
                    "discovery": run.discovery,
                    "url": getattr(discovered, "url", None),
                    "agentName": getattr(card, "name", None),
                },
            )
            result = await run.runtime.run(
                event_sink=event_sink,
                on_decision_requested=on_decision_requested,
                on_decision_made=on_decision_made,
                on_intent_ready=(on_intent_ready if run.mode == MODE_CONFIRM and self._intent_review else None),
                trace_sink=trace_sink,
                activity_sink=activity_sink,
                run_id=run.run_id,
                user_request=run.user_request,
                conversation_context=run.conversation_context,
                validate_before_send=run.validate_before_send,
                enforce_required_fields=_should_enforce_required_fields(run.source),
                require_rate_target=_requires_rate_target(run.source),
                site_level_rate_optional=bool(run.source.get("negotiationExpected")),
                run_mode=run.run_mode,
                target_task_id=run.target_task_id,
                target_context_id=run.target_context_id,
                target_server_task_id=run.target_server_task_id,
                target_server_context_id=run.target_server_context_id,
            )
            if run.pending_decision and not run.cancel_requested:
                # A provider that returns while the operator is still being waited for would let
                # the run finish as if the negotiation had concluded. Refuse to hide that state.
                raise RuntimeError(
                    "The client flow returned while a decision was still pending; refusing to "
                    "finish the run."
                )
            run.result = result
            if result.final_state == FINAL_STATE_INPUT_INVALID:
                run.error = _input_invalid_summary(run)
            elif result.final_state == FINAL_STATE_DECISION_UNGROUNDED:
                run.error = result.error or "The client decision had no grounded value."
            if _should_watch_long_task(run, result):
                result = await self._watch_long_task(run, result)
                run.result = result
            # Decisions are normally recorded live by ``on_decision_made``; the late loop is only
            # a fallback for a runtime double that does not call the hook.
            for index, decision in enumerate(result.decisions):
                if index < len(transcript.decisions):
                    continue
                propose = run.proposals[index] if index < len(run.proposals) else None
                run.record_decision(decision, propose, decided_by=_decision_origin(run.mode))
            transcript.intent = run.processed_intent
            transcript.record_final(result)
            run.status = STATUS_FINISHED
        except asyncio.CancelledError:
            run.status = STATUS_INTERRUPTED
            run.error = _cancel_label(run)
            transcript.record_error(run.error)
            raise
        except Exception as exc:  # noqa: BLE001 - a failed run must reach the UI, not a bare task
            _LOGGER.exception("client run %s failed", run.run_id)
            run.status = STATUS_FAILED
            run.error = f"{type(exc).__name__}: {exc}"
            transcript.record_error(run.error)
        finally:
            _clear_decision_wait()
            run.pending_decision = False
            run.pending_intent = False
            run.intent_future = None
            if run.runtime is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await run.runtime.aclose()
                # The runtime owns the ADK runner and its decision sessions; a finished run keeps
                # only the transcript, traces and result, so a long-lived UI/gateway process does
                # not accumulate one agent history per run.
                run.runtime = None
            transcript.intent = run.processed_intent
            try:
                run.paths = transcript.write(self._runs_dir)
            except OSError as exc:
                run.transcript_error = f"{type(exc).__name__}: {exc}"
            await run.drain_persistence()
            await self._finalize_persistence(run)
            self._active_by_conversation.pop(run.conversation_id or "", None)
            exporter = run.langfuse_exporter
            if exporter is not None:
                with suppress(asyncio.CancelledError, Exception):
                    # ``finish`` flushes over HTTP and resolves the trace URL; keep both off the
                    # event loop so a slow Langfuse endpoint cannot delay the terminal frame.
                    await asyncio.to_thread(exporter.finish, result=run.result, error=run.error)
                    run.langfuse = await asyncio.to_thread(exporter.snapshot, resolve_url=True)
            run.emit_finished()

    async def _watch_long_task(self, run: RunState, result: ClientFlowResult) -> ClientFlowResult:
        """Keep a run active while its remote Task executes, polling until a terminal state.

        The client flow returns ``incomplete`` as soon as the request stream closes. For a real NMS
        that is only the start of a task that may run for days, so the UI must not finish the run;
        this watcher polls the authoritative Task and records the terminal status/report.
        """
        task_id = result.task_id or ""
        # The tracker polls the authoritative Task, so the process-owned webhook subscription is
        # no longer needed. Releasing it stops pushes queueing for a run that may stay active for
        # days; the runtime's aclose() later sees the cleared field and does nothing.
        runtime = run.runtime
        subscription = getattr(runtime, "webhook_subscription", None) if runtime is not None else None
        if subscription is not None:
            with suppress(Exception):
                subscription.unsubscribe()
            with suppress(Exception):
                setattr(runtime, "webhook_subscription", None)
        background_interval = max(0.01, self._config.task_poll_interval_seconds)
        watched_interval = max(0.01, self._config.nms_state_poll_interval)
        # Only the engineer page is a viewer. A gateway run has a caller waiting for the result
        # and keeps its background cadence; a closed page switches a presentation run to the
        # background cadence but does not end it: the remote Task may still run for days, and the
        # page must be able to reattach.
        is_presentation = run.source.get("kind") == "presentation"
        interval = watched_interval if is_presentation else background_interval
        last_watched = time.monotonic()
        run.remote_task_id = task_id
        if self._store is not None:
            with suppress(Exception):
                await self._store.update_run(
                    run.run_id,
                    server_task_id=task_id,
                    server_context_id=result.context_id,
                    downstream_task_id=run.downstream_task_id,
                    downstream_context_id=run.downstream_context_id,
                    downstream_hop=run.downstream_hop,
                )
        run.emit_trace(
            kind="task_tracking",
            phase=PHASE_DISPATCH,
            title="Task handed to background tracking",
            detail={
                "taskId": task_id,
                "protocol": run.protocol,
                "discovery": run.discovery,
                "pollIntervalSeconds": interval,
            },
        )
        # The page must be able to tell "still waiting" from "nothing happened". This signal is
        # ephemeral: it is broadcast to live subscribers only, so a task polled for days does not
        # accumulate one replay message per poll.
        tracking_started_at = now_iso()
        state_since = tracking_started_at
        poll_count = 0
        poll_errors = 0

        def publish_wait_state(
            state: str,
            *,
            status: str = "running",
            detached: bool | None = None,
        ) -> None:
            """Publish the current wait state of the tracked Task (one frame per poll)."""
            run.record_activity(
                {
                    "activityId": f"nms-task:{task_id}",
                    "activityKind": "nms_task",
                    "labelKey": "waitingNmsTask",
                    "status": status,
                    "startedAt": tracking_started_at,
                    "taskId": task_id,
                    "state": state,
                    "stateSince": state_since,
                    "pollCount": poll_count,
                    "pollErrorCount": poll_errors,
                    "pollIntervalSeconds": interval,
                    "detached": run.detached if detached is None else detached,
                }
            )

        publish_wait_state("")
        last_state: str | None = None
        answered_questions: set[tuple[int, str]] = set()
        while True:
            if is_presentation:
                if run.subscribers:
                    if run.detached:
                        run.detached = False
                        _LOGGER.info("run %s has a viewer again; resuming fast tracking", run.run_id)
                        interval = watched_interval
                        publish_wait_state(last_state or "", detached=False)
                    last_watched = time.monotonic()
                elif not run.detached and time.monotonic() - last_watched >= _DETACHED_TRACKING_GRACE_SECONDS:
                    run.detached = True
                    _LOGGER.info(
                        "run %s has no viewer; continuing remote task tracking for %s in the background",
                        run.run_id,
                        task_id,
                    )
                    run.emit_trace(
                        kind="task_tracking",
                        phase=PHASE_DISPATCH,
                        title="Task tracking continues without a viewer",
                        detail={
                            "taskId": task_id,
                            "graceSeconds": _DETACHED_TRACKING_GRACE_SECONDS,
                            "pollIntervalSeconds": background_interval,
                        },
                    )
                    interval = background_interval
                    publish_wait_state(last_state or "", detached=True)
                interval = watched_interval if run.subscribers else background_interval
            try:
                polled = await self._poll_remote_task(run, task_id)
                poll_count += 1
                if polled.state_name and polled.state_name != last_state:
                    last_state = polled.state_name
                    state_since = now_iso()
                    # Polling observes the Task; it never fabricates a status update the peer
                    # did not send. The local snapshot row is explicitly client-origin.
                    run.record_polled_task_state(
                        task_id=task_id,
                        context_id=polled.context_id or result.context_id,
                        state=polled.state_name,
                    )
                if polled.state_name:
                    # Every poll publishes, including an unchanged one: a stable SUBMITTED state
                    # is still proof that the client is alive and still checking.
                    publish_wait_state(polled.state_name)
                if polled.state_name == "TASK_STATE_INPUT_REQUIRED" and run.protocol == PROTOCOL_PURE:
                    # The initial stream may have closed before the OSS Agent relayed the vendor
                    # question. Open the same human decision instead of waiting forever.
                    key = (_question_round(polled.metadata), polled.text)
                    if key not in answered_questions:
                        # Mark the question answered only after the answer was actually sent.
                        # An empty answer (manual timeout) or a transient send failure must leave
                        # the question open, otherwise the Task stays INPUT_REQUIRED forever.
                        if await self._answer_remote_question(run, task_id, polled):
                            answered_questions.add(key)
                final_state = _final_state_for_task_state(
                    polled.state_name,
                    polled.text,
                    has_report=bool(polled.reports),
                )
                if final_state is not None:
                    publish_wait_state(polled.state_name or last_state or "", status="done")
                    return replace(
                        result,
                        final_state=final_state,
                        context_id=polled.context_id or result.context_id,
                        reports=polled.reports,
                        report=polled.report or result.report,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a poll failure must not end a days-long run
                poll_errors += 1
                # A failed poll is not retried early: the next attempt keeps the normal cadence.
                # The vendor NMS delivers progress through its push/webhook channel, so a poll
                # failure costs at most one observation, not the run.
                _LOGGER.warning("task poll failed for run %s", run.run_id, exc_info=True)
                # Surface the retry: a silent failure looks exactly like an idle task.
                publish_wait_state(last_state or "")
            # Sleep in one-second slices so a disappearing viewer is noticed within the grace
            # window instead of after a full background poll interval.
            deadline = time.monotonic() + interval
            while time.monotonic() < deadline:
                if is_presentation and run.subscribers:
                    # A reattached page resumes the fast cadence immediately; the next outer
                    # iteration clears ``detached`` and broadcasts the change.
                    last_watched = time.monotonic()
                    if run.detached:
                        break
                await asyncio.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    async def _answer_remote_question(
        self,
        run: RunState,
        task_id: str,
        polled: _PolledTask,
    ) -> bool:
        """Open the same human decision for a question observed by polling.

        The pure answer callback is the one the page already uses: it publishes the decision frame
        and waits for the operator; this method then sends the answer back to the same Task.

        Returns ``True`` only when the answer was actually sent. An empty answer (manual timeout)
        or a send failure returns/raises without consuming the question, so the next poll can
        present it again instead of stranding the Task in ``INPUT_REQUIRED``.
        """
        runtime = run.runtime
        answer = getattr(runtime, "pure_answer", None) if runtime is not None else None
        transport = getattr(runtime, "transport", None) if runtime is not None else None
        if not callable(answer) or transport is None:
            return False
        event = {
            "kind": "status",
            "state": "TASK_STATE_INPUT_REQUIRED",
            "task_id": task_id,
            "context_id": polled.context_id,
            "text": polled.text,
            "metadata": dict(polled.metadata or {}),
        }
        answered = answer(event)
        if inspect.isawaitable(answered):
            answered = await answered
        text = answered
        if not isinstance(text, str) or not text.strip():
            return False
        # This answer resumes the caller→OSS Task, so it must use the streaming path's default
        # (taskId + contextId). ``A2AT_SAMPLE_PURE_FOLLOWUP`` configures the OSS→downstream hop;
        # applying it here drops the taskId the OSS needs to find the INPUT_REQUIRED Task.
        request = build_follow_up_request(
            text,
            task_id=task_id,
            context_id=polled.context_id or None,
        )
        await transport.send(
            request,
            ClientCallContext(
                timeout=self._config.http_timeout_seconds,
                service_parameters={"A2A-Extensions": ""},
            ),
        )
        return True

    async def _poll_remote_task(self, run: RunState, task_id: str) -> _PolledTask:
        """Poll the authoritative Task of one long-running run."""
        runtime = run.runtime
        if runtime is None:
            raise RuntimeError("The run has no runtime to poll.")
        if run.protocol == PROTOCOL_A2A_T and run.discovery == DISCOVERY_NMS:
            nms_client = getattr(runtime, "nms_client", None)
            if nms_client is None:
                raise RuntimeError("The run has no NMS client to poll.")
            task = await nms_client.get_task(task_id)
            return _polled_task_from_nms(task, task_id)
        a2a_client = getattr(runtime, "a2a_client", None)
        if a2a_client is None:
            raise RuntimeError("The run has no A2A client to poll.")
        task = await a2a_client.get_task(
            GetTaskRequest(id=task_id, history_length=0),
            context=ClientCallContext(timeout=self._config.http_timeout_seconds),
        )
        return _polled_task_from_a2a(task, task_id)

    async def _prepare_persistence(self, run: RunState) -> None:
        """Claim the conversation, load memory and append the user turn.

        The claim happens before the turn is appended so a rejected run leaves no duplicate turn
        behind.
        """
        if self._store is None or run.conversation_id is None:
            return
        request_text = run.user_request or self._catalog.spec(run.scenario, language=self._config.language).user_request
        await self._store.ensure_conversation(
            conversation_id=run.conversation_id,
            external_id=run.external_conversation_id or "",
            user_id=run.user_id or "",
            title=request_text,
        )
        if self._memory_turns > 0:
            turns = await self._store.list_recent_turns(run.conversation_id, self._memory_turns)
            run.conversation_context = _format_conversation_context(turns) or None
        await self._store.claim_run(
            ClientRunRecord(
                run_id=run.run_id,
                conversation_id=run.conversation_id,
                scenario=run.scenario,
                mode=run.mode,
                transport=run.transport,
                discovery=run.discovery,
                protocol=run.protocol,
                trace_id=run.trace_id,
                user_request=request_text,
                status=RUN_STATUS_RUNNING,
                owner_pid=os.getpid(),
                idempotency_key=run.idempotency_key,
                source_kind=str(run.source.get("kind") or ""),
                source=dict(run.source),
                chain_id=run.chain_id,
                parent_run_id=run.parent_run_id,
            )
        )
        user_turn = await self._store.append_turn(
            conversation_id=run.conversation_id,
            role=TURN_ROLE_USER,
            content=request_text,
            run_id=run.run_id,
        )
        if user_turn is not None:
            run._user_turn_id = user_turn.turn_id
            await self._store.update_run(run.run_id, turn_id=user_turn.turn_id)

    async def _finalize_persistence(self, run: RunState) -> None:
        """Persist the terminal run status and the assistant turn."""
        if self._store is None or run.conversation_id is None:
            return
        if run.status == STATUS_RUNNING:
            run.status = STATUS_INTERRUPTED
        result = run.result
        await self._store.update_run(
            run.run_id,
            status=run.status,
            final_state=result.final_state if result is not None else None,
            server_context_id=result.context_id if result is not None else None,
            server_task_id=result.task_id if result is not None else None,
            downstream_task_id=run.downstream_task_id,
            downstream_context_id=run.downstream_context_id,
            downstream_hop=run.downstream_hop,
            error=run.error,
            transcript_json=str(run.paths.json_path) if run.paths is not None else None,
            transcript_markdown=(str(run.paths.markdown_path) if run.paths is not None else None),
            finished_at=now_iso(),
        )
        question = _question_turn(run)
        if question is not None:
            await self._store.append_turn(
                conversation_id=run.conversation_id,
                role=TURN_ROLE_ASSISTANT,
                content=question,
                run_id=run.run_id,
            )
        await self._store.append_turn(
            conversation_id=run.conversation_id,
            role=TURN_ROLE_ASSISTANT,
            content=_run_summary(run),
            run_id=run.run_id,
        )

    async def list_runs(
        self,
        limit: int = 50,
        conversation_id: str | None = None,
    ) -> list[ClientRunRecord]:
        """Return persisted runs, newest first."""
        if self._store is None:
            return []
        return await self._store.list_runs(limit, conversation_id)

    async def shutdown(self) -> None:
        """Cancel every still-running background run (app shutdown)."""
        tasks = [
            run.task for run in self._active_by_conversation.values() if run.task is not None and not run.task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.gather(*tasks, return_exceptions=True)
        if self._metric_tasks:
            # The run has finished; let the short trace-flush window elapse so its metrics are
            # persisted before the process exits.
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.gather(*tuple(self._metric_tasks), return_exceptions=True)
        if self._langfuse_tasks:
            # A URL lookup is best effort; do not let a slow Langfuse endpoint delay shutdown.
            for task in tuple(self._langfuse_tasks):
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.gather(*tuple(self._langfuse_tasks), return_exceptions=True)


def _format_conversation_context(turns: list[Any]) -> str:
    """Render recent turns as one delimited prompt block."""
    lines: list[str] = []
    total = 0
    for turn in turns:
        content = " ".join(str(turn.content).split())
        if len(content) > 500:
            content = content[:499] + "…"
        line = f"{turn.role}: {content}"
        if total + len(line) > 4000:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


#: Entry points whose requests must satisfy the caller schema's required fields. A gateway or API
#: caller keeps its previous behavior this iteration; the interactive entry points do not send an
#: incomplete Task-T to the network.
_REQUIRED_FIELD_SOURCE_KINDS: Final[frozenset[str]] = frozenset({"presentation", "tester", "explorer", "cli"})


def _should_validate_before_send(
    config: SampleConfig,
    source: Mapping[str, object],
) -> bool:
    """Return whether this run enables the client-side Task-T validation gate."""
    setting = config.pre_send_validation
    return setting == PRE_SEND_VALIDATION_ALWAYS or (
        setting == PRE_SEND_VALIDATION_ON and source.get("kind") == "presentation"
    )


def _should_enforce_required_fields(source: Mapping[str, object]) -> bool:
    """Return whether this run must satisfy the caller schema's required fields."""
    return str(source.get("kind") or "") in _REQUIRED_FIELD_SOURCE_KINDS


def _requires_rate_target(source: Mapping[str, object]) -> bool:
    """Return whether an area-level request must state its guaranteed rate.

    The interactive demo keeps the historical hard gate (the information scenario demonstrates
    it). The OSP acceptance runner must send the corpus' area-level requests that omit the rate;
    site-level requests still require it.
    """
    return str(source.get("kind") or "") != "tester"


def _input_invalid_summary(run: RunState) -> str:
    """Build the run error summary of a blocked pre-send validation."""
    for span in reversed(run.traces):
        if span.get("kind") != "task_validation":
            continue
        detail = span.get("detail")
        errors = detail.get("errors") if isinstance(detail, Mapping) else None
        if not isinstance(errors, list) or not errors:
            return "Input validation failed"
        first = errors[0]
        message = first.get("message") if isinstance(first, Mapping) else None
        suffix = f": {message}" if isinstance(message, str) and message.strip() else ""
        return f"Input validation failed: {len(errors)} blocking issue(s){suffix}"
    return "Input validation failed"


def _run_summary(run: RunState) -> str:
    """Build the assistant turn recorded for one finished run."""
    if run.error and run.result is not None and run.result.final_state == FINAL_STATE_INPUT_INVALID:
        return "Input validation failed; the request was not sent."
    if run.error:
        return f"Run failed: {run.error}"
    if run.result is None:
        return f"Run finished with status {run.status}."
    if run.protocol == PROTOCOL_PURE and run.result.final_state == FINAL_STATE_TASK_FAILED:
        # The vendor's refusal sentence is the answer the engineer needs; the generic final-state
        # line hides it from the conversation.
        detail = _last_status_text(run)
        if detail:
            return detail
    report = run.result.report
    if isinstance(report, Mapping):
        summary = report.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
    return f"Run completed with final state {run.result.final_state}."


def _last_status_text(run: RunState) -> str:
    """Return the latest non-empty status text recorded in this run's transcript."""
    if run.transcript is None:
        return ""
    for entry in reversed(run.transcript.events):
        if entry.get("entryType") != "event" or entry.get("kind") != "status":
            continue
        text = entry.get("summary")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _question_turn(run: RunState) -> str | None:
    """Render the relayed downstream questions as one assistant turn, or ``None``.

    A pure run has no structured negotiation channel: the vendor's text question is the actual
    interaction. Keeping it only in the Explorer leaves the conversation with the engineer's
    request and a generic ending.
    """
    if run.protocol != PROTOCOL_PURE or not run.proposals:
        return None
    downstream = "NMS" if run.discovery == DISCOVERY_NMS else "RAN Agent"
    questions: list[str] = []
    for propose in run.proposals:
        content = propose.content.strip()
        if content and content not in questions:
            questions.append(content)
    if not questions:
        return None
    return "\n\n".join(f"The {downstream} asked: {question}" for question in questions)
