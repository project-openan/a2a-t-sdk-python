"""Grounding of an automatic pure-A2A answer.

The pure A2A hop has no Negotiation-T metadata: the decision agent's structured answer is rendered
back into one plain-text message. There is no place to put a verifier after the fact, so the
decision is sanitized before it is rendered:

* a value must appear in the operator's original request or in an affirmative sentence of the
  vendor's question; anything else is dropped instead of being sent as if the operator had typed it;
* a counter is not an answer to an information question (the vendor asked for a value, not a plan);
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
from protocol.grounding import normalize, server_contains, server_entity_values
from protocol.manual_answer import ungrounded_answer_fields

#: One plain rate in a value.
_RATE: Final[re.Pattern[str]] = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*Mbps", re.IGNORECASE)


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

    filled = {
        name: value
        for name, value in (decision.filled_params or {}).items()
        if value is not None and not (isinstance(value, str) and not value.strip())
    }
    if not filled:
        return None, ["the decision carries no value to answer the question"]

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
