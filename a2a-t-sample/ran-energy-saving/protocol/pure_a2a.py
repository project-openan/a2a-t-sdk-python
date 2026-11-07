"""Standard A2A multi-turn negotiation used by the pure protocol mode.

The pure mode deliberately uses no A2A-T extension. A missing or conflicting target is resolved
through the standard A2A Task lifecycle:

1. the client sends a plain ``message/send``;
2. the server creates a Task and publishes ``INPUT_REQUIRED`` with a question or a proposal;
3. the client sends a new message carrying ``taskId``/``contextId``;
4. the server resumes the Task, executes it, or asks again (up to ``max_rounds``).

The module owns the protocol mechanics. The business decision (which target to propose, how to
validate the answer) is delegated to :class:`PureTargetPolicy`, so the demo keeps its RAN dataset
rules outside this module.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution.context import RequestContext
from a2a.types import Message, Task, TaskState, TaskStatus

from protocol.messages import build_agent_message, metadata_to_dict
from protocol.pre_task_negotiation import NegotiationOutcome

#: One ``{HH:MM~HH:MM,<rate>Mbps}`` segment, accepting the ``、`` separator of Chinese requests.
_TARGET_SEGMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(\d{2}:\d{2})\s*~\s*(\d{2}:\d{2})\s*[,，、]\s*(\d+(?:\.\d+)?)\s*Mbps",
    re.IGNORECASE,
)

#: Text of a pure A2A request that carries no text at all.
EMPTY_REQUEST_TEXT: Final[str] = "The pure A2A request carries no task description."

#: Metadata namespace of the pure mode. It is sample-local and is not an A2A-T extension.
PURE_METADATA_KEY: Final[str] = "pure"

#: Negotiation phases of the pure mode.
PURE_PHASE_INFORMATION: Final[str] = "information"
PURE_PHASE_TARGET: Final[str] = "target"

_LOGGER = logging.getLogger("ran_energy_saving.pure_a2a")


@dataclass(frozen=True)
class TargetSegment:
    """One time-segmented guaranteed throughput target."""

    start: str
    end: str
    rate: str


@dataclass(frozen=True)
class PureFinding:
    """Initial signal that a pure A2A request needs a negotiation."""

    kind: Literal["information", "target"]
    question: str
    proposal: str | None = None


@dataclass(frozen=True)
class PureResolution:
    """Result of evaluating one client answer."""

    kind: Literal["resolved", "retry", "rejected"]
    target: str | None = None
    question: str | None = None
    proposal: str | None = None
    reason: str | None = None


@dataclass
class PureNegotiationState:
    """In-memory state of one pure A2A negotiation, keyed by Task id."""

    task_id: str
    context_id: str
    original_text: str
    kind: str
    round: int
    max_rounds: int
    proposed_target: str | None = None
    resolved_target: str | None = None


class PureTargetPolicy(Protocol):
    """Business policy of a pure A2A target negotiation."""

    def inspect(self, text: str, language: str) -> PureFinding | None:
        """Return a finding when ``text`` needs a negotiation, else ``None``."""

    def resolve(
        self, state: PureNegotiationState, answer: str, language: str
    ) -> PureResolution:
        """Evaluate one client answer and return the next protocol step."""


class RegexPureTargetPolicy:
    """Default policy: detect missing/overlapping target segments and validate answers.

    The policy is intentionally free of demo scenario data. The caller supplies the localized
    baseline target used as the proposal for a conflict (and as the expected answer for a missing
    target). The demo composition root injects the RAN dataset's target.
    """

    def __init__(self, *, base_targets: Mapping[str, str] | None = None) -> None:
        """Create one policy.

        Args:
            base_targets: localized baseline target strings, keyed by language.
        """
        self._base_targets = dict(base_targets or {})

    def base_target(self, language: str) -> str:
        """Return the localized baseline target, or an empty string when none is configured."""
        return self._base_targets.get(language) or self._base_targets.get("en-US") or ""

    def inspect(self, text: str, language: str) -> PureFinding | None:
        """Return ``information`` for a missing target, ``target`` for overlapping segments."""
        segments = parse_target_segments(text)
        if not segments:
            return PureFinding(
                kind="information",
                question=(
                    "The request does not define the time-segmented guaranteed throughput target. "
                    "Please provide it in the form {HH:MM~HH:MM,NMbps}."
                ),
            )
        if has_overlap(segments):
            proposal = self.base_target(language)
            proposal_text = f" I propose: {proposal}." if proposal else ""
            return PureFinding(
                kind="target",
                question=(
                    "The time-segmented guaranteed throughput target has overlapping segments."
                    f"{proposal_text} Do you accept, or propose a non-overlapping target?"
                ),
                proposal=proposal or None,
            )
        return None

    def resolve(
        self, state: PureNegotiationState, answer: str, language: str
    ) -> PureResolution:
        """Accept a non-overlapping target; retry or reject an unusable answer."""
        segments = parse_target_segments(answer)
        if not segments:
            return self._retry_or_reject(
                state,
                "The answer does not contain a time-segmented target in the form "
                "{HH:MM~HH:MM,NMbps}.",
                language,
            )
        if has_overlap(segments):
            return self._retry_or_reject(
                state,
                "The proposed time-segmented target still has overlapping segments.",
                language,
            )
        target = format_target_segments(segments, language)
        if (
            state.kind == PURE_PHASE_TARGET
            and state.proposed_target
            and canonical_target(state.proposed_target, language) != target
        ):
            # A different valid target is a counter-proposal: the server keeps its own proposal
            # until the client accepts it or the round budget is exhausted.
            return self._retry_or_reject(
                state,
                f"The answer differs from the server's proposal ({state.proposed_target}).",
                language,
            )
        return PureResolution(kind="resolved", target=target)

    def _retry_or_reject(
        self, state: PureNegotiationState, reason: str, language: str
    ) -> PureResolution:
        """Return a retry while the round budget allows it, otherwise a rejection."""
        if state.round >= state.max_rounds:
            return PureResolution(
                kind="rejected",
                reason=f"Negotiation did not converge after {state.max_rounds} rounds: {reason}",
            )
        return PureResolution(
            kind="retry",
            question=f"{reason} Please provide the target again.",
            proposal=self.base_target(language) or None,
        )


class PureTaskHandler:
    """Serve the pure A2A entry: initial request, ``INPUT_REQUIRED`` continuation, execution."""

    def __init__(
        self,
        *,
        policy: PureTargetPolicy,
        language: str = "en-US",
        max_rounds: int = 3,
        logger: logging.Logger | None = None,
    ) -> None:
        """Create one pure handler.

        Args:
            policy: business policy deciding missing/conflicting targets and validating answers.
            language: process-level language used to render/parse target separators.
            max_rounds: negotiation round budget.
            logger: optional logger.
        """
        self._policy = policy
        self._language = language
        self._max_rounds = max_rounds
        self._logger = logger or _LOGGER
        self._states: dict[str, PureNegotiationState] = {}

    def state_for(self, task_id: str) -> PureNegotiationState | None:
        """Return the in-memory state of one negotiation (test hook)."""
        return self._states.get(task_id)

    async def handle(self, context: RequestContext) -> NegotiationOutcome:
        """Handle one initial or continuation request."""
        current = context.current_task
        if current is not None:
            metadata = pure_metadata(current)
            if metadata.get("kind") == "negotiation":
                return await self._resume(context, current)
            # A follow-up on a Task that is not a pure negotiation is not ours to advance.
            return NegotiationOutcome.execution(context)

        message = context.message
        text = _message_text(message)
        if not text:
            return NegotiationOutcome.short(
                build_agent_message(
                    context_id=context.context_id or "", text=EMPTY_REQUEST_TEXT
                )
            )

        finding = self._policy.inspect(text, self._language)
        if finding is None:
            self._logger.info("pure request accepted; handing it to the deterministic executor")
            return NegotiationOutcome.execution(context)

        task = self._new_input_required_task(context, message, text, finding)
        self._logger.info(
            "pure request opens a %s negotiation: task=%s", finding.kind, task.id
        )
        return NegotiationOutcome.input_required(task)

    def _new_input_required_task(
        self,
        context: RequestContext,
        message: Message | None,
        text: str,
        finding: PureFinding,
        *,
        round_number: int = 1,
        proposal: str | None = None,
    ) -> Task:
        """Build the non-terminal Task carrying one question/proposal."""
        task = new_task_from_user_message(message) if message is not None else Task()
        task.id = context.task_id or str(uuid.uuid4())
        task.context_id = context.context_id or task.id
        self._apply_input_required(task, text, finding, round_number, proposal)
        return task

    def _apply_input_required(
        self,
        task: Task,
        original_text: str,
        finding: PureFinding,
        round_number: int,
        proposal: str | None,
    ) -> None:
        """Write one question/proposal onto an existing Task and refresh the in-memory state."""
        pure = {
            "kind": "negotiation",
            "phase": finding.kind,
            "round": round_number,
            "maxRounds": self._max_rounds,
            "proposal": proposal if proposal is not None else finding.proposal,
            "question": finding.question,
        }
        task.status.state = TaskState.TASK_STATE_INPUT_REQUIRED
        task.status.message.CopyFrom(
            build_agent_message(
                context_id=task.context_id,
                text=finding.question,
                metadata={PURE_METADATA_KEY: pure},
            )
        )
        task.metadata[PURE_METADATA_KEY] = pure
        state = self._states.get(task.id)
        if state is None:
            state = PureNegotiationState(
                task_id=task.id,
                context_id=task.context_id,
                original_text=original_text,
                kind=finding.kind,
                round=round_number,
                max_rounds=self._max_rounds,
            )
        state.round = round_number
        state.proposed_target = (
            pure.get("proposal") if isinstance(pure.get("proposal"), str) else None
        )
        self._states[task.id] = state

    async def _resume(self, context: RequestContext, task: Task) -> NegotiationOutcome:
        """Evaluate one continuation message and either ask again or execute."""
        state = self._states.get(task.id)
        if state is None:
            self._logger.warning("pure negotiation state missing for task %s", task.id)
            return NegotiationOutcome.rejected(
                _build_rejected_task(
                    context,
                    "The pure A2A negotiation session is no longer available; please start again.",
                )
            )

        answer = _message_text(context.message)
        resolution = self._policy.resolve(state, answer, self._language)
        if resolution.kind == "resolved" and resolution.target:
            self._states.pop(task.id, None)
            state.resolved_target = resolution.target
            self._logger.info("pure negotiation resolved: task=%s", task.id)
            return NegotiationOutcome.resume(
                context, _resume_prompt(state.original_text, resolution.target)
            )
        if resolution.kind == "retry":
            state.round += 1
            finding = PureFinding(
                kind="target" if state.kind == "target" else "information",
                question=resolution.question or "Please provide the target again.",
                proposal=resolution.proposal,
            )
            self._apply_input_required(
                task, state.original_text, finding, state.round, resolution.proposal
            )
            return NegotiationOutcome.input_required(task)

        self._states.pop(task.id, None)
        reason = resolution.reason or "The pure A2A negotiation was rejected."
        self._logger.info("pure negotiation rejected: task=%s reason=%s", task.id, reason)
        return NegotiationOutcome.rejected(_build_rejected_task(context, reason))


def parse_target_segments(text: str) -> tuple[TargetSegment, ...]:
    """Extract every ``{HH:MM~HH:MM,<rate>Mbps}`` target segment from ``text``."""
    return tuple(
        TargetSegment(start=match.group(1), end=match.group(2), rate=match.group(3))
        for match in _TARGET_SEGMENT_PATTERN.finditer(text)
    )


def has_overlap(segments: tuple[TargetSegment, ...]) -> bool:
    """Return whether any two target segments overlap."""
    intervals = sorted(
        (_minutes(segment.start), _minutes(segment.end)) for segment in segments
    )
    for index in range(1, len(intervals)):
        previous_end = intervals[index - 1][1]
        current_start = intervals[index][0]
        if current_start < previous_end:
            return True
    return False


def format_target_segments(segments: tuple[TargetSegment, ...], language: str) -> str:
    """Render target segments in the request format of ``language``."""
    separator = "、" if language.startswith("zh") else ", "
    return separator.join(
        f"{{{segment.start}~{segment.end},{segment.rate}Mbps}}" for segment in segments
    )


def canonical_target(text: str, language: str) -> str:
    """Return the canonical form of the target segments in ``text``, or ``""``."""
    segments = parse_target_segments(text)
    return format_target_segments(segments, language) if segments else ""


def pure_metadata(task: Task) -> dict[str, object]:
    """Return the ``pure`` metadata map of one Task, falling back to its status message."""
    for source in (task.metadata, task.status.message.metadata):
        raw = metadata_to_dict(source).get(PURE_METADATA_KEY)
        if isinstance(raw, Mapping):
            return dict(raw)
    return {}


def _message_text(message: Message | None) -> str:
    """Return the first non-empty text part of one message."""
    if message is None:
        return ""
    for part in message.parts or ():
        text = getattr(part, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _minutes(value: str) -> int:
    """Convert ``HH:MM`` to minutes, treating ``24:00`` as the end of day."""
    hours, minutes = value.split(":", 1)
    return int(hours) * 60 + int(minutes)


def _resume_prompt(original: str, target: str) -> str:
    """Build the planner prompt with the negotiated target as the authoritative value."""
    return (
        f"{original}\n\n"
        f"Negotiated guaranteed throughput target (authoritative): {target}"
    )


def _build_rejected_task(context: RequestContext, text: str) -> Task:
    """Build the terminal ``REJECTED`` Task of a failed pure negotiation."""
    context_id = context.context_id or ""
    task_id = context.task_id or str(uuid.uuid4())
    status_message = build_agent_message(context_id=context_id, text=text)
    message = context.message
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(
            state=TaskState.TASK_STATE_REJECTED,
            message=status_message,
        ),
        history=[message] if message is not None else [],
    )


__all__ = [
    "EMPTY_REQUEST_TEXT",
    "PURE_METADATA_KEY",
    "PURE_PHASE_INFORMATION",
    "PURE_PHASE_TARGET",
    "PureFinding",
    "PureNegotiationState",
    "PureResolution",
    "PureTargetPolicy",
    "PureTaskHandler",
    "RegexPureTargetPolicy",
    "TargetSegment",
    "canonical_target",
    "format_target_segments",
    "has_overlap",
    "parse_target_segments",
    "pure_metadata",
]
