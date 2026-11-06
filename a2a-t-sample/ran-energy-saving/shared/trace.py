"""Unified trace model of the RAN energy-saving sample (Run Explorer design §3).

The Web UI, the client flow and the LLM/tool producers share one span envelope so the timeline can
order protocol events, decisions and LLM calls with a single ``seq``. This module owns:

* the :class:`Span` dataclass and its camelCase wire form;
* the eight phase constants;
* :class:`TraceRecorder`, a thread-safe span factory that assigns ``seq``, per-phase ``phaseStep``
  and ``spanId`` and applies secret redaction plus payload truncation;
* :class:`TraceContext` + :func:`trace_scope` / :func:`trace_stage`, the ``ContextVar`` correlation
  used by the LLM/tool capture of the next task;
* :func:`set_global_sink` / :func:`emit_span`, the producer-side hook that is a no-op when the Web
  UI (or another consumer) has not attached a sink;
* :func:`record_span`, the shared LLM/tool producer helper, plus :func:`phase_for_stage`, the stage
  to phase mapping of design §5.3;
* :class:`HttpTraceEmitter`, the cross-process server-to-UI transport, and
  :class:`FileTraceEmitter`, the CLI server trace file writer.

Tracing is an observer: no helper here may change the protocol flow, and a failing sink is ignored.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from a2a_t.core.metadata import NEGOTIATION_CONTEXT_METADATA_KEY, TASK_T_EXTENSION_URI

#: Version of the span envelope written by this module.
SCHEMA_VERSION: Final[int] = 1

#: Maximum characters kept per captured payload (design §3: 256KB per request/response).
MAX_PAYLOAD_CHARS: Final[int] = 256 * 1024

#: Characters kept at each end of a truncated string.
_TRUNCATED_HEAD_CHARS: Final[int] = 8 * 1024
_TRUNCATED_TAIL_CHARS: Final[int] = 8 * 1024

#: Marker added to a sanitized detail map when any value was redacted or truncated.
TRUNCATED_MARKER: Final[str] = "_traceTruncated"

#: Metadata keys whose values are never recorded (design §7).
_REDACTED_KEYS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "api_key",
        "api-key",
        "apikey",
        "x-api-key",
        "x-auth-token",
        "access_token",
        "a2at_sample_trace_token",
        "webhook_token",
    }
)

#: The Run Explorer phases, in display order.
PHASE_DISPATCH: Final[str] = "dispatch"
PHASE_TASK_PROMPT: Final[str] = "task_prompt"
PHASE_TASK_VALIDATION: Final[str] = "task_validation"
#: Second Task-T of a two-phase run: the Modify follow-up sent on the existing Task.
PHASE_TASK_MODIFY: Final[str] = "task_modify"
PHASE_NEGOTIATION: Final[str] = "negotiation"
PHASE_RESOLUTION: Final[str] = "resolution"
PHASE_EXECUTION: Final[str] = "execution"
PHASE_REPORT: Final[str] = "report"
PHASE_DONE: Final[str] = "done"

#: All single-phase phases, in display order. ``PHASE_TASK_MODIFY`` is an optional phase of the
#: two-phase planned-outage run; it is not part of every run's phase set.
PHASES: Final[tuple[str, ...]] = (
    PHASE_DISPATCH,
    PHASE_TASK_PROMPT,
    PHASE_TASK_VALIDATION,
    PHASE_NEGOTIATION,
    PHASE_RESOLUTION,
    PHASE_EXECUTION,
    PHASE_REPORT,
    PHASE_DONE,
)

#: Sink signature shared by producers and the Web UI.
TraceSink = Callable[[dict[str, object]], None]


def _now_iso() -> str:
    """Return the current UTC time in ISO 8601 form."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _as_str(value: object, default: str = "") -> str:
    """Return one value as a string, ``default`` when absent."""
    return value if isinstance(value, str) else default


def _as_optional_str(value: object) -> str | None:
    """Return one value as an optional string."""
    return value if isinstance(value, str) and value else None


def _as_int(value: object, default: int = 0) -> int:
    """Return one value as an integer, ``default`` when absent or invalid."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _as_optional_number(value: object) -> float | None:
    """Return one value as an optional number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _truncate_text(value: str) -> tuple[str, bool]:
    """Keep the head and tail of one oversized string and mark the cut."""
    if len(value) <= MAX_PAYLOAD_CHARS:
        return value, False
    removed = len(value) - _TRUNCATED_HEAD_CHARS - _TRUNCATED_TAIL_CHARS
    return (
        value[:_TRUNCATED_HEAD_CHARS] + f"\n… [truncated {removed} characters] …\n" + value[-_TRUNCATED_TAIL_CHARS:],
        True,
    )


def _sanitize_value(value: object, truncated: list[bool]) -> object:
    """Recursively redact secrets and truncate oversized strings inside one payload."""
    if isinstance(value, Mapping):
        sanitized: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if key.lower() in _REDACTED_KEYS:
                sanitized[key] = "[redacted]"
                continue
            sanitized[key] = _sanitize_value(raw_value, truncated)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item, truncated) for item in value]
    if isinstance(value, str):
        text, was_truncated = _truncate_text(value)
        truncated[0] = truncated[0] or was_truncated
        return text
    if isinstance(value, bytes):
        text, was_truncated = _truncate_text(value.decode("utf-8", errors="replace"))
        truncated[0] = truncated[0] or was_truncated
        return text
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_value(str(value), truncated)


def sanitize_payload(payload: Mapping[str, object] | None) -> tuple[dict[str, object], bool]:
    """Redact and truncate one detail payload.

    Args:
        payload: Arbitrary JSON-ish mapping supplied by a producer.

    Returns:
        A sanitized copy and whether anything was redacted or truncated. The copy carries
        :data:`TRUNCATED_MARKER` when the flag is set.
    """
    truncated = [False]
    sanitized_value = _sanitize_value(dict(payload or {}), truncated)
    sanitized: dict[str, object]
    if isinstance(sanitized_value, dict):
        sanitized = {str(key): value for key, value in sanitized_value.items()}
    else:
        sanitized = {"value": sanitized_value}
    rendered = json.dumps(sanitized, ensure_ascii=False, default=str)
    if len(rendered) > MAX_PAYLOAD_CHARS:
        truncated[0] = True
        sanitized = {
            "truncated": True,
            "originalLength": len(rendered),
            "preview": rendered[:2048],
        }
    if truncated[0]:
        sanitized[TRUNCATED_MARKER] = True
    return sanitized, truncated[0]


@dataclass
class Span:
    """One trace span (the unified protocol/decision/LLM/tool envelope).

    Attributes:
        schema_version: Envelope version.
        run_id: Owning run.
        seq: Global ordering number assigned by the recorder or the Web UI.
        span_id: Stable span identifier.
        parent_span_id: Optional parent span for nested spans.
        context_id: A2A context id, when known.
        task_id: A2A task id, when known.
        phase: One of :data:`PHASES`.
        phase_step: Step number inside ``phase``.
        round: Negotiation round, when applicable.
        actor: ``client`` / ``server`` / ``llm`` / ``tool`` / ``human`` / ``system``.
        side: ``client`` / ``server`` / ``shared``.
        kind: Semantic event kind (``message_sent``, ``decision_made``, ``llm``, ...).
        stage: Capture stage label (``client_decision_agent``, ...).
        title: One-line timeline title.
        purpose: Why this step happened.
        status: ``ok`` / ``error`` / ``pending`` / ``skipped``.
        started_at: UTC start time.
        ended_at: UTC end time, when measured.
        duration_ms: Duration in milliseconds, when measured.
        direction: ``client->server`` / ``server->client`` / ``internal``.
        model: Model id of an LLM span.
        tokens: Token usage map of an LLM span.
        error: Error text of a failed span.
        detail: Sanitized payload (prompt, raw message, tool args, ...).
    """

    schema_version: int = SCHEMA_VERSION
    run_id: str = ""
    seq: int = 0
    span_id: str = ""
    parent_span_id: str | None = None
    context_id: str | None = None
    task_id: str | None = None
    phase: str = PHASE_DISPATCH
    phase_step: int = 0
    round: int | None = None
    actor: str = ""
    side: str = ""
    kind: str = ""
    stage: str | None = None
    title: str = ""
    purpose: str | None = None
    status: str = "ok"
    started_at: str = field(default_factory=_now_iso)
    ended_at: str | None = None
    duration_ms: float | None = None
    direction: str | None = None
    model: str | None = None
    tokens: dict[str, object] | None = None
    error: str | None = None
    detail: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Return the camelCase JSON envelope of this span."""
        return {
            "schemaVersion": self.schema_version,
            "runId": self.run_id,
            "seq": self.seq,
            "spanId": self.span_id,
            "parentSpanId": self.parent_span_id,
            "contextId": self.context_id,
            "taskId": self.task_id,
            "phase": self.phase,
            "phaseStep": self.phase_step,
            "round": self.round,
            "actor": self.actor,
            "side": self.side,
            "kind": self.kind,
            "stage": self.stage,
            "title": self.title,
            "purpose": self.purpose,
            "status": self.status,
            "startedAt": self.started_at,
            "endedAt": self.ended_at,
            "durationMs": self.duration_ms,
            "direction": self.direction,
            "model": self.model,
            "tokens": self.tokens,
            "error": self.error,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Span:
        """Build one span from an envelope mapping (used by the Web UI trace ingestion)."""
        detail = data.get("detail")
        tokens = data.get("tokens")
        return cls(
            schema_version=_as_int(data.get("schemaVersion"), SCHEMA_VERSION),
            run_id=_as_str(data.get("runId")),
            seq=_as_int(data.get("seq")),
            span_id=_as_str(data.get("spanId")),
            parent_span_id=_as_optional_str(data.get("parentSpanId")),
            context_id=_as_optional_str(data.get("contextId")),
            task_id=_as_optional_str(data.get("taskId")),
            phase=_as_str(data.get("phase"), PHASE_DISPATCH) or PHASE_DISPATCH,
            phase_step=_as_int(data.get("phaseStep")),
            round=_as_int(data["round"]) if data.get("round") is not None else None,
            actor=_as_str(data.get("actor")),
            side=_as_str(data.get("side")),
            kind=_as_str(data.get("kind")),
            stage=_as_optional_str(data.get("stage")),
            title=_as_str(data.get("title")),
            purpose=_as_optional_str(data.get("purpose")),
            status=_as_str(data.get("status"), "ok") or "ok",
            started_at=_as_str(data.get("startedAt"), _now_iso()) or _now_iso(),
            ended_at=_as_optional_str(data.get("endedAt")),
            duration_ms=_as_optional_number(data.get("durationMs")),
            direction=_as_optional_str(data.get("direction")),
            model=_as_optional_str(data.get("model")),
            tokens=dict(tokens) if isinstance(tokens, Mapping) else None,
            error=_as_optional_str(data.get("error")),
            detail=dict(detail) if isinstance(detail, Mapping) else {},
        )


#: Backwards-friendly alias: the brief calls the same envelope ``TraceEvent``.
TraceEvent = Span


class TraceRecorder:
    """Thread-safe span factory owning the ordering state of one run.

    ``emit`` assigns a global ``seq``, a per-``phase`` ``phaseStep`` and a unique ``spanId`` when the
    caller does not supply them. Every detail payload is redacted and truncated before it is stored.

    Attributes:
        run_id: Run id stamped on every span generated here.
        id_prefix: Namespace of generated span ids. Independent recorders (the UI and the client
            flow) must use distinct prefixes so their ``span_0001`` sequences never collide in the
            run-wide lookup map.
    """

    def __init__(self, run_id: str = "", *, id_prefix: str = "span") -> None:
        """Create one recorder for a run (``run_id`` may be empty in CLI mode)."""
        self.run_id = run_id
        self.id_prefix = id_prefix or "span"
        self._lock = threading.RLock()
        self._seq = 0
        self._span_counter = 0
        self._phase_steps: dict[str, int] = {}
        self._spans: dict[str, Span] = {}

    def new_span_id(self) -> str:
        """Return a fresh, unique span id."""
        with self._lock:
            return self._next_span_id()

    def _next_span_id(self) -> str:
        """Return the next unused generated span id (the lock must be held)."""
        while True:
            self._span_counter += 1
            candidate = f"{self.id_prefix}_{self._span_counter:04d}"
            if candidate not in self._spans:
                return candidate

    def emit(
        self,
        *,
        kind: str = "",
        phase: str = PHASE_DISPATCH,
        title: str = "",
        actor: str = "",
        side: str = "",
        direction: str | None = None,
        status: str = "ok",
        round: int | None = None,
        context_id: str | None = None,
        task_id: str | None = None,
        stage: str | None = None,
        purpose: str | None = None,
        model: str | None = None,
        tokens: Mapping[str, object] | None = None,
        error: str | None = None,
        detail: Mapping[str, object] | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        seq: int | None = None,
        phase_step: int | None = None,
        run_id: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        duration_ms: float | None = None,
    ) -> Span:
        """Create, store and return one span.

        Args:
            kind: Semantic kind of the span.
            phase: Phase constant the span belongs to.
            title: One-line timeline title.
            actor: Actor of the span.
            side: Side of the actor.
            direction: Direction of a protocol span.
            status: Status label.
            round: Negotiation round.
            context_id: A2A context id.
            task_id: A2A task id.
            stage: Capture stage label.
            purpose: Why the step happened.
            model: Model id of an LLM span.
            tokens: Token usage map.
            error: Error text.
            detail: Arbitrary payload (redacted/truncated).
            span_id: Explicit span id; generated when omitted.
            parent_span_id: Parent span id.
            seq: Explicit global sequence number; assigned when omitted.
            phase_step: Explicit per-phase step; assigned when omitted.
            run_id: Override of the recorder run id.
            started_at: Explicit start time.
            ended_at: Explicit end time.
            duration_ms: Explicit duration.

        Returns:
            The stored span.
        """
        sanitized_detail, _ = sanitize_payload(detail)
        safe_tokens = dict(tokens) if tokens is not None else None
        with self._lock:
            if seq is None:
                self._seq += 1
                resolved_seq = self._seq
            else:
                resolved_seq = max(0, int(seq))
                self._seq = max(self._seq, resolved_seq)
            if phase_step is None:
                step = self._phase_steps.get(phase, 0) + 1
                self._phase_steps[phase] = step
                resolved_step = step
            else:
                resolved_step = max(0, int(phase_step))
                self._phase_steps[phase] = max(self._phase_steps.get(phase, 0), resolved_step)
            resolved_span_id = span_id or self._next_span_id()
            span = Span(
                run_id=run_id if run_id is not None else self.run_id,
                seq=resolved_seq,
                span_id=resolved_span_id,
                parent_span_id=parent_span_id,
                context_id=context_id,
                task_id=task_id,
                phase=phase,
                phase_step=resolved_step,
                round=round,
                actor=actor,
                side=side,
                kind=kind,
                stage=stage,
                title=title,
                purpose=purpose,
                status=status,
                started_at=started_at or _now_iso(),
                ended_at=ended_at,
                duration_ms=duration_ms,
                direction=direction,
                model=model,
                tokens=safe_tokens,
                error=error,
                detail=sanitized_detail,
            )
            self._spans[resolved_span_id] = span
            return span

    def get(self, span_id: str) -> Span | None:
        """Return one recorded span by id, ``None`` when unknown."""
        with self._lock:
            return self._spans.get(span_id)

    @property
    def spans(self) -> list[Span]:
        """Every recorded span, ordered by ``seq``."""
        with self._lock:
            return sorted(self._spans.values(), key=lambda span: (span.seq, span.span_id))

    def to_dict(self) -> dict[str, object]:
        """Return the recorder document with every span in ``seq`` order."""
        return {
            "schemaVersion": SCHEMA_VERSION,
            "runId": self.run_id,
            "spans": [span.to_dict() for span in self.spans],
        }


@dataclass(frozen=True)
class TraceContext:
    """Correlation context carried across async boundaries (LLM/tool capture).

    Attributes:
        run_id: Current run id, when known.
        role: ``client`` / ``server``.
        context_id: A2A context id.
        task_id: A2A task id.
        stage: Explicit capture stage label.
    """

    run_id: str | None = None
    role: str | None = None
    context_id: str | None = None
    task_id: str | None = None
    stage: str | None = None


_CURRENT_TRACE: ContextVar[TraceContext] = ContextVar("a2at_sample_trace", default=TraceContext())


def current_trace() -> TraceContext:
    """Return the trace context of the current task."""
    return _CURRENT_TRACE.get()


@contextmanager
def trace_scope(
    *,
    run_id: str | None = None,
    role: str | None = None,
    context_id: str | None = None,
    task_id: str | None = None,
    stage: str | None = None,
) -> Generator[TraceContext, None, None]:
    """Temporarily merge trace correlation values into the current context.

    A value of ``None`` keeps the current value, so nested scopes can override a single field.
    """
    current = _CURRENT_TRACE.get()
    merged = TraceContext(
        run_id=run_id if run_id is not None else current.run_id,
        role=role if role is not None else current.role,
        context_id=context_id if context_id is not None else current.context_id,
        task_id=task_id if task_id is not None else current.task_id,
        stage=stage if stage is not None else current.stage,
    )
    token = _CURRENT_TRACE.set(merged)
    try:
        yield merged
    finally:
        _CURRENT_TRACE.reset(token)


@contextmanager
def trace_stage(
    stage: str,
    *,
    role: str | None = None,
    context_id: str | None = None,
    task_id: str | None = None,
) -> Generator[TraceContext, None, None]:
    """Temporarily label the current capture stage, keeping every other correlation value."""
    with trace_scope(stage=stage, role=role, context_id=context_id, task_id=task_id) as context:
        yield context


_GLOBAL_SINK: TraceSink | None = None
_SINK_LOCK = threading.Lock()

#: Sentinel meaning "this task has no sink override".
_UNSET_SINK: Final[object] = object()

#: Per-task sink override. The Web UI starts several runs on one loop; a process-wide sink would
#: send one run's spans to whichever run attached last, so each run scopes its own sink here.
_SINK_OVERRIDE: ContextVar[TraceSink | None | object] = ContextVar("a2at_trace_sink_override", default=_UNSET_SINK)


def set_global_sink(sink: TraceSink | None) -> None:
    """Attach (or clear) the process-wide sink used by :func:`emit_span`.

    The OSS/Web server attaches its trace emitter for the whole process. A sink failure never
    propagates into the producer.
    """
    global _GLOBAL_SINK
    with _SINK_LOCK:
        _GLOBAL_SINK = sink


def set_task_sink(sink: TraceSink | None) -> Token[TraceSink | None | object]:
    """Attach a task-local sink override and return its reset token.

    A concurrent run uses this so its spans never reach another run's sink. Restore the previous
    value with :func:`reset_task_sink` in a ``finally`` block.
    """
    return _SINK_OVERRIDE.set(sink)


def reset_task_sink(token: Token[TraceSink | None | object]) -> None:
    """Drop a task-local sink override installed by :func:`set_task_sink`."""
    _SINK_OVERRIDE.reset(token)


def emit_span(span: Span | Mapping[str, object]) -> None:
    """Forward one span to the task-local sink, else the process sink; no-op when neither is set."""
    override = _SINK_OVERRIDE.get()
    if override is _UNSET_SINK:
        with _SINK_LOCK:
            sink = _GLOBAL_SINK
    else:
        sink = override if override is None or callable(override) else None
    if sink is None:
        return
    payload = span.to_dict() if isinstance(span, Span) else dict(span)
    try:
        sink(payload)
    except Exception:  # noqa: BLE001 - tracing must never break the observed flow
        return


def is_report_artifact(event: Mapping[str, object]) -> bool:
    """Report whether one normalized artifact event is the final Task-T report.

    The ADK executor emits one artifact per tool call (tool arguments/results) before the final
    report. Only the report artifact belongs to the Report phase: it is either named ``report`` or
    stamped with the Task-T extension metadata by the server executor. Every other artifact is an
    intermediate execution artifact.

    This is the one predicate shared by the webhook transport's stop condition and the Run Explorer
    phase mapping, so both agree on which artifact ends the run.
    """
    if _as_str(event.get("kind")) != "artifact":
        return False
    name = _as_str(event.get("name"))
    if "report" in name.lower():
        return True
    metadata = event.get("metadata")
    return isinstance(metadata, Mapping) and TASK_T_EXTENSION_URI in metadata


def infer_event_phase(event: Mapping[str, object]) -> str:
    """Infer the Run Explorer phase of one normalized A2A event."""
    kind = _as_str(event.get("kind"))
    if kind == "artifact":
        # Per-tool artifacts (args/response) are execution steps; only the report ends in Report.
        return PHASE_REPORT if is_report_artifact(event) else PHASE_EXECUTION
    if kind == "status":
        if _as_str(event.get("state")) == "TASK_STATE_COMPLETED":
            return PHASE_REPORT
        return PHASE_EXECUTION
    if kind == "message":
        metadata = event.get("metadata")
        performative = ""
        if isinstance(metadata, Mapping):
            raw_context = metadata.get(NEGOTIATION_CONTEXT_METADATA_KEY)
            if isinstance(raw_context, Mapping):
                performative = _as_str(raw_context.get("performative")).upper()
        if performative in {"ABORT", "ACCEPT", "REJECT"}:
            return PHASE_RESOLUTION
        return PHASE_NEGOTIATION
    return PHASE_NEGOTIATION


def infer_decision_phase(action: str) -> str:
    """Infer the phase of one provider decision (counter stays in negotiation)."""
    return PHASE_NEGOTIATION if action == "counter" else PHASE_RESOLUTION


#: Capture stage prefix -> Run Explorer phase (design §5.3), most specific prefix first.
#:
#: Story view groups rows by these phases; Waterfall keeps the global ``seq`` order. The ordering
#: matters: the accept-time Task-T refill and its revalidation happen *after* the accept decision,
#: so they belong to Resolution even though they carry a ``task_t_`` prefix. The initial generation
#: and validation keep their own Task-T/Validation phases.
_STAGE_PHASE_PREFIXES: Final[tuple[tuple[str, str], ...]] = (
    ("task_t_accept_validation", PHASE_RESOLUTION),
    ("task_t_slot_extraction_refill", PHASE_RESOLUTION),
    ("task_t_modify_validation", PHASE_TASK_MODIFY),
    ("task_t_modify_extraction", PHASE_TASK_MODIFY),
    ("task_t_server_validation", PHASE_TASK_VALIDATION),
    ("content_validation", PHASE_TASK_VALIDATION),
    ("task_t_", PHASE_TASK_PROMPT),
    ("negotiation_accept", PHASE_RESOLUTION),
    ("negotiation_reject", PHASE_RESOLUTION),
    ("negotiation_abort", PHASE_RESOLUTION),
    ("negotiation_", PHASE_NEGOTIATION),
    ("client_decision_agent", PHASE_NEGOTIATION),
    ("server_execution_agent", PHASE_EXECUTION),
)


def phase_for_stage(stage: str | None, default: str = PHASE_NEGOTIATION) -> str:
    """Return the Run Explorer phase of one capture stage label.

    Story rows are grouped by phase; Waterfall rows keep the global ``seq`` order, so a stage only
    decides its card, never its chronological position. Accept-time Task-T work
    (``task_t_slot_extraction_refill`` / ``task_t_accept_validation``) therefore maps to Resolution:
    it runs after the accept decision, even though it renders Task-T content.

    Args:
        stage: Capture stage of the call (``task_t_slot_extraction``, ...), or ``None``.
        default: Phase returned when the stage carries no known prefix.
    """
    if not stage:
        return default
    for prefix, phase in _STAGE_PHASE_PREFIXES:
        if stage.startswith(prefix):
            return phase
    return default


_PRODUCER_COUNTERS: dict[str, int] = {}
_PRODUCER_LOCK = threading.Lock()


def producer_span_id(prefix: str) -> str:
    """Return a process-unique LLM/tool span id belonging to one producer namespace.

    The prefix must identify the producer role (``llm_client``, ``llm_server``, ``tool_server``,
    ...), so the ``<prefix>_0001`` sequences of two processes never collide once merged by the Web
    UI.
    """
    safe_prefix = "".join(character for character in prefix if character.isalnum() or character in "-_")
    resolved = safe_prefix or "span"
    with _PRODUCER_LOCK:
        counter = _PRODUCER_COUNTERS.get(resolved, 0) + 1
        _PRODUCER_COUNTERS[resolved] = counter
    return f"{resolved}_{counter:04d}"


def record_span(
    *,
    kind: str,
    phase: str,
    title: str,
    prefix: str,
    actor: str = "llm",
    side: str | None = None,
    stage: str | None = None,
    status: str = "ok",
    purpose: str | None = None,
    model: str | None = None,
    tokens: Mapping[str, object] | None = None,
    error: str | None = None,
    detail: Mapping[str, object] | None = None,
    parent_span_id: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    duration_ms: float | None = None,
) -> dict[str, object]:
    """Build one LLM/tool span from the current trace context and emit it to the global sink.

    The correlation values (run id, role, context id, task id) come from :func:`current_trace`;
    every producer shares this builder so the envelope never drifts between the SDK and ADK
    capture paths. The span is sanitized and forwarded through :func:`emit_span`, which is a no-op
    without a sink.

    Args:
        kind: Span kind (``llm`` / ``tool``).
        phase: Run Explorer phase of the call (see :func:`phase_for_stage`).
        title: One-line timeline title.
        prefix: Producer id namespace of the generated span id (see :func:`producer_span_id`).
        actor: Actor label (``llm`` / ``tool``).
        side: ``client`` / ``server``; defaults to the role of the current trace context.
        stage: Capture stage; defaults to the stage of the current trace context.
        status: ``ok`` / ``error``.
        purpose: Why the call happened.
        model: Model id of an LLM span.
        tokens: Token usage map (``None`` values for the scripted provider).
        error: Error text of a failed call.
        detail: Request/response payload (redacted and truncated).
        parent_span_id: Parent span, when known.
        started_at: Explicit start time.
        ended_at: Explicit end time.
        duration_ms: Measured duration.

    Returns:
        The emitted camelCase envelope (also returned when no sink is attached).
    """
    trace = current_trace()
    sanitized, _ = sanitize_payload(detail)
    span = Span(
        run_id=trace.run_id or "",
        seq=0,
        span_id=producer_span_id(prefix),
        parent_span_id=parent_span_id,
        context_id=trace.context_id,
        task_id=trace.task_id,
        phase=phase,
        phase_step=0,
        actor=actor,
        side=side if side is not None else (trace.role or ""),
        kind=kind,
        stage=stage if stage is not None else trace.stage,
        title=title,
        purpose=purpose,
        status=status,
        started_at=started_at or _now_iso(),
        ended_at=ended_at,
        duration_ms=duration_ms,
        direction="internal",
        model=model,
        tokens=dict(tokens) if tokens is not None else None,
        error=error,
        detail=sanitized,
    )
    payload = span.to_dict()
    emit_span(payload)
    return payload


def elapsed_ms(started: float) -> float:
    """Return the milliseconds elapsed since one ``time.perf_counter`` reading."""
    return round((time.perf_counter() - started) * 1000.0, 3)


def _safe_run_id(value: object) -> str:
    """Return a file-name-safe run id (also blocks path traversal)."""
    if not isinstance(value, str):
        return "unknown-run"
    safe = "".join(character for character in value if character.isalnum() or character in "-_")
    return safe or "unknown-run"


class FileTraceEmitter:
    """Append server spans to ``<directory>/<runId>/server-trace.jsonl`` (CLI mode).

    The run id is read from the span itself (the server learns it from the ``sampleRunId`` message
    metadata), so one emitter serves every run of a process. A write failure is logged and dropped:
    tracing never breaks the observed flow.
    """

    def __init__(self, directory: Path | str, *, logger: logging.Logger | None = None) -> None:
        """Create one file emitter rooted at ``directory``."""
        self._directory = Path(directory)
        self._logger = logger or logging.getLogger(__name__)
        self._lock = threading.Lock()
        self._closed = False

    @property
    def directory(self) -> Path:
        """Root directory of the per-run trace files."""
        return self._directory

    def __call__(self, span: dict[str, object]) -> None:
        """Append one span to the run file; failures are logged, never raised."""
        run_id = _safe_run_id(span.get("runId"))
        sanitized, _ = sanitize_payload(span)
        with self._lock:
            if self._closed:
                return
            try:
                path = self._directory / run_id / "server-trace.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(sanitized, ensure_ascii=False) + "\n")
            except OSError as exc:
                self._logger.warning("trace file write failed: %s", exc)

    def close(self) -> None:
        """Stop accepting spans."""
        with self._lock:
            self._closed = True


#: Default number of spans per HTTP batch.
DEFAULT_TRACE_BATCH_SIZE: Final[int] = 20

#: Default seconds to wait before flushing a partial batch.
DEFAULT_TRACE_FLUSH_INTERVAL: Final[float] = 0.5

#: Default HTTP timeout of one trace POST.
DEFAULT_TRACE_TIMEOUT: Final[float] = 2.0

#: Default number of POST attempts before a batch is dropped.
DEFAULT_TRACE_MAX_ATTEMPTS: Final[int] = 2

#: POST signature of :class:`HttpTraceEmitter` (URL, JSON body, headers); may raise on failure.
TracePoster = Callable[[str, object, Mapping[str, str]], object]


def _default_trace_post(url: str, body: object, headers: Mapping[str, str]) -> object:
    """POST one trace batch with ``httpx`` (imported lazily to keep the module import cheap).

    ``trust_env=False`` is deliberate: spans carry prompts, tool arguments and results, so a
    corporate ``HTTP(S)_PROXY`` must never receive them. Every other sample HTTP client is local-only
    for the same reason.
    """
    import httpx

    response = httpx.post(
        url,
        json=body,
        headers=dict(headers),
        timeout=DEFAULT_TRACE_TIMEOUT,
        trust_env=False,
    )
    response.raise_for_status()
    return response.status_code


class HttpTraceEmitter:
    """Cross-process server-to-Web-UI trace transport (design §4).

    Spans are queued and POSTed in batches by one background daemon thread to
    ``POST {base_url}/api/runs/{runId}/trace`` with the local ``x-a2at-trace-token`` header. A
    failing batch is retried a bounded number of times and then logged and dropped; the producer
    is never blocked by the network.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        batch_size: int = DEFAULT_TRACE_BATCH_SIZE,
        flush_interval: float = DEFAULT_TRACE_FLUSH_INTERVAL,
        max_attempts: int = DEFAULT_TRACE_MAX_ATTEMPTS,
        logger: logging.Logger | None = None,
        post: TracePoster | None = None,
    ) -> None:
        """Create and start one emitter.

        Args:
            base_url: Web UI base URL (``http://127.0.0.1:8080``).
            token: Local trace token check by the UI.
            batch_size: Spans per POST.
            flush_interval: Seconds to wait for a full batch before flushing.
            max_attempts: POST attempts before a batch is dropped.
            logger: Optional logger; defaults to the module logger.
            post: Optional POST function (tests inject a fake).
        """
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._batch_size = max(1, batch_size)
        self._flush_interval = max(0.01, flush_interval)
        self._max_attempts = max(1, max_attempts)
        self._logger = logger or logging.getLogger(__name__)
        self._post = post or _default_trace_post
        self._queue: queue.Queue[dict[str, object] | None] = queue.Queue()
        self._stop = threading.Event()
        self._closed = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="a2at-trace-emitter", daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        """Web UI base URL this emitter posts to."""
        return self._base_url

    def __call__(self, span: dict[str, object]) -> None:
        """Queue one span for delivery; never blocks and never raises."""
        if self._closed:
            return
        self._queue.put(dict(span))

    def _run(self) -> None:
        """Collect spans into batches and flush them until :meth:`close`."""
        pending: list[dict[str, object]] = []
        while True:
            try:
                item = self._queue.get(timeout=self._flush_interval)
            except queue.Empty:
                self._flush(pending)
                pending = []
                if self._stop.is_set():
                    break
                continue
            if item is None:
                self._flush(pending)
                break
            pending.append(item)
            if len(pending) >= self._batch_size:
                self._flush(pending)
                pending = []

    def _flush(self, spans: list[dict[str, object]]) -> None:
        """Group one batch by run id and POST each group."""
        if not spans:
            return
        batches: dict[str, list[dict[str, object]]] = {}
        for span in spans:
            run_id = span.get("runId")
            if not isinstance(run_id, str) or not run_id:
                self._logger.warning("trace span without a run id dropped (kind=%s)", span.get("kind"))
                continue
            batches.setdefault(_safe_run_id(run_id), []).append(sanitize_payload(span)[0])
        headers = {"x-a2at-trace-token": self._token, "content-type": "application/json"}
        for run_id, group in batches.items():
            url = f"{self._base_url}/api/runs/{run_id}/trace"
            for attempt in range(1, self._max_attempts + 1):
                try:
                    self._post(url, group, headers)
                    break
                except Exception as exc:  # noqa: BLE001 - tracing never breaks the producer
                    if attempt >= self._max_attempts:
                        self._logger.warning(
                            "trace POST failed after %s attempt(s); %s span(s) dropped: %s",
                            attempt,
                            len(group),
                            exc,
                        )

    def close(self, timeout: float = 2.0) -> None:
        """Flush the queue and stop the background thread."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._stop.set()
        self._queue.put(None)
        self._thread.join(timeout=timeout)


__all__ = [
    "DEFAULT_TRACE_BATCH_SIZE",
    "DEFAULT_TRACE_FLUSH_INTERVAL",
    "DEFAULT_TRACE_MAX_ATTEMPTS",
    "DEFAULT_TRACE_TIMEOUT",
    "MAX_PAYLOAD_CHARS",
    "PHASES",
    "PHASE_DISPATCH",
    "PHASE_DONE",
    "PHASE_EXECUTION",
    "PHASE_NEGOTIATION",
    "PHASE_REPORT",
    "PHASE_RESOLUTION",
    "PHASE_TASK_MODIFY",
    "PHASE_TASK_PROMPT",
    "PHASE_TASK_VALIDATION",
    "SCHEMA_VERSION",
    "TRUNCATED_MARKER",
    "FileTraceEmitter",
    "HttpTraceEmitter",
    "Span",
    "TraceContext",
    "TraceEvent",
    "TracePoster",
    "TraceRecorder",
    "TraceSink",
    "current_trace",
    "elapsed_ms",
    "emit_span",
    "reset_task_sink",
    "set_task_sink",
    "infer_decision_phase",
    "infer_event_phase",
    "is_report_artifact",
    "phase_for_stage",
    "producer_span_id",
    "record_span",
    "sanitize_payload",
    "set_global_sink",
    "trace_scope",
    "trace_stage",
]
