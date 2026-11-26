"""Test-only decision provider replaying a fixed decision sequence."""

from __future__ import annotations

from collections.abc import Sequence

from a2a_t.core.metadata import NegotiationContext
from protocol.decision_provider import DecisionProviderError
from shared.models import NegotiationDecision, NegotiationProposeView


class ScriptedDecisionProvider:
    """Decision provider replaying a pre-defined sequence of decisions.

    The offline scenarios of :mod:`demo.scenario` carry their decisions in order; each call to
    :meth:`respond` consumes the next one. Exhausting the sequence is an error, so a scenario whose
    server behavior drifts from its script fails loudly instead of silently accepting.
    """

    def __init__(
        self,
        decisions: Sequence[NegotiationDecision],
        *,
        graceful_exhaustion: bool = False,
    ) -> None:
        """Create one scripted provider over an ordered decision list.

        ``graceful_exhaustion`` marks a script the caller supplied for one run (the tester's case
        column) rather than the scenario's own catalogue. Such a script running out means the case
        recorded fewer rounds than the vendor asked, so the client stops answering and reports the
        reason instead of failing the run; a scenario script keeps failing loudly.
        """
        self._decisions = tuple(decisions)
        self._index = 0
        self.graceful_exhaustion = graceful_exhaustion

    @property
    def remaining(self) -> int:
        """Number of scripted decisions not consumed yet."""
        return len(self._decisions) - self._index

    async def respond(
        self,
        propose: NegotiationProposeView,
        session: NegotiationContext,
    ) -> NegotiationDecision:
        """Return the next scripted decision.

        Raises:
            DecisionProviderError: when the script has no decision left.
        """
        del propose, session
        if self._index >= len(self._decisions):
            raise DecisionProviderError("The scripted decision sequence is exhausted.")
        decision = self._decisions[self._index]
        self._index += 1
        return decision
