"""Structured negotiation transcript of the RAN energy-saving sample.

One :class:`NegotiationTranscript` records the normalized client events, the provider decisions and
the terminal outcome of a single run, and writes the result to ``runs/<timestamp>-<scenario>.json``
and ``.md`` (design §11). The transcript is an observer: it never parses private wire formats beyond
the public A2A-T metadata fields (``templateUri`` and ``negotiationContext``) and it never changes
the protocol flow.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from a2a_t.core.metadata import (
    NEGOTIATION_T_EXTENSION_URI,
    TEMPLATE_URI_METADATA_KEY,
    NegotiationPerformative,
)
from shared.logging_utils import summarize_text
from shared.models import NegotiationDecision, NegotiationProposeView
from shared.trace import PHASE_NEGOTIATION, infer_event_phase, is_report_artifact

from protocol.client_flow import ClientFlowResult
from protocol.messages import parse_negotiation_context
from protocol.negotiation_policy import negotiation_kind_for_template_uri

#: Direction of an inbound event (server to client).
DIRECTION_SERVER_TO_CLIENT: Final[str] = "server->client"

#: Direction of a decision or an outbound message (client to server).
DIRECTION_CLIENT_TO_SERVER: Final[str] = "client->server"

#: Default directory of the run transcripts, relative to the sample root.
DEFAULT_RUNS_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "runs"

#: Performative carried by each decision action on the wire.
PERFORMATIVE_BY_ACTION: Final[dict[str, str]] = {
    "accept": NegotiationPerformative.ACCEPT.value,
    "reject": NegotiationPerformative.REJECT.value,
    "abort": NegotiationPerformative.ABORT.value,
    "counter": NegotiationPerformative.PROPOSE.value,
}

#: Terminal state of a run that raised instead of finishing.
FINAL_STATE_FAILED: Final[str] = "failed"

#: Maximum summary length of one timeline row.
_SUMMARY_LENGTH: Final[int] = 160


def _now_iso() -> str:
    """Return the current UTC time in ISO 8601 form."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _run_stamp() -> str:
    """Return the compact UTC timestamp used in run file names."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _text(value: object) -> str:
    """Return one event field as stripped text, ``""`` when absent."""
    return value.strip() if isinstance(value, str) else ""


def _event_kind(kind: str, performative: str | None) -> str:
    """Return the semantic timeline kind (``propose``, ``status``, ``artifact``, ...).

    Only an A2A negotiation message takes its kind from the performative. Artifacts carry the
    agreeing negotiation context as audit metadata (the final report echoes the Accept), so an
    artifact stays an artifact instead of being mislabeled as an accept event.
    """
    if kind == "message" and performative:
        return performative.lower()
    if kind:
        return kind
    return "unknown"


def _negotiation_type(template_uri: object) -> str | None:
    """Return the negotiation type (``information``/``target``/``feasibility``) of a template URI."""
    if not isinstance(template_uri, str):
        return None
    kind = negotiation_kind_for_template_uri(template_uri)
    return kind.value if kind is not None else None


def _event_summary(
    event: Mapping[str, object],
    performative: str | None,
    negotiation_text: str = "",
) -> str:
    """Build a short human-readable summary of one normalized event."""
    if negotiation_text:
        return summarize_text(negotiation_text, max_length=_SUMMARY_LENGTH)
    text = _text(event.get("text"))
    if text:
        return summarize_text(text, max_length=_SUMMARY_LENGTH)
    if performative:
        return performative
    if event.get("kind") == "status":
        return _text(event.get("state")) or "status update"
    if event.get("kind") == "artifact":
        name = _text(event.get("name")) or _text(event.get("artifact_id")) or "artifact"
        data = event.get("data")
        if isinstance(data, Mapping) and data:
            return f"{name}: {summarize_text(json.dumps(dict(data), ensure_ascii=False), max_length=_SUMMARY_LENGTH)}"
        return name
    return _text(event.get("kind")) or "event"


@dataclass(frozen=True)
class TranscriptPaths:
    """Locations of the two files written for one run.

    Attributes:
        json_path: Absolute path of the JSON transcript.
        markdown_path: Absolute path of the Markdown transcript.
    """

    json_path: Path
    markdown_path: Path


@dataclass
class NegotiationTranscript:
    """In-memory transcript of one client run, writable as JSON and Markdown.

    Attributes:
        scenario: Scenario name of the run.
        language: Process-level language shown in the transcript.
        model_mode: ``live`` or ``offline``.
        mode: Client decision mode (``auto`` / ``manual`` / ``scripted``).
        transport: Event transport (``sse`` / ``webhook``).
        started_at: UTC start time (ISO 8601).
        finished_at: UTC end time (ISO 8601), set when the terminal outcome is recorded.
        events: Timeline entries in arrival order.
        decisions: Provider decisions in order.
        final_state: Terminal state of the run, when observed.
        context_id: A2A context id, when observed.
        task_id: Task id, when one was created.
        report: Report artifact payload of a completed task, when observed.
        error: Failure message of a run that raised.
        runs_dir: Directory the transcript is written to.
    """

    scenario: str
    language: str
    model_mode: str
    mode: str = "auto"
    transport: str = "sse"
    started_at: str = field(default_factory=_now_iso)
    finished_at: str | None = None
    events: list[dict[str, object]] = field(default_factory=list)
    decisions: list[dict[str, object]] = field(default_factory=list)
    final_state: str | None = None
    context_id: str | None = None
    task_id: str | None = None
    report: dict[str, object] | None = None
    error: str | None = None
    runs_dir: Path = DEFAULT_RUNS_DIR
    _seq: int = field(default=0, repr=False)

    def _next_seq(self, seq: int | None) -> int:
        """Return the explicit sequence number or allocate the next one."""
        if seq is None:
            self._seq += 1
            return self._seq
        self._seq = max(self._seq, seq)
        return seq

    def record_event(
        self,
        event: Mapping[str, object],
        *,
        direction: str = DIRECTION_SERVER_TO_CLIENT,
        phase: str | None = None,
        phase_step: int | None = None,
        seq: int | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        status: str | None = None,
        duration_ms: float | None = None,
        detail: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Record one normalized client event and return its timeline entry.

        The Run Explorer fields (``seq``, ``phase``, ``phaseStep``, ``spanId``, ...) are optional so
        CLI and older callers keep working; they are inferred/allocated when omitted.
        """
        metadata = event.get("metadata")
        metadata_map = dict(metadata) if isinstance(metadata, Mapping) else {}
        session = parse_negotiation_context(metadata_map)
        # Only a negotiation message has a performative; artifacts and status updates carry the
        # agreeing context as audit metadata but are not themselves messages of that performative.
        is_negotiation_message = _text(event.get("kind")) == "message"
        performative = (
            session.performative.value if is_negotiation_message and session is not None else None
        )
        template_uri = metadata_map.get(TEMPLATE_URI_METADATA_KEY)
        negotiation_text = _text(metadata_map.get(NEGOTIATION_T_EXTENSION_URI))
        explicit_detail = detail if detail is not None else event.get("detail")
        if not isinstance(explicit_detail, Mapping):
            explicit_detail = None
        if explicit_detail is None and _text(event.get("kind")) == "artifact":
            # The NMS stamps every tool artifact with the tool that produced it. A presentation
            # must not guess which artifact is the applied plan from its position: the planner may
            # read several profiles and evaluate several candidates before it applies one.
            tool_name = metadata_map.get("toolName")
            if isinstance(tool_name, str) and tool_name:
                explicit_detail = {"toolName": tool_name}
        resolved_phase = phase or infer_event_phase(event)
        resolved_step = phase_step if phase_step is not None else self._count_phase(resolved_phase)
        entry: dict[str, object] = {
            "ts": _now_iso(),
            "seq": self._next_seq(seq),
            "entryType": "event",
            "phase": resolved_phase,
            "phaseStep": resolved_step,
            "spanId": span_id,
            "parentSpanId": parent_span_id,
            "direction": direction,
            "kind": _event_kind(_text(event.get("kind")), performative),
            "eventKind": _text(event.get("kind")),
            # The Task state name. The human-readable text of a status is localized business
            # copy, so consumers must never infer the state from the text.
            "state": _text(event.get("state")) or None,
            "round": session.round if session is not None else None,
            "maxRounds": session.max_rounds if session is not None else None,
            "contextId": _text(event.get("context_id")) or (session.id if session is not None else None) or None,
            "templateUri": template_uri if isinstance(template_uri, str) else None,
            "negotiationType": _negotiation_type(template_uri),
            "performative": performative,
            "summary": _event_summary(event, performative, negotiation_text),
            # The full negotiation content (not the truncated summary), so an audience-facing view
            # can show what the NMS actually asked for without re-reading the raw message.
            "content": negotiation_text or None,
            "rationale": None,
            "status": status or "ok",
            "durationMs": duration_ms,
            "detail": dict(explicit_detail) if explicit_detail is not None else None,
        }
        if _text(event.get("kind")) == "artifact" and is_report_artifact(event):
            # The UI can render the report as soon as the artifact arrives instead of waiting for
            # the terminal frame, which live models can delay by many seconds.
            data = event.get("data")
            if isinstance(data, Mapping) and data:
                entry["data"] = dict(data)
        self.events.append(entry)
        self.context_id = self.context_id or (
            entry["contextId"] if isinstance(entry["contextId"], str) else None
        )
        task_id = _text(event.get("task_id"))
        if task_id:
            self.task_id = self.task_id or task_id
        return entry

    def record_trace(
        self,
        span: Mapping[str, object],
        *,
        seq: int | None = None,
        phase_step: int | None = None,
    ) -> dict[str, object]:
        """Record one trace span as a timeline entry (synthetic protocol/LLM/tool rows).

        The producer's ``phaseStep`` is deliberately ignored: producers keep their own counters, so
        trusting it would duplicate a step already allocated for a protocol event or decision.
        Callers that own the run-wide counter (the Web UI) pass ``phase_step`` explicitly.
        """
        phase = _text(span.get("phase")) or PHASE_NEGOTIATION
        resolved_step = phase_step if phase_step is not None else self._count_phase(phase)
        detail = span.get("detail")
        entry: dict[str, object] = {
            "ts": _text(span.get("startedAt")) or _now_iso(),
            "seq": self._next_seq(seq),
            "entryType": "trace",
            "phase": phase,
            "phaseStep": resolved_step,
            "spanId": _text(span.get("spanId")) or None,
            "parentSpanId": _text(span.get("parentSpanId")) or None,
            "direction": _text(span.get("direction")) or "internal",
            "kind": _text(span.get("kind")) or "trace",
            "eventKind": "trace",
            "round": span.get("round") if isinstance(span.get("round"), int) else None,
            "maxRounds": None,
            "contextId": _text(span.get("contextId")) or self.context_id,
            "templateUri": None,
            "negotiationType": None,
            "performative": None,
            "summary": _text(span.get("title")) or _text(span.get("kind")) or "trace",
            "rationale": _text(span.get("purpose")) or None,
            "status": _text(span.get("status")) or "ok",
            "durationMs": span.get("durationMs") if isinstance(span.get("durationMs"), (int, float)) else None,
            "detail": dict(detail) if isinstance(detail, Mapping) else None,
        }
        self.events.append(entry)
        context_id = entry["contextId"]
        if isinstance(context_id, str):
            self.context_id = self.context_id or context_id
        task_id = _text(span.get("taskId"))
        if task_id:
            self.task_id = self.task_id or task_id
        return entry

    def record_decision(
        self,
        decision: NegotiationDecision,
        *,
        propose: NegotiationProposeView | None = None,
        round_number: int | None = None,
        phase: str | None = None,
        phase_step: int | None = None,
        seq: int | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        status: str = "ok",
        duration_ms: float | None = None,
        detail: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Record one provider decision and return its timeline entry."""
        resolved_round = round_number if round_number is not None else (propose.round if propose else None)
        resolved_phase = phase or PHASE_NEGOTIATION
        resolved_step = phase_step if phase_step is not None else self._count_phase(resolved_phase)
        entry: dict[str, object] = {
            "ts": _now_iso(),
            "seq": self._next_seq(seq),
            "entryType": "decision",
            "phase": resolved_phase,
            "phaseStep": resolved_step,
            "spanId": span_id,
            "parentSpanId": parent_span_id,
            "direction": DIRECTION_CLIENT_TO_SERVER,
            "kind": decision.action,
            "eventKind": "decision",
            "round": resolved_round,
            "maxRounds": propose.max_rounds if propose is not None else None,
            "contextId": propose.context_id if propose is not None else self.context_id,
            "templateUri": propose.template_uri if propose is not None else None,
            "negotiationType": propose.kind if propose is not None else None,
            "performative": PERFORMATIVE_BY_ACTION.get(decision.action, decision.action.upper()),
            "summary": summarize_text(decision.rationale, max_length=_SUMMARY_LENGTH),
            "rationale": decision.rationale,
            "action": decision.action,
            "filledParams": dict(decision.filled_params) if decision.filled_params else None,
            "counterProposal": decision.counter_proposal,
            "counterParams": dict(decision.counter_params) if decision.counter_params else None,
            "confirmedIntent": decision.confirmed_intent,
            "feasibilitySummary": decision.feasibility_summary,
            "status": status,
            "durationMs": duration_ms,
            "detail": dict(detail) if detail is not None else None,
        }
        self.decisions.append(entry)
        return entry

    def _count_phase(self, phase: str) -> int:
        """Return the next step number of one phase in the recorded timeline."""
        return 1 + sum(
            1 for entry in (*self.events, *self.decisions) if entry.get("phase") == phase
        )

    def record_final(self, result: ClientFlowResult) -> None:
        """Record the terminal state, ids and report of one finished run."""
        self.final_state = result.final_state
        self.context_id = result.context_id or self.context_id
        self.task_id = result.task_id or self.task_id
        self.report = result.report
        self.finished_at = _now_iso()

    def record_error(self, message: str) -> None:
        """Record a run failure."""
        self.final_state = FINAL_STATE_FAILED
        self.error = message
        self.finished_at = _now_iso()

    def timeline(self) -> list[dict[str, object]]:
        """Return events, trace rows and decisions in one ``seq``-ordered timeline."""
        entries = [*self.events, *self.decisions]
        return sorted(entries, key=_entry_seq)

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-serializable transcript document."""
        return {
            "scenario": self.scenario,
            "language": self.language,
            "modelMode": self.model_mode,
            "mode": self.mode,
            "transport": self.transport,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "finalState": self.final_state,
            "contextId": self.context_id,
            "taskId": self.task_id,
            "report": self.report,
            "error": self.error,
            "events": list(self.events),
            "decisions": list(self.decisions),
            "timeline": self.timeline(),
        }

    def to_json(self) -> str:
        """Render the transcript as indented JSON (UTF-8 text)."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        """Render the transcript as a Markdown document."""
        lines: list[str] = [
            "# RAN energy-saving negotiation transcript",
            "",
            f"- Scenario: `{self.scenario}`",
            f"- Language: `{self.language}`",
            f"- Mode: `{self.mode}`",
            f"- Transport: `{self.transport}`",
            f"- Model mode: `{self.model_mode}`",
            f"- Started: `{self.started_at}`",
            f"- Finished: `{self.finished_at or 'n/a'}`",
            f"- Final state: `{self.final_state or 'n/a'}`",
            f"- Context id: `{self.context_id or 'n/a'}`",
            f"- Task id: `{self.task_id or 'n/a'}`",
        ]
        if self.error:
            lines.append(f"- Error: `{_markdown_cell(self.error)}`")
        lines.extend(
            [
                "",
                "## Timeline",
                "",
                "| # | Seq | Phase | Step | Round | Direction | Kind | Performative | Summary | Rationale |",
                "|---|-----|-------|------|-------|-----------|------|--------------|---------|-----------|",
            ]
        )
        for index, entry in enumerate(self.events, start=1):
            lines.append(_markdown_row(index, entry))
        lines.extend(["", "## Decisions", ""])
        if not self.decisions:
            lines.append("_No provider decisions were recorded._")
        else:
            lines.extend(
                [
                    "| # | Round | Action | Performative | Rationale |",
                    "|---|-------|--------|--------------|-----------|",
                ]
            )
            for index, entry in enumerate(self.decisions, start=1):
                lines.append(_markdown_decision_row(index, entry))
        lines.extend(["", "## Final report", "", "```json"])
        lines.append(json.dumps(self.report, ensure_ascii=False, indent=2) if self.report else "null")
        lines.extend(["```", ""])
        return "\n".join(lines)

    def write(self, runs_dir: Path | None = None) -> TranscriptPaths:
        """Write the JSON and Markdown transcripts and return their paths."""
        target_dir = runs_dir or self.runs_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{_run_stamp()}-{_safe_name(self.scenario)}"
        json_path = target_dir / f"{stem}.json"
        markdown_path = target_dir / f"{stem}.md"
        json_path.write_text(self.to_json(), encoding="utf-8")
        markdown_path.write_text(self.to_markdown(), encoding="utf-8")
        return TranscriptPaths(json_path=json_path, markdown_path=markdown_path)


def _entry_seq(entry: Mapping[str, object]) -> int:
    """Return the ``seq`` of one timeline entry, ``0`` when it has none."""
    value = entry.get("seq")
    return value if isinstance(value, int) else 0


def _safe_name(value: str) -> str:
    """Return a file-name-safe version of one scenario name."""
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value) or "run"


def _markdown_cell(value: object) -> str:
    """Render one value as a single Markdown table cell."""
    if value is None:
        return ""
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()


def _markdown_row(index: int, entry: Mapping[str, object]) -> str:
    """Render one event/trace entry as a row of the phase-aware timeline table."""
    return (
        "| {index} | {seq} | {phase} | {step} | {round} | {direction} | {kind} | {performative} | "
        "{summary} | {rationale} |"
    ).format(
        index=index,
        seq=_markdown_cell(entry.get("seq")),
        phase=_markdown_cell(entry.get("phase")),
        step=_markdown_cell(entry.get("phaseStep")),
        round=_markdown_cell(entry.get("round")),
        direction=_markdown_cell(entry.get("direction")),
        kind=_markdown_cell(entry.get("kind")),
        performative=_markdown_cell(entry.get("performative")),
        summary=_markdown_cell(entry.get("summary")),
        rationale=_markdown_cell(entry.get("rationale")),
    )


def _markdown_decision_row(index: int, entry: Mapping[str, object]) -> str:
    """Render one decision entry as a Markdown table row of the decisions table.

    The decisions table has five columns (``#``, round, action, performative, rationale); the
    timeline renderer's seven cells would spill two extra columns into it.
    """
    return "| {index} | {round} | {action} | {performative} | {rationale} |".format(
        index=index,
        round=_markdown_cell(entry.get("round")),
        action=_markdown_cell(entry.get("action") or entry.get("kind")),
        performative=_markdown_cell(entry.get("performative")),
        rationale=_markdown_cell(entry.get("rationale")),
    )
