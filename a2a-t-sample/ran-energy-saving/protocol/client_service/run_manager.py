"""Run lifecycle management: conversations, persistence and per-conversation concurrency."""

from __future__ import annotations

import asyncio
import json
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
)
from shared.run_source import normalize_run_source
from shared.trace import (
    PHASE_DISPATCH,
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
from protocol.decision_provider import sweep_stale_sessions
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
    """One polled remote Task rendered for the run timeline."""

    state_name: str
    context_id: str
    events: list[dict[str, object]]
    reports: list[dict[str, object]]
    report: dict[str, object] | None


def _should_watch_long_task(run: RunState, result: ClientFlowResult | None) -> bool:
    """Whether a finished client flow really left a remote Task still executing."""
    return (
        result is not None
        and result.final_state == FINAL_STATE_INCOMPLETE
        and bool(result.task_id)
        and run.protocol in (PROTOCOL_A2A_T, PROTOCOL_PURE)
    )


def _final_state_for_task_state(state_name: str) -> str | None:
    """Map one remote A2A Task state name to a run final state."""
    return _TASK_STATE_FINAL_STATES.get(state_name)


def _artifact_payload(artifact: object, *, task_id: str, context_id: str) -> dict[str, object]:
    """Render one proto or JSON artifact as a normalized Run Explorer event."""
    if isinstance(artifact, Mapping):
        raw = dict(artifact)
        name = str(raw.get("name") or "")
        artifact_id = str(raw.get("artifactId") or raw.get("artifact_id") or "")
        parts = raw.get("parts")
    else:
        raw = MessageToDict(artifact)
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
    task_id: str,
    context_id: str,
    artifacts: list[dict[str, object]],
) -> _PolledTask:
    """Build the timeline events and report of one polled Task."""
    events: list[dict[str, object]] = []
    if state_name:
        events.append(
            {
                "kind": "status",
                "task_id": task_id,
                "context_id": context_id,
                "state": state_name,
                "text": f"The remote task is {state_name}.",
            }
        )
    events.extend(artifacts)
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
        events=events,
        reports=reports,
        report=reports[-1] if reports else None,
    )


def _polled_task_from_a2a(task: object, task_id: str) -> _PolledTask:
    """Render one proto Task returned by ``Client.get_task``."""
    context_id = str(getattr(task, "context_id", "") or "")
    status = getattr(task, "status", None)
    state_name = TaskState.Name(status.state) if status is not None else ""
    artifacts = [
        _artifact_payload(artifact, task_id=task_id, context_id=context_id)
        for artifact in getattr(task, "artifacts", []) or []
    ]
    return _polled_task_from_events(
        state_name=state_name,
        task_id=task_id,
        context_id=context_id,
        artifacts=artifacts,
    )


def _polled_task_from_nms(task: Mapping[str, object], task_id: str) -> _PolledTask:
    """Render one vendor Task document returned by ``NmsClient.get_task``."""
    status = task.get("status")
    state_name = str(status.get("state") or "") if isinstance(status, Mapping) else ""
    context_id = str(task.get("contextId") or task.get("context_id") or "")
    raw_artifacts = task.get("artifacts")
    artifacts = [
        _artifact_payload(artifact, task_id=task_id, context_id=context_id)
        for artifact in (raw_artifacts if isinstance(raw_artifacts, list) else [])
    ]
    return _polled_task_from_events(
        state_name=state_name,
        task_id=task_id,
        context_id=context_id,
        artifacts=artifacts,
    )


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

    @property
    def store(self) -> ClientStore | None:
        """Return the configured client store."""
        return self._store

    def get(self, run_id: str) -> RunState | None:
        """Return one run by id, ``None`` when unknown."""
        return self._runs.get(run_id)

    async def cancel(self, run_id: str, *, remote: bool = True) -> bool:
        """Cancel one active run, asking the remote side to cancel its Task first.

        The remote cancel is best effort: the vendor NMS may answer HTTP 503, and the local run
        must stop regardless. The terminal ``finished`` frame is still published by the run task.

        Args:
            run_id: run to cancel.
            remote: also ask the transport to cancel its Task. The Running-tasks picker already
                cancelled the NMS Task and passes ``False`` to avoid a second ``:cancel``.

        Returns:
            ``True`` when a running run was cancelled, ``False`` for an unknown or finished run.
        """
        run = self._runs.get(run_id)
        if run is None or run.finished():
            return False
        run.cancel_requested = True
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
            run.task = asyncio.create_task(
                self._resume_run(run), name=f"ran-client-resume-{run.run_id}"
            )
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
            # The store does not persist the vendor trace id; the run id is the fallback.
            trace_id=record.run_id,
            scenario=record.scenario,
            mode=record.mode,
            transport=record.transport,
            protocol=record.protocol,
            discovery=record.discovery,
            user_request=record.user_request,
            status=STATUS_RUNNING,
            detached=True,
            conversation_id=record.conversation_id,
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
            run.error = CANCELED_BY_OPERATOR if run.cancel_requested else CANCELED_BY_SHUTDOWN
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
        user_id: str = "web-user",
        idempotency_key: str | None = None,
        intent_mode: str | None = None,
        source: Mapping[str, object] | None = None,
        target_task_id: str | None = None,
        target_context_id: str | None = None,
        scripted_decisions: Sequence[NegotiationDecision] | None = None,
        pure_answers: Sequence[str] | None = None,
        validate_before_send: bool | None = None,
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
            trace_id: stable vendor-facing trace id; defaults to the new run id. A retry of a
                blocked run passes the blocked run's trace id so the vendor sees one trace.
            user_id: caller identity.
            idempotency_key: optional key that deduplicates a repeated request.
            intent_mode: per-run override of the process intent mode (``processed`` / ``raw``).
            source: optional request-origin document (page/caller/remote address); normalized
                through :func:`shared.run_source.normalize_run_source`.
            target_task_id: existing running Task a Modify-only run continues.
            target_context_id: context of the Task a Modify-only run continues.
            scripted_decisions: per-run A2A-T decision script used by ``MODE_SCRIPTED``; ``None``
                keeps the scenario's catalog script.
            pure_answers: per-run answers for the pure A2A ``INPUT_REQUIRED`` rounds, consumed in
                order; ``None`` keeps the process-level pure policy.
            validate_before_send: force the pre-send Task-T validation on or off; ``None`` uses the
                process setting and the source kind.

        Raises:
            RunConflictError: when the same conversation already has an active run.
            ValueError: when ``intent_mode`` is unsupported.
        """
        resolved_source = normalize_run_source(source)
        resolved_target_task = target_task_id.strip() if isinstance(target_task_id, str) else ""
        resolved_target_context = target_context_id.strip() if isinstance(target_context_id, str) else ""
        if bool(resolved_target_task) != bool(resolved_target_context):
            raise ValueError("target_task_id and target_context_id must be provided together.")
        run_mode = RUN_MODE_MODIFY if resolved_target_task else RUN_MODE_CREATE
        if run_mode == RUN_MODE_MODIFY and protocol != PROTOCOL_A2A_T:
            raise ValueError("A Modify-only run requires the A2A-T protocol.")
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
        spec = self._catalog.spec(scenario, language=self._config.language)
        # The scenario request is the text the client sends; a Modify-only run sends the operator's
        # Modify text, so any A2A-T scenario can target an existing Task.
        raw_request = custom_request or spec.user_request
        run_uuid = uuid.uuid4().hex
        run = RunState(
            run_id=run_uuid,
            trace_id=resolved_trace_id or run_uuid,
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
            scripted_decisions=tuple(scripted_decisions or ()),
            pure_answers=tuple(pure_answers or ()),
        )
        run.transcript = NegotiationTranscript(
            scenario=scenario,
            language=self._config.language,
            model_mode=self._config.model_mode,
            mode=mode,
            transport=transport,
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

        self._runs[run.run_id] = run
        self._active_by_conversation[resolved_conversation] = run
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
            # The store does not persist the vendor trace id; a replay falls back to the run id.
            trace_id=record.run_id,
            scenario=record.scenario,
            mode=record.mode,
            transport=record.transport,
            discovery=record.discovery,
            protocol=record.protocol,
            user_request=record.user_request,
            status=cast(RunStatus, record.status),
            conversation_id=record.conversation_id,
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

        def on_decision_requested(propose: NegotiationProposeView, session: NegotiationContext) -> None:
            # The proposals are recorded in both modes because the finished run pairs them with the
            # provider decisions; a manual decision panel only exists in manual mode.
            run.record_proposal(propose, session)
            run.proposals.append(propose)
            NEGOTIATION_ROUNDS_TOTAL.inc({"role": "server", "round": session.round})
            # Confirm mode waits for the agent recommendation; its callback publishes the
            # decision frame with the recommendation attached, so the page never shows a
            # decision before the OSS Agent has one.
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

        def on_decision_made(
            decision: NegotiationDecision,
            propose: NegotiationProposeView,
            session: NegotiationContext,
        ) -> None:
            run.record_decision(decision, propose, session=session)
            NEGOTIATION_ROUNDS_TOTAL.inc({"role": "client", "round": session.round})

        def on_decision_recommended(
            decision: NegotiationDecision,
            propose: NegotiationProposeView,
            session: NegotiationContext,
        ) -> None:
            """Publish the recommendation and let the engineer confirm it."""
            run.pending_decision = True
            timeout_seconds = getattr(run.manual_provider, "timeout_seconds", None)
            run.broadcast(
                {
                    "type": "decision_requested",
                    "mode": run.mode,
                    "proposal": propose.model_dump(),
                    "recommendation": decision.model_dump(),
                    "round": session.round,
                    "maxRounds": session.max_rounds,
                    "performative": session.performative.value,
                    "phase": "negotiation",
                    "timeoutSeconds": (float(timeout_seconds) if isinstance(timeout_seconds, (int, float)) else None),
                    "requestedAt": now_iso(),
                }
            )

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
                run_id=run.run_id,
                user_request=run.user_request,
                conversation_context=run.conversation_context,
                validate_before_send=run.validate_before_send,
                enforce_required_fields=_should_enforce_required_fields(run.source),
                run_mode=run.run_mode,
                target_task_id=run.target_task_id,
                target_context_id=run.target_context_id,
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
                run.record_decision(decision, propose)
            transcript.intent = run.processed_intent
            transcript.record_final(result)
            run.status = STATUS_FINISHED
        except asyncio.CancelledError:
            run.status = STATUS_INTERRUPTED
            run.error = CANCELED_BY_OPERATOR if run.cancel_requested else CANCELED_BY_SHUTDOWN
            transcript.record_error(run.error)
            raise
        except Exception as exc:  # noqa: BLE001 - a failed run must reach the UI, not a bare task
            _LOGGER.exception("client run %s failed", run.run_id)
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
                runtime.webhook_subscription = None
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
        run.broadcast(
            {
                "type": "task_tracking",
                "taskId": task_id,
                "state": "working",
                "pollIntervalSeconds": interval,
                "updatedAt": now_iso(),
            }
        )
        last_signature: str | None = None
        while True:
            if is_presentation:
                if run.subscribers:
                    if run.detached:
                        run.detached = False
                        _LOGGER.info("run %s has a viewer again; resuming fast tracking", run.run_id)
                        run.broadcast(
                            {
                                "type": "task_tracking",
                                "taskId": task_id,
                                "state": "watching",
                                "pollIntervalSeconds": watched_interval,
                                "updatedAt": now_iso(),
                            }
                        )
                    last_watched = time.monotonic()
                elif (
                    not run.detached
                    and time.monotonic() - last_watched >= _DETACHED_TRACKING_GRACE_SECONDS
                ):
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
                    run.broadcast(
                        {
                            "type": "task_tracking",
                            "taskId": task_id,
                            "state": "detached",
                            "pollIntervalSeconds": background_interval,
                            "updatedAt": now_iso(),
                        }
                    )
                interval = watched_interval if run.subscribers else background_interval
            try:
                polled = await self._poll_remote_task(run, task_id)
                signature = json.dumps(
                    {"state": polled.state_name, "events": polled.events},
                    sort_keys=True,
                    default=str,
                )
                if signature != last_signature:
                    last_signature = signature
                    for event in polled.events:
                        run.record_event(event)
                    run.broadcast(
                        {
                            "type": "task_tracking",
                            "taskId": task_id,
                            "state": polled.state_name,
                            "pollIntervalSeconds": interval,
                            "updatedAt": now_iso(),
                        }
                    )
                final_state = _final_state_for_task_state(polled.state_name)
                if final_state is not None:
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
                _LOGGER.warning("task poll failed for run %s", run.run_id, exc_info=True)
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
                user_request=request_text,
                status=RUN_STATUS_RUNNING,
                owner_pid=os.getpid(),
                idempotency_key=run.idempotency_key,
                source_kind=str(run.source.get("kind") or ""),
                source=dict(run.source),
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
_REQUIRED_FIELD_SOURCE_KINDS: Final[frozenset[str]] = frozenset(
    {"presentation", "tester", "explorer", "cli"}
)


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
    report = run.result.report
    if isinstance(report, Mapping):
        summary = report.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
    return f"Run completed with final state {run.result.final_state}."
