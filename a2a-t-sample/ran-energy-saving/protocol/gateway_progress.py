"""Translate a client run's internal events into outer A2A Task progress.

The inbound gateway hides the A2A-T negotiation: an external caller sees one A2A Task. This mapper
publishes a small, stable set of milestones on that Task while the client run negotiates with the
RAN server. It never exposes raw prompts, proposals, tool arguments or trace spans.

The same milestones feed ``message:stream`` and push notifications; delivery is the A2A runtime's
job, not this module's.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

#: Negotiation performatives worth reporting to an external caller.
_REPORTED_PERFORMATIVES: Final[frozenset[str]] = frozenset(
    {"PROPOSE", "ACCEPT", "REJECT", "ABORT"}
)

#: RAN task states worth reporting once.
_EXECUTION_STATES: Final[frozenset[str]] = frozenset(
    {"TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"}
)

#: Apply artifact marker of the sample's NMS tool. Kept local so this mapper does not import the
#: LangGraph executor module (and its tool stack) just for one constant.
_APPLY_TOOL_NAME: Final[str] = "apply_energy_saving_plan"

#: Upper bound of progress updates per inbound Task.
MAX_PROGRESS_EVENTS: Final[int] = 10


@dataclass(frozen=True)
class ProgressUpdate:
    """One outer-Task status update derived from an internal run event.

    Attributes:
        text: short human-readable milestone.
        metadata: structured fields the caller can render (phase, round, performative, action).
        key: deduplication key.
    """

    text: str
    metadata: dict[str, object]
    key: str


class GatewayProgressMapper:
    """Stateful mapper of one run's broadcast messages (one instance per inbound Task)."""

    def __init__(self, *, max_events: int = MAX_PROGRESS_EVENTS) -> None:
        self._max_events = max(1, max_events)
        self._seen: set[str] = set()
        self._count = 0
        self._run_id: str | None = None

    def map(self, message: Mapping[str, object]) -> ProgressUpdate | None:
        """Return the progress update of one broadcast message, or ``None``.

        Trace spans, artifacts, manual-decision requests and repeated milestones are ignored.
        """
        if self._count >= self._max_events:
            return None
        message_type = str(message.get("type") or "")
        if message_type == "run_started":
            return self._accept(message)
        if message_type != "event":
            return None
        entry = message.get("event")
        if not isinstance(entry, Mapping):
            return None
        event_kind = str(entry.get("eventKind") or "")
        if event_kind == "message":
            return self._negotiation_message(entry)
        if event_kind == "decision":
            return self._decision(entry)
        if event_kind == "status":
            return self._execution(entry)
        if event_kind == "artifact":
            return self._artifact(entry)
        return None

    def _accept(self, message: Mapping[str, object]) -> ProgressUpdate | None:
        run_id = message.get("runId")
        if isinstance(run_id, str):
            self._run_id = run_id
        return self._build(
            key="accepted",
            text="Request accepted. Negotiating with the RAN server.",
            metadata={"phase": "dispatch", "clientRunId": self._run_id},
        )

    def _negotiation_message(self, entry: Mapping[str, object]) -> ProgressUpdate | None:
        performative = entry.get("performative")
        if not isinstance(performative, str) or performative not in _REPORTED_PERFORMATIVES:
            return None
        round_number = _int_value(entry.get("round"))
        kind = entry.get("negotiationType")
        if performative == "PROPOSE":
            if isinstance(kind, str) and kind:
                article = "an" if kind[:1].lower() in "aeiou" else "a"
                detail = f"proposed {article} {kind} negotiation"
            else:
                detail = "sent a negotiation proposal"
        else:
            detail = f"sent {performative}"
        return self._build(
            key=f"message:{round_number}:{performative}",
            text=f"Negotiation round {round_number or 1}: the RAN server {detail}.",
            metadata={
                "phase": "negotiation",
                "round": round_number,
                "performative": performative,
                "kind": kind,
                "clientRunId": self._run_id,
            },
        )

    def _decision(self, entry: Mapping[str, object]) -> ProgressUpdate | None:
        action = entry.get("action")
        if not isinstance(action, str) or not action:
            return None
        round_number = _int_value(entry.get("round"))
        return self._build(
            key=f"decision:{round_number}:{action}",
            text=f"Negotiation round {round_number or 1}: the client chose {action}.",
            metadata={
                "phase": "negotiation",
                "round": round_number,
                "action": action,
                "performative": entry.get("performative"),
                "clientRunId": self._run_id,
            },
        )

    def _execution(self, entry: Mapping[str, object]) -> ProgressUpdate | None:
        state = str(entry.get("summary") or "")
        if state not in _EXECUTION_STATES:
            return None
        return self._build(
            key="execution",
            text="The RAN server accepted the task and started execution.",
            metadata={
                "phase": "execution",
                "state": state,
                "clientRunId": self._run_id,
            },
        )

    def _artifact(self, entry: Mapping[str, object]) -> ProgressUpdate | None:
        """Map the apply and report artifacts to the two execution milestones callers wait for.

        Other artifacts (profiles, evaluations, trace payloads) stay internal: forwarding every
        tool result would leak NMS internals and flood the outer Task.
        """
        if str(entry.get("phase") or "") == "report":
            return self._build(
                key="execution:report",
                text="The RAN server published the energy-saving report.",
                metadata={
                    "phase": "report",
                    "clientRunId": self._run_id,
                },
            )
        detail = entry.get("detail")
        tool_name = detail.get("toolName") if isinstance(detail, Mapping) else None
        if tool_name == _APPLY_TOOL_NAME:
            return self._build(
                key="execution:applied",
                text="The RAN server applied the energy-saving plan.",
                metadata={
                    "phase": "execution",
                    "state": "applied",
                    "clientRunId": self._run_id,
                },
            )
        return None

    def _build(
        self,
        *,
        key: str,
        text: str,
        metadata: dict[str, object],
    ) -> ProgressUpdate | None:
        if key in self._seen or self._count >= self._max_events:
            return None
        self._seen.add(key)
        self._count += 1
        return ProgressUpdate(
            text=text,
            metadata={name: value for name, value in metadata.items() if value is not None},
            key=key,
        )


def _int_value(value: object) -> int | None:
    """Return an integer field, ``None`` when it is absent or not numeric."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = ["GatewayProgressMapper", "MAX_PROGRESS_EVENTS", "ProgressUpdate"]
