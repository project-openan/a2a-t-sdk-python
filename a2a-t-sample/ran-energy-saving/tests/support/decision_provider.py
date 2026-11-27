"""Test-only decision provider replaying a fixed decision sequence."""

from __future__ import annotations

from collections.abc import Sequence

from a2a_t.core.metadata import NegotiationContext
from protocol.decision_provider import DecisionProviderError
from shared.models import NegotiationDecision, NegotiationProposeView


class ScriptedDecisionProvider:
    """Decision provider replaying a pre-defined sequence of decisions.

    Each call to :meth:`respond` consumes the next decision. Exhausting the sequence is an error,
    so a test whose server behavior drifts from its script fails loudly instead of silently
    accepting.
    """

    def __init__(self, decisions: Sequence[NegotiationDecision]) -> None:
        """Create one scripted provider over an ordered decision list."""
        self._decisions = tuple(decisions)
        self._index = 0

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
