"""Automatic answer of a direct standard-A2A (pure) negotiation.

The direct hop has no OSS Agent in front of it: the client talks to the vendor NMS itself, and the
vendor negotiates in prose. The answer is built in two tiers:

1. a deterministic plan (:mod:`demo.pure_concession`) when the terse question has a value the
   request itself implies (a rate-quality term, a bare rate, or a long-outage concession);
2. the ADK decision agent for everything else, sanitized by
   :func:`protocol.pure_answer_guard.sanitize_pure_decision` and rendered as free text.

A value the client does not own is never invented. The NMS owns the network inventory (regions,
sites, RAT capabilities, frequencies), so when a question asks for one and neither tier can ground
it, the client asks the server to provide the value. The exchange is bounded to
:data:`MAX_SERVER_ASKS` questions; a server that still has not provided the value after that gets an
explicit rejection. Automatic mode has no operator to hand the question to.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

from protocol.client_policy import ClientPolicy
from protocol.decision_provider import NegotiationDecisionProvider
from protocol.grounding import server_target_rates
from protocol.manual_answer import missing_item_labels
from protocol.pure_answer_guard import sanitize_pure_decision
from protocol.pure_client_flow import pure_proposal_view
from shared.models import NegotiationDecision

from demo.pure_concession import plan_pure_answer
from demo.pure_policy import render_pure_answer

PureAnswer = Callable[[Mapping[str, object]], str | Awaitable[str]]

#: Questions the client may answer with "please provide the value" before it rejects.
MAX_SERVER_ASKS: Final[int] = 3

#: Free-text request for a value only the server owns.
ASK_TEMPLATE: Final[str] = (
    "Please provide the available value; the request does not state it ({item})."
)

#: Explicit refusal after the server kept asking without providing the value.
REJECT_TEMPLATE: Final[str] = (
    "Reject: {item}; the server did not provide a usable value."
)


class AutoPureAnswer:
    """Answer pure-A2A questions automatically, asking the server for server-owned values.

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
        max_server_asks: int = MAX_SERVER_ASKS,
    ) -> None:
        """Create one automatic answer.

        Args:
            provider: ADK decision provider used when no deterministic plan applies.
            policy: Client acceptance limits.
            request_text: Operator request used for grounding; the runtime keeps it current.
            max_server_asks: Questions answered with "please provide" before the client rejects.
        """
        self._provider = provider
        self._policy = policy
        self._request_text = request_text
        self._max_server_asks = max(1, max_server_asks)
        self._server_asks = 0
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
        """Keep the proposal observer; every question is announced here."""
        self._proposal_sink = sink

    def set_decision_sink(self, sink: Callable[..., None] | None) -> None:
        """Keep the decision observer for automatic decisions and refusals."""
        self._decision_sink = sink

    async def __call__(self, event: Mapping[str, object]) -> str:
        """Return the free-text answer of one pure-A2A question."""
        propose, session = pure_proposal_view(event)
        if self._proposal_sink is not None:
            self._proposal_sink(propose, session)
        request_text = self._request_text or _provider_text(self._provider)
        if propose.kind in ("feasibility", "target") and not server_target_rates(propose):
            plan = plan_pure_answer(request_text, propose, self._policy)
            if plan is not None:
                self._server_asks = 0
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
            self._server_asks = 0
            self._announce(grounded, propose, session)
            return _render(grounded)
        return self._ask_or_reject(propose, session, decision)

    def _ask_or_reject(
        self,
        propose: Any,
        session: Any,
        decision: NegotiationDecision,
    ) -> str:
        """Ask the server for the missing value, or refuse after the ask budget.

        The NMS owns the network inventory; the client cannot invent a region, site, RAT or
        frequency. In automatic mode there is no operator to transfer the question to, so the
        client asks the server and, after :data:`MAX_SERVER_ASKS` unanswered questions, refuses
        instead of looping until the run timeout.
        """
        item = _missing_item(propose, decision)
        self._server_asks += 1
        if self._server_asks < self._max_server_asks:
            text = ASK_TEMPLATE.format(item=item)
            self._announce(
                NegotiationDecision(action="counter", rationale=text, counter_proposal=text),
                propose,
                session,
            )
            return text
        text = REJECT_TEMPLATE.format(item=item)
        self._announce(
            NegotiationDecision(action="reject", rationale=text), propose, session
        )
        return text

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
    max_server_asks: int = MAX_SERVER_ASKS,
) -> AutoPureAnswer:
    """Build the automatic pure-A2A answer callback."""
    return AutoPureAnswer(
        provider,
        policy,
        request_text=request_text,
        max_server_asks=max_server_asks,
    )


def _missing_item(propose: Any, decision: NegotiationDecision) -> str:
    """Return the item the client needs the server to provide, in human-readable form."""
    labels = missing_item_labels(propose.content)
    if labels:
        return ", ".join(labels)
    rationale = (decision.rationale or "").strip()
    if rationale:
        return rationale[:160]
    return "the value the request does not state"


def _provider_text(provider: Any) -> str:
    """Return the request text a provider carries for the current hop."""
    getter = getattr(provider, "grounding_text", None)
    return getter() if callable(getter) else ""


def _render(decision: NegotiationDecision) -> str:
    """Render one sanitized decision as the vendor's free-text answer."""
    return render_pure_answer(decision)


__all__ = [
    "ASK_TEMPLATE",
    "MAX_SERVER_ASKS",
    "REJECT_TEMPLATE",
    "AutoPureAnswer",
    "build_auto_pure_answer",
]
