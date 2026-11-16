"""Presence-only key-data judgement for the acceptance review console.

The trace axis no longer asks "did every span arrive"; it asks whether the exported Langfuse
trace carries the data a customer acceptance needs. Each data item is resolved from one or more
observations in the same trace, and **any** copy is enough. Copy content is deliberately not
compared here: both copies are written by the same exporter, so a mismatch would be an exporter
bug, not an acceptance verdict.

The module performs no I/O and imports no Web UI code. Callers hand it the normalized observation
mappings that ``demo/web/review/traces.py`` builds, so ``eval.osp.review`` stays independent of
``demo.web.review`` (which imports ``eval.osp.review`` during module initialization).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

PROTOCOL_A2A_T: Final[str] = "a2a-t"
PROTOCOL_A2A: Final[str] = "a2a"

STATUS_PRESENT: Final[str] = "present"
STATUS_MISSING: Final[str] = "missing"
STATUS_NOT_APPLICABLE: Final[str] = "n/a"

#: One key-data row, in the order the table shows it.
DATA_POINT_IDS: Final[tuple[str, ...]] = (
    "request.original",
    "request.dispatched",
    "slot_extraction",
    "task_t",
    "negotiation.message",
    "negotiation.reply",
    "task.succeeded",
)

_DECISION_KINDS: Final[frozenset[str]] = frozenset({"accept", "counter", "reject", "abort"})
_REPLY_PERFORMATIVES: Final[frozenset[str]] = frozenset({"ACCEPT", "COUNTER", "PROPOSE", "REJECT", "ABORT"})
_SLOT_STAGES: Final[frozenset[str]] = frozenset({"task_t_slot_extraction", "task_t_modify_extraction"})
_SUCCESS_STATES: Final[frozenset[str]] = frozenset(
    {"TASK_STATE_SUBMITTED", "TASK_STATE_WORKING", "TASK_STATE_COMPLETED"}
)
_SUCCESS_FINAL_STATES: Final[frozenset[str]] = frozenset(
    {"task_completed", "task_modified", "task_submitted", "task_working"}
)
_ROOT_SPAN_PREFIXES: Final[tuple[str, ...]] = ("run ·", "run opened")

_EXCERPT_LENGTH: Final[int] = 240

_LABELS: Final[Mapping[str, str]] = {
    "request.original": "原始请求",
    "request.dispatched": "实际下发请求",
    "slot_extraction": "槽位抽取响应",
    "task_t": "Task-T 报文",
    "negotiation.message": "协商报文",
    "negotiation.reply": "协商回复",
    "task.succeeded": "任务创建/修改成功报文",
}


@dataclass(frozen=True)
class DataContext:
    """Run facts that decide which data items apply and whether they are required."""

    protocol: str
    run_mode: str = "create"
    blocked: bool = False
    followup: bool = False
    rounds: int = 0
    accepted: bool = True
    run_id: str | None = None


@dataclass(frozen=True)
class DataCopy:
    """One Langfuse observation (or trace root) that carries a data item."""

    source: str
    span: str = ""
    run_id: str = ""
    side: str = ""
    round: int | None = None
    fields: tuple[str, ...] = ()
    excerpt: str = ""
    value: str = ""

    def to_dict(self, *, include_value: bool = False) -> dict[str, object]:
        """Return the JSON-friendly form; the drawer asks for ``value`` explicitly."""
        payload: dict[str, object] = {
            "source": self.source,
            "span": self.span,
            "runId": self.run_id,
            "side": self.side,
            "round": self.round,
            "fields": list(self.fields),
        }
        if self.excerpt:
            payload["excerpt"] = self.excerpt
        if include_value and self.value:
            payload["value"] = self.value
        return payload


@dataclass(frozen=True)
class DataPoint:
    """One expected data item and whether the run should carry it."""

    id: str
    required: bool = False
    applicable: bool = True

    @property
    def label(self) -> str:
        """Return the Chinese label the page shows."""
        return _LABELS.get(self.id, self.id)


@dataclass(frozen=True)
class DataPointResult:
    """Presence of one data item, with every Langfuse copy that satisfied it."""

    point: DataPoint
    status: str
    copies: tuple[DataCopy, ...] = ()

    @property
    def present(self) -> bool:
        """Return whether at least one copy carried the data."""
        return self.status == STATUS_PRESENT

    def to_dict(self, *, include_values: bool = False) -> dict[str, object]:
        """Return the compact export form used by the page and the customer pack."""
        return {
            "id": self.point.id,
            "label": self.point.label,
            "required": self.point.required,
            "applicable": self.point.applicable,
            "status": self.status,
            "present": self.present,
            "copies": [copy.to_dict(include_value=include_values) for copy in self.copies],
        }


def data_points(context: DataContext) -> tuple[DataPoint, ...]:
    """Return the data items of one protocol and run mode.

    Items that do not apply stay in the tuple with ``applicable=False`` so the page can render a
    stable column set (``—``) instead of dropping a column per protocol.
    """
    if context.protocol == PROTOCOL_A2A:
        return (
            DataPoint("request.original", True),
            DataPoint("request.dispatched", True, False),
            DataPoint("slot_extraction", False, False),
            DataPoint("task_t", False, False),
            DataPoint("negotiation.message", True, context.rounds > 0),
            DataPoint("negotiation.reply", True, context.rounds > 0),
            DataPoint("task.succeeded", True, context.accepted),
        )
    blocked_without_followup = context.blocked and not context.followup
    return (
        DataPoint("request.original", True),
        DataPoint("request.dispatched", True, not blocked_without_followup),
        DataPoint("slot_extraction", False, True),
        DataPoint("task_t", not blocked_without_followup, True),
        DataPoint("negotiation.message", True, context.rounds > 0),
        DataPoint("negotiation.reply", True, context.rounds > 0),
        DataPoint("task.succeeded", True, context.accepted),
    )


def resolve_data_points(
    payload: Mapping[str, object],
    context: DataContext,
    *,
    include_values: bool = False,
) -> tuple[DataPointResult, ...]:
    """Return the presence of every data item of one run.

    Args:
        payload: The trace payload stored by the review console; ``observations`` is the only
            required key.
        context: Protocol, run mode and the run facts that decide applicability.
        include_values: Keep the full text of each copy. The page's round detail sets this; the
            batch-level projection leaves it off to keep the list payload small.
    """
    observations = _observations(payload)
    results: list[DataPointResult] = []
    for point in data_points(context):
        if not point.applicable:
            results.append(DataPointResult(point, STATUS_NOT_APPLICABLE))
            continue
        copies = _resolve_copies(point.id, observations, context)
        status = STATUS_PRESENT if copies else STATUS_MISSING
        kept = tuple(copy if include_values else _without_value(copy) for copy in copies)
        results.append(DataPointResult(point, status, kept))
    return tuple(results)


def missing_required(results: Sequence[DataPointResult]) -> tuple[DataPointResult, ...]:
    """Return the required items with no copy anywhere."""
    return tuple(result for result in results if result.point.required and result.status == STATUS_MISSING)


def missing_optional(results: Sequence[DataPointResult]) -> tuple[DataPointResult, ...]:
    """Return the optional items with no copy anywhere."""
    return tuple(result for result in results if not result.point.required and result.status == STATUS_MISSING)


def _resolve_copies(
    point_id: str,
    observations: Sequence[Mapping[str, object]],
    context: DataContext,
) -> list[DataCopy]:
    """Return every observation that carries one data item."""
    if point_id == "request.original":
        return _original_copies(observations, context)
    if point_id == "request.dispatched":
        return _dispatched_copies(observations, context)
    if point_id == "slot_extraction":
        return _slot_extraction_copies(observations, context)
    if point_id == "task_t":
        return _task_t_copies(observations, context)
    if point_id == "negotiation.message":
        return _negotiation_message_copies(observations)
    if point_id == "negotiation.reply":
        return _negotiation_reply_copies(observations)
    if point_id == "task.succeeded":
        return _task_succeeded_copies(observations, context)
    return []


def _original_copies(
    observations: Sequence[Mapping[str, object]],
    context: DataContext,
) -> list[DataCopy]:
    """Resolve the operator's original request.

    A blocked A2A-T case is recorded as two segments. The dispatched run is started with the
    replacement request, so every span of that run names the replacement; the blocked original
    exists only in the interception span replayed from the preflight. With a follow-up, the
    original column must therefore come from that span alone — taking the run's own request would
    silently report the replacement as the original. A blocked case without a follow-up runs the
    original itself, so its trace is a valid carrier. Pure A2A has no Task-T validation at all.
    """
    if context.blocked and context.protocol != PROTOCOL_A2A:
        copies = _copies(
            observations,
            predicate=lambda item: _kind(item) == "task_validation"
            and _flag(item, "interception")
            and bool(_text(item, "user_request", "userRequest")),
            source="task_validation",
            fields=("userRequest",),
            value=lambda item: _text(item, "user_request", "userRequest"),
        )
        if copies:
            return copies
        return [] if context.followup else _trace_request_copies(observations, context)
    copies = _run_then_trace_copies(
        observations,
        context,
        predicate=lambda item: _kind(item) == "task_prompt_rendered"
        and bool(_text(item, "user_request", "userRequest")),
        source="task_prompt_rendered",
        fields=("userRequest",),
        value=lambda item: _text(item, "user_request", "userRequest"),
    )
    if copies:
        return copies
    return _trace_request_copies(observations, context)


def _dispatched_copies(
    observations: Sequence[Mapping[str, object]],
    context: DataContext,
) -> list[DataCopy]:
    """Resolve the request the run actually dispatched (the follow-up when blocked)."""
    copies = _run_then_trace_copies(
        observations,
        context,
        predicate=lambda item: _kind(item) == "task_prompt_rendered"
        and bool(_text(item, "user_request", "userRequest")),
        source="task_prompt_rendered",
        fields=("userRequest",),
        value=lambda item: _text(item, "user_request", "userRequest"),
    )
    if copies:
        return copies
    copies = _run_then_trace_copies(
        observations,
        context,
        predicate=lambda item: _kind(item) == "message_sent"
        and _performative(item) == "TASK_T"
        and bool(_text(item, "summary")),
        source="message_sent",
        fields=("summary",),
        value=lambda item: _text(item, "summary"),
    )
    if copies:
        return copies
    return _trace_request_copies(observations, context)


def _slot_extraction_copies(
    observations: Sequence[Mapping[str, object]],
    context: DataContext,
) -> list[DataCopy]:
    """Resolve the LLM slot-extraction response (A2A-T only)."""
    return _run_then_trace_copies(
        observations,
        context,
        predicate=lambda item: _kind(item) == "llm"
        and _text(item, "stage") in _SLOT_STAGES
        and bool(_text(item, "response", "output")),
        source="llm",
        fields=("output",),
        value=lambda item: _text(item, "response", "output"),
    )


def _task_t_copies(
    observations: Sequence[Mapping[str, object]],
    context: DataContext,
) -> list[DataCopy]:
    """Resolve the Task-T: the rendered prompt or any Task-T send frame."""
    rendered = _run_then_trace_copies(
        observations,
        context,
        predicate=lambda item: _kind(item) == "task_prompt_rendered"
        and bool(_text(item, "prompt_text", "promptText")),
        source="task_prompt_rendered",
        fields=("promptText",),
        value=lambda item: _text(item, "prompt_text", "promptText"),
    )
    sent = _run_then_trace_copies(
        observations,
        context,
        predicate=lambda item: _kind(item) == "message_sent"
        and _performative(item) == "TASK_T"
        and bool(_text(item, "task_t", "taskT")),
        source="message_sent",
        fields=("taskT",),
        value=lambda item: _text(item, "task_t", "taskT"),
    )
    return [*rendered, *sent]


def _negotiation_message_copies(
    observations: Sequence[Mapping[str, object]],
) -> list[DataCopy]:
    """Resolve the downstream proposal the client observed."""
    proposed = _copies(
        observations,
        predicate=lambda item: _kind(item) == "propose" and bool(_text(item, "content")),
        source="propose",
        fields=("content",),
        value=lambda item: _text(item, "content"),
    )
    requested = _copies(
        observations,
        predicate=lambda item: _kind(item) == "decision_requested" and bool(_text(item, "content")),
        source="decision_requested",
        fields=("content",),
        value=lambda item: _text(item, "content"),
    )
    return [*proposed, *requested]


def _negotiation_reply_copies(
    observations: Sequence[Mapping[str, object]],
) -> list[DataCopy]:
    """Resolve the client's negotiation reply: the decision or the frame it sent."""
    decisions = _copies(
        observations,
        predicate=lambda item: _kind(item) in _DECISION_KINDS,
        source="decision",
        fields=("action",),
        value=lambda item: _text(item, "content") or _text(item, "summary") or _kind(item).upper(),
    )
    sent = _copies(
        observations,
        predicate=lambda item: _kind(item) == "message_sent"
        and _performative(item) in _REPLY_PERFORMATIVES,
        source="message_sent",
        fields=("negotiationT", "performative"),
        value=lambda item: _text(item, "negotiation_t", "negotiationT") or _performative(item),
    )
    return [*decisions, *sent]


def _task_succeeded_copies(
    observations: Sequence[Mapping[str, object]],
    context: DataContext,
) -> list[DataCopy]:
    """Resolve the Task creation/modification success frame.

    ``task_created`` is the acceptance carrier. Real NMS can accept a Modify as WORKING without a
    SUBMITTED frame, so a terminal ``finished`` span carrying the Task identity is also accepted.
    A full-level ``status`` span is the final fallback, but a Modify must show its own acceptance
    sentence (``Task modification succeeded`` / ``taskModifyStatus=SUCCESSFUL``); a bare WORKING
    status is not proof of a successful modification.
    """
    created = _copies(
        observations,
        predicate=lambda item: _kind(item) == "task_created",
        source="task_created",
        fields=("taskId", "contextId", "state", "operation"),
        value=_task_identity_text,
    )
    if created:
        return created
    finished = _copies(
        observations,
        predicate=lambda item: _kind(item) == "finished"
        and bool(_text(item, "task_id", "taskId") or _text(item, "context_id", "contextId"))
        and _finished_is_success(item),
        source="finished",
        fields=("taskId", "contextId", "finalState"),
        value=_task_identity_text,
    )
    if finished:
        return finished
    return _copies(
        observations,
        predicate=lambda item: _kind(item) == "status"
        and bool(_text(item, "task_id", "taskId") or _text(item, "context_id", "contextId"))
        and _status_proves_success(item, context),
        source="status",
        fields=("taskId", "contextId", "state", "modifyStatus"),
        value=_task_identity_text,
    )


def _run_then_trace_copies(
    observations: Sequence[Mapping[str, object]],
    context: DataContext,
    *,
    predicate: Callable[[Mapping[str, object]], bool],
    source: str,
    fields: tuple[str, ...],
    value: Callable[[Mapping[str, object]], str],
) -> list[DataCopy]:
    """Return the run's own copies, falling back to the whole exported trace.

    The snapshot is already the selected run's evidence, but a hop's recorded ``runId`` can
    differ from the review row's (gateway/OSS/agent, a correction chain, an older tester). A
    strict equality there reported exported data as missing. The run's own copy is still listed
    first; the trace-wide copy only answers presence.
    """
    own = [item for item in observations if _matches_run(item, context.run_id)]
    copies = _copies(own, predicate=predicate, source=source, fields=fields, value=value)
    if copies:
        return copies
    return _copies(observations, predicate=predicate, source=source, fields=fields, value=value)


def _trace_request_copies(
    observations: Sequence[Mapping[str, object]],
    context: DataContext,
) -> list[DataCopy]:
    """Resolve the operator request from any observation carrying ``userRequest``.

    The run root (``run · …``) and its anchors (``run opened`` / ``trace opened``) carry the
    request as trace metadata, which the export surfaces as custom metadata. Older emitters and
    other hops may name the span differently, so presence is decided by the field, not the name.
    Root-looking spans are listed first.
    """
    copies = _run_then_trace_copies(
        observations,
        context,
        predicate=lambda item: bool(_text(item, "user_request", "userRequest")),
        source="trace_root",
        fields=("userRequest",),
        value=lambda item: _text(item, "user_request", "userRequest"),
    )
    return sorted(copies, key=lambda copy: 0 if copy.span.startswith(_ROOT_SPAN_PREFIXES) else 1)


def _copies(
    observations: Sequence[Mapping[str, object]],
    *,
    predicate: Callable[[Mapping[str, object]], bool],
    source: str,
    fields: tuple[str, ...],
    value: Callable[[Mapping[str, object]], str],
) -> list[DataCopy]:
    """Project every observation matching ``predicate`` onto one data copy."""
    copies: list[DataCopy] = []
    seen: set[tuple[str, str]] = set()
    for observation in observations:
        if not predicate(observation):
            continue
        text = value(observation)
        span = _name(observation)
        key = (_text(observation, "span_id", "spanId") or span, source)
        if key in seen:
            continue
        seen.add(key)
        copies.append(
            DataCopy(
                source=source,
                span=span,
                run_id=_run_id(observation),
                side=_text(observation, "side"),
                round=_round(observation),
                fields=fields,
                excerpt=text[:_EXCERPT_LENGTH],
                value=text,
            )
        )
    return copies


def _without_value(copy: DataCopy) -> DataCopy:
    """Drop the full text from a batch-level projection."""
    if not copy.value:
        return copy
    return DataCopy(
        source=copy.source,
        span=copy.span,
        run_id=copy.run_id,
        side=copy.side,
        round=copy.round,
        fields=copy.fields,
        excerpt=copy.excerpt,
        value="",
    )


def _status_proves_success(observation: Mapping[str, object], context: DataContext) -> bool:
    """Return whether a status frame is the recorded acceptance of this run.

    A Modify accepted by the vendor is reported as ``Task modification succeeded`` with
    ``taskModifyStatus=SUCCESSFUL`` while the Task stays WORKING. Accepting any WORKING frame
    would make an intermediate status look like a successful modification.
    """
    text = _text(observation, "status_text", "statusText").lower()
    modify_status = _text(observation, "modify_status", "modifyStatus").lower()
    if "task modification succeeded" in text or modify_status in {"successful", "success", "succeeded"}:
        return True
    if context.run_mode == "modify":
        return False
    return _text(observation, "state") in _SUCCESS_STATES


def _task_identity_text(observation: Mapping[str, object]) -> str:
    """Render the Task identity of a success copy as one readable line."""
    identity = {
        key: value
        for key in ("taskId", "contextId", "state", "operation", "finalState", "modifyStatus")
        if (value := _text(observation, _snake(key), key))
    }
    if identity:
        return json.dumps(identity, ensure_ascii=False)
    return _text(observation, "summary") or _text(observation, "status_text", "statusText")


def _finished_is_success(observation: Mapping[str, object]) -> bool:
    """Return whether a ``finished`` span describes an accepted/finished Task."""
    final_state = _text(observation, "final_state", "finalState")
    return not final_state or final_state in _SUCCESS_FINAL_STATES


def _observations(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    """Return the normalized observation rows of one trace payload."""
    value = payload.get("observations")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _text(observation: Mapping[str, object], *keys: str) -> str:
    """Return the first non-empty string field among ``keys``."""
    for key in keys:
        value = observation.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _snake(key: str) -> str:
    """Return the snake_case spelling of one camelCase field."""
    return "".join(f"_{char.lower()}" if char.isupper() else char for char in key)


def _name(observation: Mapping[str, object]) -> str:
    """Return the Langfuse observation name."""
    return _text(observation, "name")


def _kind(observation: Mapping[str, object]) -> str:
    """Return the span kind."""
    return _text(observation, "kind")


def _run_id(observation: Mapping[str, object]) -> str:
    """Return the run id of one observation."""
    return _text(observation, "run_id", "runId")


def _round(observation: Mapping[str, object]) -> int | None:
    """Return the negotiation round of one observation, when it carries one."""
    value = observation.get("round")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _performative(observation: Mapping[str, object]) -> str:
    """Return the normalized performative of one message span."""
    return _text(observation, "performative").upper()


def _flag(observation: Mapping[str, object], key: str) -> bool:
    """Return a boolean observation field, accepting a truthy string."""
    value = observation.get(key)
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}


def _matches_run(observation: Mapping[str, object], run_id: str | None) -> bool:
    """Return whether an observation belongs to the selected run.

    A missing run id on either side is accepted: old snapshots and trace-root rows do not always
    carry one, and refusing them would hide a copy that is present.
    """
    if not run_id:
        return True
    observed = _run_id(observation)
    return not observed or observed == run_id
