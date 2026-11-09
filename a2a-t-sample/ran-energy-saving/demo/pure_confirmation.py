"""Confirm-mode answer for the pure A2A form.

The pure hop has no protocol negotiation metadata, so the answer lives entirely in business text.
The OSS Agent relays the downstream question and, when its decision model produced one, a
recommended answer. This answer object turns the relayed question into the same human moment the
A2A-T form already uses: the page shows the question and the recommendation, the operator confirms
or edits it, and only the submitted answer leaves the client.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final

from a2a_t.core.metadata import NegotiationContext
from protocol.client_runtime import MODE_CONFIRM
from protocol.decision_provider import DEFAULT_MANUAL_TIMEOUT_SECONDS, ManualDecisionProvider
from protocol.pure_client_flow import pure_proposal_view
from shared.models import NegotiationDecision, NegotiationProposeView

from demo.pure_policy import recommendation_decision, render_pure_answer

ProposalSink = Callable[[NegotiationProposeView, NegotiationContext], None]
RecommendationSink = Callable[[NegotiationDecision | None, NegotiationProposeView, NegotiationContext], None]
DecisionSink = Callable[[NegotiationDecision, NegotiationProposeView, NegotiationContext], None]

#: Metadata key the OSS Agent uses for the relayed question payload.
_PURE_METADATA_KEY: Final[str] = "pure"


def _pure_metadata(event: Mapping[str, object]) -> Mapping[str, object]:
    """Return the sample-local ``pure`` metadata of one normalized event."""
    metadata = event.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    raw = metadata.get(_PURE_METADATA_KEY)
    return raw if isinstance(raw, Mapping) else {}


class PureConfirmationAnswer:
    """Answer one relayed pure A2A question after a human confirmation.

    The object is the runtime's ``pure_answer`` callback and its manual provider: the Web UI's
    decision endpoint calls :meth:`submit` with the operator's structured decision.
    """

    def __init__(
        self,
        *,
        mode: str,
        timeout_seconds: float = DEFAULT_MANUAL_TIMEOUT_SECONDS,
        max_rounds: int = 3,
    ) -> None:
        """Create one confirmation answer for the given run mode."""
        self._mode = mode
        self._max_rounds = max_rounds
        self._manual = ManualDecisionProvider(timeout_seconds=timeout_seconds)
        self._proposal_sink: ProposalSink | None = None
        self._recommendation_sink: RecommendationSink | None = None
        self._decision_sink: DecisionSink | None = None
        self._last_recommendation_error: str | None = None
        #: Questions answered so far; the fallback round of a peer that does not number them.
        self._rounds = 0

    @property
    def last_recommendation_error(self) -> str | None:
        """Why the relayed recommendation is empty (suppressed or unavailable), else ``None``."""
        return self._last_recommendation_error

    @property
    def timeout_seconds(self) -> float:
        """Confirmation budget in seconds."""
        return self._manual.timeout_seconds

    @property
    def pending(self) -> bool:
        """Whether a confirmation is expected or already submitted."""
        return self._manual.pending

    def submit(self, decision: NegotiationDecision) -> None:
        """Hand the operator's decision to the waiting answer."""
        self._manual.submit(decision)

    def set_proposal_sink(self, sink: ProposalSink | None) -> None:
        """Install the callback that records/publishes the relayed question."""
        self._proposal_sink = sink

    def set_recommendation_sink(self, sink: RecommendationSink | None) -> None:
        """Install the callback that publishes the recommendation before the human wait."""
        self._recommendation_sink = sink

    def set_decision_sink(self, sink: DecisionSink | None) -> None:
        """Install the callback that records the submitted decision."""
        self._decision_sink = sink

    async def __call__(self, event: Mapping[str, object]) -> str:
        """Build the proposal view, wait for the operator, and render the answer text."""
        pure = _pure_metadata(event)
        self._last_recommendation_error = None
        self._rounds += 1
        propose, session = pure_proposal_view(
            event,
            default_max_rounds=self._max_rounds,
            fallback_round=self._rounds,
        )
        if self._proposal_sink is not None:
            self._proposal_sink(propose, session)
        if self._mode == MODE_CONFIRM and self._recommendation_sink is not None:
            suppressed = pure.get("recommendationSuppressed")
            self._last_recommendation_error = (
                "The AI suggestion repeats a value the NMS already rejected; enter the answer manually."
                if suppressed == "already_sent"
                else None
            )
            self._recommendation_sink(
                recommendation_decision(pure.get("recommendation")),
                propose,
                session,
            )
        decision = await self._manual.respond(propose, session)
        if decision.action == "abort" and decision.rationale and "timed out" in decision.rationale:
            # A pure hop has no protocol abort; stop the flow instead of sending the timeout text.
            return ""
        if self._decision_sink is not None:
            self._decision_sink(decision, propose, session)
        return render_pure_answer(decision)


__all__ = ["PureConfirmationAnswer"]
