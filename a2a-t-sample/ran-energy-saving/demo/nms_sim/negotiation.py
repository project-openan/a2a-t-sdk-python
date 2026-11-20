"""Negotiation-T state machine of the NMS simulator.

The simulator reproduces the vendor contract the client must handle: a proposal arrives either as
a standalone ``Message`` or inside the status message of a Task in ``TASK_STATE_INPUT_REQUIRED``
(the real NMS default), carries the rendered Negotiation-T body plus ``templateUri`` and
``negotiationContext``, and the server owns the round counter. A reply is matched by the
negotiation id, never by the A2A context id; the real NMS rejects that confusion with HTTP 422.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    TEMPLATE_URI_METADATA_KEY,
)

if TYPE_CHECKING:
    from demo.nms_sim.intent import ExtractedContent

#: Negotiation kinds.
KIND_INFORMATION: Final[str] = "information"
KIND_TARGET: Final[str] = "target"
KIND_FEASIBILITY: Final[str] = "feasibility"
SUPPORTED_KINDS: Final[tuple[str, ...]] = (KIND_INFORMATION, KIND_TARGET, KIND_FEASIBILITY)

#: Negotiation performatives.
PERFORMATIVE_PROPOSE: Final[str] = "PROPOSE"
PERFORMATIVE_ACCEPT: Final[str] = "ACCEPT"
PERFORMATIVE_REJECT: Final[str] = "REJECT"
PERFORMATIVE_ABORT: Final[str] = "ABORT"
SUPPORTED_PERFORMATIVES: Final[frozenset[str]] = frozenset(
    {PERFORMATIVE_PROPOSE, PERFORMATIVE_ACCEPT, PERFORMATIVE_REJECT, PERFORMATIVE_ABORT}
)

#: Abort template of the Negotiation-T specification.
ABORT_TEMPLATE_URI: Final[str] = "Negotiation-T/common/abort/v1"

_HEADINGS: Final[dict[str, str]] = {
    KIND_INFORMATION: "## Information Negotiation",
    KIND_TARGET: "## Target Negotiation",
    KIND_FEASIBILITY: "## Feasibility Negotiation",
}

_INTROS: Final[dict[str, str]] = {
    KIND_INFORMATION: "Please supplement the relevant content based on <Required Information Items>.",
    KIND_TARGET: (
        "Target negotiation for the RAN energy saving task: the requested goal exceeds the "
        "energy-saving capability of the cell."
    ),
    KIND_FEASIBILITY: (
        "Feasibility negotiation for the RAN energy saving task: the requested plan cannot be "
        "executed as written."
    ),
}


@dataclass(frozen=True)
class NegotiationIssue:
    """One reason the simulator cannot accept a request, and the kind that describes it.

    Attributes:
        kind: ``information``, ``target`` or ``feasibility``.
        items: short names of the fields that need attention.
        detail: explanation shown under the required-information/adjustment section.
    """

    kind: str
    items: tuple[str, ...]
    detail: str = ""

    def heading(self) -> str:
        """Return the ``##`` heading that opens this issue's rendered body."""
        return _HEADINGS.get(self.kind, _HEADINGS[KIND_INFORMATION])

    def body(self) -> str:
        """Render the Negotiation-T body of this issue."""
        lines = [self.heading(), "", _INTROS.get(self.kind, _INTROS[KIND_INFORMATION])]
        if self.kind == KIND_INFORMATION:
            lines.extend(["", "## Required Information Items"])
            lines.extend(f"{index}. {item}" for index, item in enumerate(self.items, 1))
            if self.detail:
                lines.extend(["", self.detail])
        else:
            lines.extend(["", "## Adjustment Proposal"])
            lines.extend(f"{index}. {item}" for index, item in enumerate(self.items, 1))
            if self.detail:
                lines.extend(["", self.detail])
        return "\n".join(lines)


def template_uri(kind: str) -> str:
    """Return the Negotiation-T propose template URI of one kind."""
    resolved = kind if kind in SUPPORTED_KINDS else KIND_INFORMATION
    return f"Negotiation-T/{resolved}-negotiation/propose/v1"


def abort_body(max_rounds: int) -> str:
    """Render the body of an ``ABORT`` reply."""
    return "\n".join(
        [
            "## Abort Negotiation",
            "",
            f"The negotiation exceeded its round budget of {max_rounds}.",
        ]
    )


@dataclass(frozen=True)
class NegotiationContext:
    """The ``negotiationContext`` carried by one message.

    Attributes:
        id: Negotiation session id; the server owns it and a reply must echo it.
        round: 1-based round of the proposal.
        max_rounds: Round budget advertised by the server.
        performative: Upper-case performative of the carried message.
    """

    id: str
    round: int
    max_rounds: int
    performative: str

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, object] | None) -> NegotiationContext | None:
        """Parse the nested context of one metadata map, ``None`` when absent or malformed."""
        if not metadata:
            return None
        raw = metadata.get(NEGOTIATION_CONTEXT_METADATA_KEY)
        if not isinstance(raw, Mapping):
            return None
        identifier = raw.get("id")
        performative = raw.get("performative")
        if not isinstance(identifier, str) or not identifier.strip():
            return None
        if not isinstance(performative, str) or performative.upper() not in SUPPORTED_PERFORMATIVES:
            return None
        try:
            round_number = int(raw.get("round", 0))
            max_rounds = int(raw.get("maxRounds", 0))
        except (TypeError, ValueError):
            return None
        if round_number < 1 or max_rounds < 1:
            return None
        return cls(
            id=identifier.strip(),
            round=round_number,
            max_rounds=max_rounds,
            performative=performative.upper(),
        )

    def as_metadata(self) -> dict[str, object]:
        """Return the wire form of this context."""
        return {
            "id": self.id,
            "round": self.round,
            "maxRounds": self.max_rounds,
            "performative": self.performative,
        }


@dataclass
class NegotiationSession:
    """Server-side state of one negotiation.

    Attributes:
        id: Negotiation id the client must echo.
        round: Current round (starts at 1).
        max_rounds: Round budget.
        kind: Kind of the last proposal.
        context_id: A2A context id used for the proposal.
        task_id: Task carrying the negotiation, when the carrier is a Task or the request named one.
        baseline: Business content of the original request, used to complete a terse accept.
    """

    id: str
    round: int
    max_rounds: int
    kind: str
    context_id: str
    task_id: str | None = None
    baseline: "ExtractedContent | None" = None


class NegotiationStore:
    """In-memory negotiations keyed by their negotiated id."""

    def __init__(self) -> None:
        """Create one empty store."""
        self._sessions: dict[str, NegotiationSession] = {}

    def create(
        self,
        *,
        kind: str,
        max_rounds: int,
        context_id: str,
        task_id: str | None = None,
        baseline: "ExtractedContent | None" = None,
    ) -> NegotiationSession:
        """Start one negotiation at round 1."""
        session = NegotiationSession(
            id=str(uuid.uuid4()),
            round=1,
            max_rounds=max(1, max_rounds),
            kind=kind,
            context_id=context_id,
            task_id=task_id,
            baseline=baseline,
        )
        self._sessions[session.id] = session
        return session

    def get(self, negotiation_id: str) -> NegotiationSession | None:
        """Return one negotiation by id."""
        return self._sessions.get(negotiation_id)

    def context(self, session: NegotiationSession, *, performative: str = PERFORMATIVE_PROPOSE) -> NegotiationContext:
        """Return the context of the current round."""
        return NegotiationContext(
            id=session.id,
            round=session.round,
            max_rounds=session.max_rounds,
            performative=performative,
        )

    def advance(self, session: NegotiationSession, *, kind: str) -> NegotiationContext:
        """Increment the round and update the kind of the next proposal."""
        session.round += 1
        session.kind = kind
        return self.context(session)

    def close(self, session: NegotiationSession) -> None:
        """Forget one finished negotiation."""
        self._sessions.pop(session.id, None)


def propose_metadata(issue: NegotiationIssue, context: NegotiationContext) -> dict[str, object]:
    """Build the metadata map of one proposal."""
    return {
        NEGOTIATION_T_EXTENSION_URI: issue.body(),
        TEMPLATE_URI_METADATA_KEY: template_uri(issue.kind),
        NEGOTIATION_CONTEXT_METADATA_KEY: context.as_metadata(),
    }


def abort_metadata(context: NegotiationContext) -> dict[str, object]:
    """Build the metadata map of one abort message."""
    return {
        NEGOTIATION_T_EXTENSION_URI: abort_body(context.max_rounds),
        TEMPLATE_URI_METADATA_KEY: ABORT_TEMPLATE_URI,
        NEGOTIATION_CONTEXT_METADATA_KEY: {
            **context.as_metadata(),
            "performative": PERFORMATIVE_ABORT,
        },
    }


def negotiation_message(
    *,
    context_id: str,
    body: str,
    metadata: Mapping[str, object],
) -> dict[str, object]:
    """Build one standalone agent Message carrying a negotiation body."""
    return {
        "messageId": str(uuid.uuid4()),
        "contextId": context_id,
        "role": "ROLE_AGENT",
        "parts": [{"text": "See metadata for details." if metadata else body}],
        "metadata": dict(metadata),
    }
