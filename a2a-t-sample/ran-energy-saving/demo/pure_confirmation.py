"""Confirm-mode answer for the pure A2A form.

The pure hop has no protocol negotiation metadata, so the answer lives entirely in business text.
The OSS Agent relays the downstream question and, when its decision model produced one, a
recommended answer. This answer object turns the relayed question into the same human moment the
A2A-T form already uses: the page shows the question and the recommendation, the operator confirms
or edits it, and only the submitted answer leaves the client.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Final

from a2a_t.core.metadata import NegotiationContext
from protocol.client_runtime import MODE_CONFIRM
from protocol.decision_provider import DEFAULT_MANUAL_TIMEOUT_SECONDS, ManualDecisionProvider
from protocol.grounding import server_target_rates
from protocol.pure_client_flow import pure_proposal_view
from shared.models import NegotiationDecision, NegotiationProposeView

from demo.pure_policy import recommendation_decision, render_pure_answer

ProposalSink = Callable[[NegotiationProposeView, NegotiationContext], None]
RecommendationSink = Callable[[NegotiationDecision | None, NegotiationProposeView, NegotiationContext], None]
DecisionSink = Callable[[NegotiationDecision, NegotiationProposeView, NegotiationContext], None]

#: Metadata key the OSS Agent uses for the relayed question payload.
_PURE_METADATA_KEY: Final[str] = "pure"

#: How many value-less acceptances the client sends before it refuses the negotiation. The vendor
#: never stops asking on its own and proposes no value in a pure-A2A round, so a bare "accept"
#: changes nothing: after this many rounds the client has to terminate explicitly.
MAX_VALUE_LESS_ACCEPTS: Final[int] = 3

#: Explicit refusal sent when no concrete value is available. It names the conflict so the vendor
#: can read it as a refusal instead of another acceptance.
NO_ADJUSTMENT_TEXT: Final[str] = (
    "No adjustment is possible: the guaranteed rate and the outage duration cannot both be met, "
    "and no new value is available from this client. Please terminate the task."
)

_LOGGER = logging.getLogger(__name__)


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
        """Create one confirmation answer for the given run mode.

        The wait follows the same policy as the A2A-T manual provider: an expired observation
        window is reported to the page and never ends the run or the downstream Task. The pure hop
        used to keep its legacy single-window abort, which turned an operator still deciding into a
        failed run.
        """
        self._mode = mode
        self._max_rounds = max_rounds
        self._manual = ManualDecisionProvider(
            timeout_seconds=timeout_seconds,
            keep_waiting=True,
        )
        self._proposal_sink: ProposalSink | None = None
        self._recommendation_sink: RecommendationSink | None = None
        self._decision_sink: DecisionSink | None = None
        self._last_recommendation_error: str | None = None
        #: The operator's own words for the next answer. A pure A2A answer is one plain text, so a
        #: typed answer is sent verbatim instead of a re-rendered Task-T summary.
        self._answer_text: str | None = None
        #: Questions answered so far; the fallback round of a peer that does not number them.
        self._rounds = 0
        #: Consecutive acceptances that carried no value and accepted nothing concrete.
        self._value_less_accepts = 0

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

    def set_wait_sink(self, sink: Callable[[object], None] | None) -> None:
        """Install the expired-window observer of the human wait."""
        self._manual.set_wait_sink(sink)

    def set_answer_text(self, text: str | None) -> None:
        """Use the operator's typed answer as the next downstream text.

        The structured preview exists to inspect what the model understood; the pure hop still
        sends one plain message, and the vendor negotiates in prose ("OK, lower to 2Mbps during the
        outage."). Re-rendering the preview would replace the operator's wording with a Task-T
        summary the vendor did not ask for.
        """
        self._answer_text = text.strip() if isinstance(text, str) and text.strip() else None

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
        typed = self._answer_text
        self._answer_text = None
        if typed:
            self._value_less_accepts = 0
            return typed
        if _is_value_less_accept(decision, propose):
            self._value_less_accepts += 1
            if self._value_less_accepts >= MAX_VALUE_LESS_ACCEPTS:
                _LOGGER.warning(
                    "pure A2A: refusing after %s value-less acceptances; the vendor proposed no "
                    "value and the client has none to give",
                    self._value_less_accepts,
                )
                return NO_ADJUSTMENT_TEXT
        else:
            self._value_less_accepts = 0
        return render_pure_answer(decision)


def _carries_a_value(decision: NegotiationDecision) -> bool:
    """Return whether one decision states a concrete value or an explicit refusal."""
    if decision.action in ("reject", "abort"):
        return True
    return bool(decision.filled_params or decision.counter_params or decision.counter_proposal)


def _is_value_less_accept(decision: NegotiationDecision, propose: NegotiationProposeView) -> bool:
    """Return whether one acceptance agrees to something the question never stated.

    The vendor's pure-A2A question is the fixed sentence ``Not feasible, please confirm``: it names
    no rate and no window, so accepting it changes nothing. A sample-server proposal that states a
    revised plan is different, and a bare accept of it is a complete answer.
    """
    if decision.action != "accept" or _carries_a_value(decision):
        return False
    return not server_target_rates(propose)


__all__ = ["MAX_VALUE_LESS_ACCEPTS", "NO_ADJUSTMENT_TEXT", "PureConfirmationAnswer"]
