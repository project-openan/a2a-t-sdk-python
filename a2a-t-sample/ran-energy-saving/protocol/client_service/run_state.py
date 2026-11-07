"""Client application service: conversations, runs and negotiation audit.

The Web UI, the CLI and the A2A gateway are inbound adapters over one application service. The
service owns the run lifecycle, conversation memory and persistence; it never depends on Starlette.

This module started as the Web UI's ``RunState``/``RunManager`` pair. It now also:

* persists conversations, turns, runs and negotiation events through :class:`ClientStore`;
* enforces one active run per conversation (different conversations may run concurrently);
* injects the recent conversation turns into the client decision prompt and Task-T generation;
* marks runs left ``running`` by a previous process as ``interrupted`` on startup.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol

from a2a_t.core.metadata import NegotiationContext
from shared.config import DISCOVERY_REGISTRY
from shared.models import NegotiationDecision, NegotiationProposeView
from shared.run_constants import PROTOCOL_A2A_T, RUN_MODE_CREATE
from shared.trace import (
    PHASE_DISPATCH,
    PHASE_DONE,
    Span,
    TraceRecorder,
    infer_decision_phase,
    infer_event_phase,
)

from protocol.client_flow import ClientFlowResult, DecisionMadeCallback
from protocol.client_runtime import MODE_AUTO, MODE_CONFIRM, MODE_MANUAL
from protocol.client_store import (
    NEGOTIATION_DIRECTION_INBOUND,
    NEGOTIATION_DIRECTION_OUTBOUND,
    ClientStore,
    NegotiationEventRecord,
    TaskSnapshotRecord,
)
from protocol.transcript import NegotiationTranscript, TranscriptPaths

_LOGGER = logging.getLogger("ran_energy_saving.client_service")

#: Minimum interval between run-triggered decision-session sweeps (startup sweeps ignore it).
SESSION_SWEEP_INTERVAL_SECONDS: Final[float] = 3600.0


class ClientRuntimeLike(Protocol):
    """Runtime surface the Web UI drives (the real :class:`ClientRuntime` or a test double)."""

    manual_provider: Any | None

    async def run(
        self,
        *,
        event_sink: Callable[[dict[str, object]], None] | None = None,
        on_decision_requested: Callable[[NegotiationProposeView, NegotiationContext], None] | None = None,
        on_decision_made: DecisionMadeCallback | None = None,
        trace_sink: Callable[[dict[str, object]], None] | None = None,
        run_id: str | None = None,
        user_request: str | None = None,
        conversation_context: str | None = None,
        validate_before_send: bool = False,
        run_mode: str = RUN_MODE_CREATE,
        target_task_id: str | None = None,
        target_context_id: str | None = None,
    ) -> ClientFlowResult: ...

    async def aclose(self) -> None: ...


#: Builds one client runtime. The protocol is keyword-only so adding it did not shift the
#: positional scenario/mode/transport/discovery arguments.
RuntimeFactory = Callable[..., Awaitable[ClientRuntimeLike]]

#: Run statuses owned by the client service.
RunStatus = Literal["running", "finished", "failed", "interrupted"]
STATUS_RUNNING: Final[RunStatus] = "running"
STATUS_FINISHED: Final[RunStatus] = "finished"
STATUS_FAILED: Final[RunStatus] = "failed"
STATUS_INTERRUPTED: Final[RunStatus] = "interrupted"

#: Modes the Web UI may start; the scripted mode is a CLI/test seam only.
UI_MODES: Final[tuple[str, ...]] = (MODE_AUTO, MODE_MANUAL, MODE_CONFIRM)


class RunConflictError(RuntimeError):
    """Raised when a run is already active."""


@dataclass
class RunState:
    """Mutable state of one Web-UI run.

    Attributes:
        run_id: Identifier returned by ``POST /api/runs``.
        scenario: scenario name.
        mode: decision mode (``auto`` / ``manual``).
        protocol: interaction protocol (``a2a-t`` / ``plain``).
        transport: event transport.
        user_request: optional natural-language request supplied by the UI; ``None`` uses the
            scenario default.
        status: ``running`` / ``finished`` / ``failed``.
        detached: whether the page stopped watching while the remote Task still runs; tracking
            continues at the background cadence and the run does not end.
        messages: every SSE message broadcast so far (replayed to late subscribers).
        subscribers: live SSE queues.
        transcript: transcript recorded while the run executes.
        runtime: assembled client runtime, once built.
        result: client flow result, once finished.
        paths: transcript file paths, once written.
        error: failure message of a failed run.
        transcript_error: failure message when the transcript could not be written.
        pending_decision: whether the manual provider currently waits for a decision.
        pending_intent: whether the run waits for approval 1 (the OSS intent review).
        intent_future: resolver of the pending intent review.
        cancel_requested: whether the operator asked to cancel the run.
        remote_task_id: last Task id observed on the remote side (cancel target).
        intent_mode: ``processed`` or ``raw`` for this run.
        validate_before_send: whether the client validates the rendered Task-T before sending it.
        run_mode: ``create`` or ``modify`` (Modify-only against an existing Task).
        target_task_id: existing Task a Modify-only run continues.
        target_context_id: context of the Task a Modify-only run continues.
        raw_request: operator request before the business intent layer.
        normalized_request: canonical text produced by the business intent layer.
        processed_intent: structured intent document, once the layer has run.
        scripted_decisions: per-run A2A-T decision script used by ``MODE_SCRIPTED``.
        pure_answers: per-run answers for the pure A2A ``INPUT_REQUIRED`` rounds.
        proposals: inbound proposals in order.
        discovery: discovery mode of the run.
        traces: every trace span produced/ingested for the run.
        spans: span lookup by id.
        task: background task of the run.
        finished_at: monotonic time the terminal frame was broadcast, when it is known.
        conversation_id: client conversation owning this run.
        trace_id: Stable vendor-facing trace id; a retry can inherit it from the blocked run.
        external_conversation_id: caller-side conversation id (for example Gemini Enterprise).
        user_id: caller identity.
        idempotency_key: optional caller key preventing a duplicate run.
        conversation_context: rendered recent turns injected into prompts.
        store: optional client persistence store.
        langfuse_exporter: optional Langfuse exporter of this run (``None`` when disabled).
        langfuse: Langfuse trace snapshot shown by the API and the presentation UI.
        source: Request origin (page, caller, remote address) recorded at creation.
    """

    run_id: str
    scenario: str
    mode: str
    transport: str
    protocol: str = PROTOCOL_A2A_T
    discovery: str = DISCOVERY_REGISTRY
    user_request: str | None = None
    status: RunStatus = STATUS_RUNNING
    detached: bool = False
    conversation_id: str | None = None
    trace_id: str = ""
    external_conversation_id: str | None = None
    user_id: str | None = None
    idempotency_key: str = ""
    conversation_context: str | None = None
    messages: list[dict[str, object]] = field(default_factory=list)
    subscribers: set[asyncio.Queue[dict[str, object]]] = field(default_factory=set)
    transcript: NegotiationTranscript | None = None
    runtime: ClientRuntimeLike | None = None
    result: ClientFlowResult | None = None
    paths: TranscriptPaths | None = None
    error: str | None = None
    transcript_error: str | None = None
    pending_decision: bool = False
    pending_intent: bool = False
    intent_future: asyncio.Future[bool] | None = None
    cancel_requested: bool = False
    remote_task_id: str | None = None
    #: Downstream Task the server continued on its own far side (NMS / RAN Agent), when it reports
    #: one. These never replace ``result.task_id``/``context_id``: those belong to this run's hop.
    downstream_task_id: str | None = None
    downstream_context_id: str | None = None
    downstream_hop: str = ""
    intent_mode: str = ""
    validate_before_send: bool = False
    #: ``create`` for a new A2A Task, ``modify`` for a Modify-only run against an existing one.
    run_mode: str = RUN_MODE_CREATE
    target_task_id: str | None = None
    target_context_id: str | None = None
    raw_request: str | None = None
    normalized_request: str | None = None
    processed_intent: dict[str, object] | None = None
    #: Per-run A2A-T decision script (``MODE_SCRIPTED``); empty uses the catalog script.
    scripted_decisions: tuple[NegotiationDecision, ...] = ()
    #: Per-run answers for the pure A2A ``INPUT_REQUIRED`` rounds, consumed in order.
    pure_answers: tuple[str, ...] = ()
    proposals: list[NegotiationProposeView] = field(default_factory=list)
    traces: list[dict[str, object]] = field(default_factory=list)
    spans: dict[str, dict[str, object]] = field(default_factory=dict)
    task: asyncio.Task[None] | None = None
    finished_at: float | None = None
    langfuse_exporter: Any | None = field(default=None, repr=False)
    langfuse: dict[str, object] = field(default_factory=dict)
    source: dict[str, object] = field(default_factory=dict)
    store: ClientStore | None = field(default=None, repr=False)
    _seq: int = field(default=0, repr=False)
    _phase_steps: dict[str, int] = field(default_factory=dict, repr=False)
    _span_id_map: dict[str, str] = field(default_factory=dict, repr=False)
    _producer_spans: dict[str, str] = field(default_factory=dict, repr=False)
    _nego_seq: int = field(default=0, repr=False)
    _user_turn_id: str | None = field(default=None, repr=False)
    _persist_tasks: set[asyncio.Task[object]] = field(default_factory=set, repr=False)
    _recorder: TraceRecorder = field(init=False, repr=False)
    #: Guards the span/sequence stores: an in-process server may ingest spans from another thread.
    _trace_lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Create the per-run span recorder (UI ids never collide with client-flow ids)."""
        self._recorder = TraceRecorder(self.run_id, id_prefix="ui")

    def next_negotiation_seq(self) -> int:
        """Allocate the next negotiation-event sequence number."""
        self._nego_seq += 1
        return self._nego_seq

    def persist(self, awaitable: Coroutine[Any, Any, object]) -> None:
        """Schedule one persistence operation without blocking the protocol loop.

        Persistence must never break a run, so the scheduled task swallows exceptions (the store
        already logs them) and the result is ignored. ``drain_persistence`` awaits every pending
        write before the run publishes its terminal frame.
        """
        if self.store is None:
            self._close_awaitable(awaitable)
            return
        try:
            task = asyncio.create_task(awaitable)
        except RuntimeError:
            # No running loop (synchronous test helper): the write is simply skipped.
            self._close_awaitable(awaitable)
            return
        self._persist_tasks.add(task)
        task.add_done_callback(self._persist_tasks.discard)

    @staticmethod
    def _close_awaitable(awaitable: Coroutine[Any, Any, object]) -> None:
        """Close an unused coroutine without triggering a 'never awaited' warning."""
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()

    async def drain_persistence(self) -> None:
        """Wait for every scheduled persistence write of this run."""
        while self._persist_tasks:
            await asyncio.gather(*tuple(self._persist_tasks), return_exceptions=True)

    @property
    def manual_provider(self) -> Any | None:
        """The manual provider of the runtime, when the run uses one."""
        return self.runtime.manual_provider if self.runtime is not None else None

    @property
    def manual_structurer(self) -> Any | None:
        """The live decision model that structures a manual answer, when the run has one."""
        return getattr(self.runtime, "manual_structurer", None) if self.runtime is not None else None

    def resolve_intent(self, approved: bool) -> bool:
        """Resolve the pending intent review, returning whether one was waiting."""
        future = self.intent_future
        if not self.pending_intent or future is None or future.done():
            return False
        future.set_result(approved)
        return True

    def next_seq(self) -> int:
        """Allocate the next global ``seq`` without consuming a phase step."""
        with self._trace_lock:
            self._seq += 1
            return self._seq

    def allocate(self, phase: str) -> tuple[int, int]:
        """Allocate the next global ``seq`` and the next step number of one phase."""
        with self._trace_lock:
            seq = self.next_seq()
            step = self._phase_steps.get(phase, 0) + 1
            self._phase_steps[phase] = step
            return seq, step

    def broadcast(self, message: dict[str, object], *, seq: int | None = None) -> dict[str, object]:
        """Publish one SSE message with a strictly increasing ``seq``."""
        with self._trace_lock:
            if seq is None:
                seq = self.next_seq()
            else:
                self._seq = max(self._seq, seq)
            message["seq"] = seq
            self.messages.append(message)
            queues = tuple(self.subscribers)
        for queue in queues:
            queue.put_nowait(message)
        return message

    def emit_run_started(
        self,
        *,
        description: str,
        user_request: str,
        language: str,
        model_mode: str,
        model_label: str,
        expected_sequence: list[dict[str, object]],
    ) -> dict[str, object]:
        """Broadcast the first frame of the run (replayed to every late subscriber)."""
        seq, step = self.allocate(PHASE_DISPATCH)
        detail: dict[str, object] = {
            "scenario": self.scenario,
            "description": description,
            "userRequest": user_request,
            "language": language,
            "mode": self.mode,
            "protocol": self.protocol,
            "transport": self.transport,
            "discovery": self.discovery,
            "intentMode": self.intent_mode,
            "runMode": self.run_mode,
            "targetTaskId": self.target_task_id,
            "targetContextId": self.target_context_id,
            "modelMode": model_mode,
            "modelLabel": model_label,
            "expectedSequence": expected_sequence,
        }
        span = self._recorder.emit(
            seq=seq,
            phase_step=step,
            kind="run_started",
            phase=PHASE_DISPATCH,
            title="Run started",
            actor="system",
            side="client",
            direction="internal",
            detail=detail,
        )
        self._store_span(span, seq=seq, record=True, broadcast_frame=False)
        message: dict[str, object] = {
            "type": "run_started",
            "runId": self.run_id,
            "scenario": self.scenario,
            "description": description,
            "userRequest": user_request,
            "language": language,
            "mode": self.mode,
            "protocol": self.protocol,
            "transport": self.transport,
            "discovery": self.discovery,
            "intentMode": self.intent_mode,
            "runMode": self.run_mode,
            "targetTaskId": self.target_task_id,
            "targetContextId": self.target_context_id,
            "modelMode": model_mode,
            "modelLabel": model_label,
            "expectedSequence": expected_sequence,
            "langfuse": self.langfuse,
            "phase": PHASE_DISPATCH,
            "phaseStep": step,
            "spanId": span.span_id,
            "startedAt": span.started_at,
        }
        return self.broadcast(message, seq=seq)

    def emit_trace(
        self,
        *,
        kind: str,
        phase: str,
        title: str,
        direction: str = "internal",
        actor: str = "system",
        side: str = "client",
        status: str = "ok",
        round_number: int | None = None,
        context_id: str | None = None,
        task_id: str | None = None,
        detail: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Create, store and broadcast one local synthetic trace span."""
        with self._trace_lock:
            seq, step = self.allocate(phase)
            span = self._recorder.emit(
                seq=seq,
                phase_step=step,
                kind=kind,
                phase=phase,
                title=title,
                actor=actor,
                side=side,
                direction=direction,
                status=status,
                round=round_number,
                context_id=context_id,
                task_id=task_id,
                detail=detail,
            )
            return self._store_span(span, seq=seq, record=True)

    def record_trace(self, span: Mapping[str, object]) -> dict[str, object]:
        """Thread-safe entry point of :meth:`_record_trace`."""
        with self._trace_lock:
            return self._record_trace(span)

    def _record_trace(self, span: Mapping[str, object]) -> dict[str, object]:
        """Ingest one producer span (client flow or server HTTP transport) and broadcast it.

        A producer id that is empty or already taken by another span is remapped to a fresh UI id;
        the parent reference follows the same mapping so nested spans stay connected.

        A **re-sent** producer span (same original ``spanId``) is ignored: the first ingested copy
        wins and the stored span is returned unchanged, so a retried server batch never duplicates
        a timeline row. The server emitter retries whole batches, and ids are role-scoped
        (``llm_server_*`` / ``tool_server_*``), so ignoring repeats is safe and idempotent.
        """
        candidate = Span.from_dict(span)
        original_span_id = candidate.span_id
        if original_span_id:
            existing_span_id = self._producer_spans.get(original_span_id)
            if existing_span_id is not None:
                return self.spans.get(existing_span_id, {})
        if candidate.kind == "intent_processed" and isinstance(candidate.detail, Mapping):
            self.record_intent(candidate.detail)
        phase = candidate.phase
        seq, step = self.allocate(phase)
        span_id: str | None = candidate.span_id or None
        if span_id is None or span_id in self.spans:
            if span_id is not None:
                span_id = self._recorder.new_span_id()
                self._span_id_map[candidate.span_id] = span_id
            else:
                span_id = None
        elif candidate.span_id:
            self._span_id_map.setdefault(candidate.span_id, candidate.span_id)
        parent_span_id = candidate.parent_span_id
        if parent_span_id is not None:
            parent_span_id = self._span_id_map.get(parent_span_id, parent_span_id)
        built = self._recorder.emit(
            seq=seq,
            phase_step=step,
            span_id=span_id,
            parent_span_id=parent_span_id,
            kind=candidate.kind,
            phase=phase,
            title=candidate.title,
            actor=candidate.actor,
            side=candidate.side,
            direction=candidate.direction,
            status=candidate.status,
            round=candidate.round,
            context_id=candidate.context_id,
            task_id=candidate.task_id,
            stage=candidate.stage,
            purpose=candidate.purpose,
            model=candidate.model,
            tokens=candidate.tokens,
            error=candidate.error,
            detail=candidate.detail,
            started_at=candidate.started_at,
            ended_at=candidate.ended_at,
            duration_ms=candidate.duration_ms,
        )
        self._producer_spans[original_span_id or built.span_id] = built.span_id
        return self._store_span(built, seq=seq, record=True)

    def _store_span(
        self,
        span: Span,
        *,
        seq: int,
        record: bool,
        broadcast_frame: bool = True,
    ) -> dict[str, object]:
        """Store one span for lookup/trace export, optionally add a transcript row and broadcast."""
        payload = span.to_dict()
        self.traces.append(payload)
        self.spans[span.span_id] = payload
        if record and self.transcript is not None:
            self.transcript.record_trace(payload, seq=seq, phase_step=span.phase_step)
        if broadcast_frame:
            self.broadcast({"type": "trace", "event": payload}, seq=seq)
        # Export from the single storage funnel: local synthetic spans (events, decisions,
        # approvals, run_started) and ingested producer spans (LLM/tool/client flow) all pass
        # here, and a re-sent producer span is filtered before it reaches this method.
        if self.langfuse_exporter is not None:
            self.langfuse_exporter(payload)
        return payload

    def record_event(self, event: Mapping[str, object]) -> dict[str, object]:
        """Record one normalized A2A event, its span and its SSE frame."""
        if self.transcript is None:
            return {}
        phase = infer_event_phase(event)
        seq, step = self.allocate(phase)
        detail = _pure_detail(event)
        self._note_downstream(detail)
        if isinstance(detail, Mapping) and isinstance(detail.get("intentProcessed"), Mapping):
            self.record_intent(detail["intentProcessed"])
        entry = self.transcript.record_event(
            event,
            phase=phase,
            phase_step=step,
            seq=seq,
            detail=detail,
        )
        round_number = entry.get("round")
        context_value = entry.get("contextId")
        span = self._recorder.emit(
            seq=seq,
            phase_step=step,
            kind=str(event.get("kind") or "event"),
            phase=phase,
            title=str(entry.get("summary") or event.get("kind") or "event"),
            actor="server",
            side="server",
            direction="server->client",
            round=round_number if isinstance(round_number, int) else None,
            context_id=context_value if isinstance(context_value, str) else None,
            task_id=str(event.get("task_id") or "") or None,
            detail={"event": dict(event)},
        )
        entry["spanId"] = span.span_id
        self._store_span(span, seq=seq, record=False, broadcast_frame=False)
        self.broadcast({"type": "event", "event": entry}, seq=seq)
        task_id = _string_value(event.get("task_id"))
        if task_id is not None:
            self.remote_task_id = task_id
        self._persist_task_snapshot(event)
        return entry

    def record_intent(self, detail: Mapping[str, object]) -> None:
        """Capture the business intent carried by one ``intent_processed`` trace row."""
        raw = _string_value(detail.get("rawRequest"))
        if raw is not None:
            self.raw_request = raw
        normalized = _string_value(detail.get("normalizedText"))
        if normalized is not None:
            self.normalized_request = normalized
        intent = detail.get("intent")
        if isinstance(intent, Mapping):
            self.processed_intent = {str(key): value for key, value in intent.items()}

    def _persist_task_snapshot(self, event: Mapping[str, object]) -> None:
        """Upsert the last observed Task state of this run."""
        if self.store is None:
            return
        task_id = _string_value(event.get("task_id"))
        if task_id is None:
            return
        self.persist(
            self.store.save_task_snapshot(
                TaskSnapshotRecord(
                    run_id=self.run_id,
                    task_id=task_id,
                    context_id=_string_value(event.get("context_id")),
                    state=_string_value(event.get("state")),
                    payload=dict(event),
                )
            )
        )

    def record_proposal(
        self,
        propose: NegotiationProposeView,
        session: NegotiationContext,
    ) -> None:
        """Persist one inbound proposal in the negotiation audit."""
        if self.store is None:
            return
        self.persist(
            self.store.record_negotiation_event(
                NegotiationEventRecord.new(
                    run_id=self.run_id,
                    seq=self.next_negotiation_seq(),
                    direction=NEGOTIATION_DIRECTION_INBOUND,
                    actor="server",
                    negotiation_id=propose.negotiation_id,
                    kind=propose.kind,
                    round=session.round,
                    performative=session.performative.value,
                    content={
                        "content": propose.content,
                        "templateUri": propose.template_uri,
                    },
                )
            )
        )

    def record_decision(
        self,
        decision: NegotiationDecision,
        propose: NegotiationProposeView | None,
        *,
        session: NegotiationContext | None = None,
    ) -> dict[str, object]:
        """Record one provider/human decision live and broadcast it with its span."""
        if self.transcript is None:
            return {}
        phase = infer_decision_phase(decision.action)
        seq, step = self.allocate(phase)
        entry = self.transcript.record_decision(
            decision,
            propose=propose,
            phase=phase,
            phase_step=step,
            seq=seq,
        )
        span = self._recorder.emit(
            seq=seq,
            phase_step=step,
            kind=decision.action,
            phase=phase,
            title=f"Decision · {decision.action}",
            actor="human" if self.mode in (MODE_MANUAL, MODE_CONFIRM) else "client",
            side="client",
            direction="internal",
            status="ok",
            round=session.round if session is not None else (propose.round if propose is not None else None),
            context_id=propose.context_id if propose is not None else None,
            detail={
                "action": decision.action,
                "rationale": decision.rationale,
                "filledParams": dict(decision.filled_params) if decision.filled_params else None,
                "counterProposal": decision.counter_proposal,
                "counterParams": dict(decision.counter_params) if decision.counter_params else None,
                "confirmedIntent": decision.confirmed_intent,
                "feasibilitySummary": decision.feasibility_summary,
                "confirmedBy": "engineer" if self.mode == MODE_CONFIRM else None,
            },
        )
        entry["spanId"] = span.span_id
        self._store_span(span, seq=seq, record=False, broadcast_frame=False)
        self.broadcast({"type": "event", "event": entry}, seq=seq)
        self._persist_decision(decision, propose, session)
        return entry

    def _persist_decision(
        self,
        decision: NegotiationDecision,
        propose: NegotiationProposeView | None,
        session: NegotiationContext | None,
    ) -> None:
        """Persist one outbound decision in the negotiation audit."""
        if self.store is None:
            return
        self.persist(
            self.store.record_negotiation_event(
                NegotiationEventRecord.new(
                    run_id=self.run_id,
                    seq=self.next_negotiation_seq(),
                    direction=NEGOTIATION_DIRECTION_OUTBOUND,
                    actor="human" if self.mode in (MODE_MANUAL, MODE_CONFIRM) else "client",
                    negotiation_id=(
                        propose.negotiation_id if propose is not None else (session.id if session is not None else None)
                    ),
                    kind=propose.kind if propose is not None else None,
                    round=(session.round if session is not None else (propose.round if propose is not None else None)),
                    performative=_performative_for_decision(decision.action),
                    content={
                        "action": decision.action,
                        "filledParams": (dict(decision.filled_params) if decision.filled_params else None),
                        "counterProposal": decision.counter_proposal,
                        "counterParams": (dict(decision.counter_params) if decision.counter_params else None),
                        "confirmedIntent": decision.confirmed_intent,
                        "feasibilitySummary": decision.feasibility_summary,
                    },
                    decision_action=decision.action,
                    rationale=decision.rationale,
                )
            )
        )

    def emit_finished(self) -> dict[str, object]:
        """Broadcast the terminal frame and record its ``done`` trace row."""
        seq, step = self.allocate(PHASE_DONE)
        span = self._recorder.emit(
            seq=seq,
            phase_step=step,
            kind="finished",
            phase=PHASE_DONE,
            title="Run finished",
            actor="system",
            side="client",
            direction="internal",
            status="error" if self.status == STATUS_FAILED else "ok",
            context_id=self.result.context_id if self.result is not None else None,
            task_id=self.result.task_id if self.result is not None else None,
            error=self.error,
            detail={
                "finalState": self.result.final_state if self.result is not None else None,
                "report": self.result.report if self.result is not None else None,
            },
        )
        self._store_span(span, seq=seq, record=True, broadcast_frame=False)
        message = self._finished_message()
        message.update(
            {
                "phase": PHASE_DONE,
                "phaseStep": step,
                "spanId": span.span_id,
                # The finished row starts when the terminal frame is built, which is what lets the
                # Done phase render a duration instead of a null one.
                "startedAt": span.started_at,
            }
        )
        self.finished_at = time.monotonic()
        return self.broadcast(message, seq=seq)

    def subscribe(self) -> tuple[list[dict[str, object]], asyncio.Queue[dict[str, object]]]:
        """Return the replay snapshot and the live queue of one new subscriber atomically."""
        snapshot = list(self.messages)
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self.subscribers.add(queue)
        return snapshot, queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, object]]) -> None:
        """Remove one SSE subscriber queue."""
        self.subscribers.discard(queue)

    def finished(self) -> bool:
        """Whether the run reached a terminal status."""
        return self.status != STATUS_RUNNING

    def _finished_message(self) -> dict[str, object]:
        """Build the terminal SSE message of this run."""
        return {
            "type": "finished",
            "runId": self.run_id,
            "conversationId": self.conversation_id,
            "status": self.status,
            "finalState": self.result.final_state if self.result is not None else None,
            "report": self.result.report if self.result is not None else None,
            "error": self.error,
            "transcriptJson": f"/api/runs/{self.run_id}/transcript.json",
            "transcriptMarkdown": f"/api/runs/{self.run_id}/transcript.md",
            "traceJson": f"/api/runs/{self.run_id}/trace.json",
            "langfuse": self.langfuse,
        }

    def _note_downstream(self, detail: Mapping[str, object] | None) -> None:
        """Remember the downstream Task identity the server reports for this run.

        The OSS Agent and the gateway both forward one request to another A2A server. That far-side
        Task/context is a different hop from the one this run owns, so it is recorded next to
        ``result.task_id``/``context_id`` instead of replacing them.
        """
        if not isinstance(detail, Mapping):
            return
        pure = detail.get("pure")
        if not isinstance(pure, Mapping):
            return
        task_id = _string_value(pure.get("downstreamTaskId"))
        context_id = _string_value(pure.get("downstreamContextId"))
        hop = _string_value(pure.get("downstreamHop"))
        if task_id:
            self.downstream_task_id = task_id
        if context_id:
            self.downstream_context_id = context_id
        if hop:
            self.downstream_hop = hop

    def snapshot(self) -> dict[str, object]:
        """Return the JSON status/result/transcript document of this run."""
        transcript = self.transcript.to_dict() if self.transcript is not None else None
        return {
            "runId": self.run_id,
            "conversationId": self.conversation_id,
            "traceId": self.trace_id,
            "externalConversationId": self.external_conversation_id,
            "userId": self.user_id,
            "idempotencyKey": self.idempotency_key,
            "scenario": self.scenario,
            "mode": self.mode,
            "protocol": self.protocol,
            "transport": self.transport,
            "discovery": self.discovery,
            "intentMode": self.intent_mode,
            "runMode": self.run_mode,
            "targetTaskId": self.target_task_id,
            "targetContextId": self.target_context_id,
            "rawRequest": self.raw_request,
            "normalizedRequest": self.normalized_request,
            "intent": self.processed_intent,
            "status": self.status,
            "detached": self.detached,
            "pendingDecision": self.pending_decision,
            "pendingIntent": self.pending_intent,
            # A recovered long-running run carries a synthetic incomplete seed result; only a
            # finished run exposes a final state to the page and the API.
            "finalState": (
                self.result.final_state
                if self.result is not None and self.status != STATUS_RUNNING
                else None
            ),
            "contextId": self.result.context_id if self.result is not None else None,
            "taskId": self.result.task_id if self.result is not None else None,
            "downstreamTaskId": self.downstream_task_id,
            "downstreamContextId": self.downstream_context_id,
            "downstreamHop": self.downstream_hop,
            "report": self.result.report if self.result is not None else None,
            "error": self.error,
            "transcriptError": self.transcript_error,
            "seq": self._seq,
            "traceCount": len(self.traces),
            "langfuse": self.langfuse,
            "source": dict(self.source),
            "transcript": transcript,
        }


def _pure_detail(event: Mapping[str, object]) -> dict[str, object] | None:
    """Return the pure-mode hop detail of one event, or ``None`` for A2A-T events."""
    metadata = event.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    pure = metadata.get("pure")
    hop = metadata.get("hop")
    tool_name = metadata.get("toolName")
    if not isinstance(pure, Mapping) and not isinstance(hop, str) and not isinstance(tool_name, str):
        return None
    detail: dict[str, object] = {}
    if isinstance(hop, str) and hop:
        detail["hop"] = hop
    if isinstance(pure, Mapping):
        detail["pure"] = dict(pure)
    if isinstance(tool_name, str) and tool_name:
        detail["toolName"] = tool_name
    intent = metadata.get("intentProcessed")
    if isinstance(intent, Mapping):
        detail["intentProcessed"] = {str(key): value for key, value in intent.items()}
    return detail or None


def _string_value(value: object) -> str | None:
    """Return a non-empty string value, ``None`` otherwise."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _performative_for_decision(action: str) -> str:
    """Map one decision action to its Negotiation-T performative name."""
    return {
        "accept": "ACCEPT",
        "counter": "PROPOSE",
        "reject": "REJECT",
        "abort": "ABORT",
    }.get(action, action.upper())
