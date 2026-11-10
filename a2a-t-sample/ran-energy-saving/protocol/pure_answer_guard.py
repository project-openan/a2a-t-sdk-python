"""Grounding of an automatic pure-A2A answer.

The pure A2A hop has no Negotiation-T metadata: the decision agent's structured answer is rendered
back into one plain-text message. There is no place to put a verifier after the fact, so the
decision is sanitized before it is rendered:

* a value must appear in the operator's original request or in an affirmative sentence of the
  vendor's question; anything else is dropped instead of being sent as if the operator had typed it;
* a counter is not an answer to an information question (the vendor asked for a value, not a plan).
  On a feasibility/confirmation question it must state a concrete rate that concedes: no rate above
  the request's own maximum, and none below the client's floor;
* a bare "accept" is an answer only when the question itself states a concrete plan. A terse
  vendor sentence such as ``Not feasible, please confirm`` offers nothing to agree to, so the
  client must not send an empty affirmative; the question is relayed to the caller instead;
* a rejection/abort has no value to carry, and the pure hop has no reject performative, so an
  automatic decision that cannot answer sends nothing rather than a rationale the vendor would
  read as its answer;
* the client's throughput floor still applies to the values the answer carries.

The caller renders the sanitized decision with :func:`demo.pure_policy.render_pure_answer`.
"""

from __future__ import annotations

import re
from typing import Final

from shared.models import NegotiationDecision, NegotiationProposeView

from protocol.client_policy import ClientPolicy
from protocol.grounding import (
    normalize,
    server_contains,
    server_entity_values,
    server_target_rates,
)
from protocol.manual_answer import asks_for_confirmation, ungrounded_answer_fields

#: One plain rate in a value.
_RATE: Final[re.Pattern[str]] = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*Mbps", re.IGNORECASE)

def _asks_for_confirmation(propose: NegotiationProposeView) -> bool:
    """Return whether the proposal asks the client to confirm a plan."""
    return asks_for_confirmation(propose.content)


def _rate_values(text: str) -> list[float]:
    """Return every Mbps value stated in one text, in order."""
    return [float(match.group(1)) for match in _RATE.finditer(text)]


def _counter_problem(
    counter: str,
    request_text: str,
    policy: ClientPolicy,
) -> str | None:
    """Return why one free-text counter cannot be sent, or ``None`` when it is acceptable.

    A pure-A2A feasibility answer is prose, so the only machine-checkable constraint is the rate:
    it must state a concrete value, it may only concede (never exceed the request's own maximum),
    and it must respect the client floor.
    """
    rates = _rate_values(counter)
    if not rates:
        return "it states no rate"
    request_rates = _rate_values(request_text)
    if request_rates and max(rates) > max(request_rates):
        return "it raises the rate above the original request"
    floor = policy.min_throughput_mbps
    if floor is not None and min(rates) < floor:
        return f"it goes below the client minimum of {floor:g} Mbps"
    return None


def sanitize_pure_decision(
    decision: NegotiationDecision,
    *,
    request_text: str,
    propose: NegotiationProposeView,
    policy: ClientPolicy,
) -> tuple[NegotiationDecision | None, list[str]]:
    """Return the decision the pure hop may send, or ``None`` when it cannot answer.

    Args:
        decision: Automatic decision produced by the decision agent.
        request_text: The operator's original request of this run.
        propose: Proposal view built from the vendor's plain question.
        policy: Acceptance limits of this client.

    Returns:
        The sanitized decision (or ``None`` when the client must not answer) and the notes for the
        trace/log.
    """
    notes: list[str] = []
    if decision.action in ("reject", "abort"):
        return None, [
            f"the model returned {decision.action}; a pure A2A question has no reject channel, so "
            "the client sends no answer"
        ]
    if decision.action == "counter" and propose.kind == "information":
        return None, ["a counter is not an answer to an information question"]
    if decision.action == "counter":
        counter = decision.counter_proposal
        if isinstance(counter, str) and counter.strip():
            problem = _counter_problem(counter, request_text, policy)
            if problem is None:
                return decision, notes
            notes.append(f"dropped the counter proposal ({problem})")
            decision = decision.model_copy(update={"counter_proposal": None})

    filled = {
        name: value
        for name, value in (decision.filled_params or {}).items()
        if value is not None and not (isinstance(value, str) and not value.strip())
    }
    if not filled:
        if decision.action == "accept" and _asks_for_confirmation(propose):
            if server_target_rates(propose):
                # The question states the revised plan itself ("reduce ... to 2Mbps"), so a plain
                # affirmative is the complete answer. Render the deterministic affirmative instead
                # of the model's rationale so a negative sentence cannot travel as the agreement.
                return decision.model_copy(update={"filled_params": None, "rationale": ""}), [
                    "an affirmative answer to a confirmation question carries no Task-T value"
                ]
            # "Not feasible, please confirm" names no value and no alternative: there is nothing to
            # agree to. Returning nothing relays the question to the caller (the tester's scripted
            # answer or the operator's card) instead of sending a useless "Accepted. Please proceed."
            return None, [
                "the confirmation question states no concrete plan; the question is relayed to the "
                "caller instead of being accepted without a value"
            ]
        return None, [*notes, "the decision carries no value to answer the question"]

    unsupported = set(ungrounded_answer_fields(request_text, filled))
    offered = server_entity_values(propose)
    for name in list(unsupported):
        value = filled[name]
        if normalize(value) in offered.get(name, set()) or server_contains(propose, value):
            unsupported.discard(name)
    for name in sorted(unsupported):
        notes.append(f"dropped {name}={filled.pop(name)!r}: neither the request nor the question states it")

    floor = policy.min_throughput_mbps
    target = filled.get("guaranteedThroughputTarget")
    if floor is not None and isinstance(target, str):
        rates = [float(match.group(1)) for match in _RATE.finditer(target)]
        if rates and min(rates) < floor:
            notes.append(
                f"dropped guaranteedThroughputTarget={target!r}: below the client minimum of "
                f"{floor:g} Mbps"
            )
            filled.pop("guaranteedThroughputTarget", None)

    if not filled:
        return None, [*notes, "no grounded value is left; the client sends no answer"]

    return decision.model_copy(update={"filled_params": filled}), notes


__all__ = ["sanitize_pure_decision"]
