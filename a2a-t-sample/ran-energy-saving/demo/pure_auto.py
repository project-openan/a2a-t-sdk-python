"""Automatic answer of a direct standard-A2A (pure) negotiation.

The direct hop has no OSS Agent in front of it: the client talks to the vendor NMS itself, and the
vendor negotiates in prose. The answer is built in two tiers:

1. a deterministic plan (:mod:`demo.pure_concession`) when the terse question has a value the
   request itself implies (a rate-quality term, a bare rate, or a long-outage concession);
2. the ADK decision agent for everything else, sanitized by
   :func:`protocol.pure_answer_guard.sanitize_pure_decision` and rendered as free text.

When neither produces an answer, the optional fallback (the confirmation card) asks the operator.
A bare "Accepted. Please proceed." is never sent: the real vendor cannot act on it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from protocol.client_policy import ClientPolicy
from protocol.decision_provider import NegotiationDecisionProvider
from protocol.grounding import server_target_rates
from protocol.pure_answer_guard import sanitize_pure_decision
from protocol.pure_client_flow import pure_proposal_view
from shared.models import NegotiationDecision

from demo.pure_concession import plan_pure_answer
from demo.pure_policy import render_pure_answer

PureAnswer = Callable[[Mapping[str, object]], str | Awaitable[str]]


class AutoPureAnswer:
    """Answer pure-A2A questions automatically, with an optional operator fallback.

    The object is the runtime's ``pure_answer`` callback. It also carries the provider hooks the
    runtime uses for the manual preview and for the run's request text, so the ADK prompt and the
    deterministic plan both see the operator's actual words.
    """

    def __init__(
        self,
        provider: NegotiationDecisionProvider,
        policy: ClientPolicy,
        *,
        request_text: str = "",
        fallback: PureAnswer | None = None,
    ) -> None:
        """Create one automatic answer.

        Args:
            provider: ADK decision provider used when no deterministic plan applies.
            policy: Client acceptance limits.
            request_text: Operator request used for grounding; the runtime keeps it current.
            fallback: Optional confirmation card used when the client cannot decide on its own.
        """
        self._provider = provider
        self._policy = policy
        self._request_text = request_text
        self._fallback = fallback
        self._proposal_sink: Callable[..., None] | None = None
        self._decision_sink: Callable[..., None] | None = None

    def set_user_request(self, request_text: str | None) -> None:
        """Keep the operator request the runtime is executing."""
        self._request_text = request_text or ""
        setter = getattr(self._provider, "set_user_request", None)
        if callable(setter):
            setter(request_text)

    def set_conversation_context(self, context: str | None) -> None:
        """Forward the recent-turn context to the decision provider."""
        setter = getattr(self._provider, "set_conversation_context", None)
        if callable(setter):
            setter(context)

    def set_proposal_sink(self, sink: Callable[..., None] | None) -> None:
        """Keep the proposal observer; every question is announced here.

        The fallback card is not given this sink as well: this object announces the question
        before deciding, so forwarding would duplicate the event when the card is used.
        """
        self._proposal_sink = sink

    def set_decision_sink(self, sink: Callable[..., None] | None) -> None:
        """Keep the decision observer and let the fallback record the operator's own decision."""
        self._decision_sink = sink
        setter = getattr(self._fallback, "set_decision_sink", None)
        if callable(setter):
            setter(sink)

    async def __call__(self, event: Mapping[str, object]) -> str:
        """Return the free-text answer of one pure-A2A question."""
        propose, session = pure_proposal_view(event)
        if self._proposal_sink is not None:
            self._proposal_sink(propose, session)
        request_text = self._request_text or _provider_text(self._provider)
        if propose.kind in ("feasibility", "target") and not server_target_rates(propose):
            plan = plan_pure_answer(request_text, propose, self._policy)
            if plan is not None:
                self._announce(
                    NegotiationDecision(
                        action="counter",
                        rationale=plan.rationale,
                        counter_proposal=plan.text,
                    ),
                    propose,
                    session,
                )
                return plan.text
        decision = await self._provider.respond(propose, session)
        grounded, _notes = sanitize_pure_decision(
            decision,
            request_text=request_text,
            propose=propose,
            policy=self._policy,
        )
        if grounded is not None:
            self._announce(grounded, propose, session)
            return _render(grounded)
        if self._fallback is not None:
            result = self._fallback(event)
            if isinstance(result, str):
                return result
            return await result
        return ""

    def _announce(
        self,
        decision: NegotiationDecision,
        propose: Any,
        session: Any,
    ) -> None:
        """Record one automatic decision, when the runtime observes decisions."""
        if self._decision_sink is not None:
            self._decision_sink(decision, propose, session)


def build_auto_pure_answer(
    provider: NegotiationDecisionProvider,
    policy: ClientPolicy,
    *,
    request_text: str = "",
    fallback: PureAnswer | None = None,
) -> AutoPureAnswer:
    """Build the automatic pure-A2A answer callback."""
    return AutoPureAnswer(
        provider,
        policy,
        request_text=request_text,
        fallback=fallback,
    )


def _provider_text(provider: Any) -> str:
    """Return the request text a provider carries for the current hop."""
    getter = getattr(provider, "grounding_text", None)
    return getter() if callable(getter) else ""


def _render(decision: NegotiationDecision) -> str:
    """Render one sanitized decision as the vendor's free-text answer."""
    return render_pure_answer(decision)


__all__ = ["AutoPureAnswer", "build_auto_pure_answer"]
