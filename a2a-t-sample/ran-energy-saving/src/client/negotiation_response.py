"""Client-side negotiation response seam (INTEGRATION POINT) of the ran-energy-saving demo.

``NegotiationResponseProvider`` is the Protocol the client flow depends on: it is where a
customer's system decides how to answer a negotiation propose. ``ScriptedNegotiationResponseProvider``
is the colocated demo implementation, wrapping the scripted answers of ``client.scenario_data`` (the
data stays there so tests keep importing it unchanged). Replace the demo provider at the assembly
site in ``client.client_main`` with your own implementation — see the integration guide
(``docs/en/negotiation_integration_guide.md`` / ``docs/zh/协商集成指南.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from a2a_t.core.metadata import NegotiationContext

from client.scenario_data import (
    NEGOTIATION_KIND_FEASIBILITY,
    NEGOTIATION_KIND_TARGET,
    negotiation_feasibility_summary,
    negotiation_target_confirmation,
)


@dataclass(frozen=True, slots=True)
class NegotiationProposeView:
    """What the flow observed from one INPUT_REQUIRED propose, handed to the provider.

    The A2A-T wire carries no structured per-type propose content — only the propose's
    ``templateUri`` and its rendered text in the status metadata (plus the session context the
    flow parses separately). The view mirrors exactly that: ``kind`` is the negotiation type the
    flow resolved (the configured flag, else the dispatch on ``template_uri``), ``template_uri``
    is the propose's template URI and ``prompt_text`` the propose's rendered text. The view is
    deliberately lightweight: the flow never parses ``prompt_text``, so a customer implementation
    decides its answer from the kind, the template and its own systems.
    """

    kind: str
    template_uri: str
    prompt_text: str


@dataclass(frozen=True, slots=True)
class NegotiationDecision:
    """The customer's answer to one negotiation propose (the provider's return contract).

    ``conclusion`` selects how the flow continues; the remaining fields are per-conclusion and
    stay ``None`` when they do not apply:

    * ``"accept"`` — the flow builds the round-2 accept from the decision field of the proposed
      type: ``filled_params`` (information: the name->value items the client fills in),
      ``confirmed_intent`` (target) or ``feasibility_summary`` (feasibility). The accept goes
      through the existing accept builders, so the wire behavior is the demo's accept.
    * ``"reject"`` / ``"abort"`` — not wired in this demo: the flow logs the decision and ends the
      round without sending anything (see the integration guide for the SDK reject/abort APIs a
      customer wires to actually emit them). ``reason`` is accepted for reject/abort but unused by
      the demo flow — it becomes the ending content's ``failure_reason`` once you wire the
      reject/abort send yourself (see the integration guide).
    """

    conclusion: Literal["accept", "reject", "abort"]
    filled_params: dict[str, object] | None = None
    confirmed_intent: str | None = None
    feasibility_summary: str | None = None
    reason: str | None = None


@runtime_checkable
class NegotiationResponseProvider(Protocol):
    """INTEGRATION POINT (client): decide how to answer a negotiation propose.

    The client flow calls this seam once the server answers the round-1 task with an
    INPUT_REQUIRED Negotiation-T propose: ``propose`` is the lightweight
    :class:`NegotiationProposeView` observed from the wire (negotiation kind, template URI, rendered
    text) and ``session`` the parsed :class:`NegotiationContext` of the server's session. The
    implementation returns the :class:`NegotiationDecision` — accept (with the per-type answer
    content), reject or abort.

    The DECISION of how to derive the answer is entirely yours (human confirmation, a policy
    engine, an upstream system call); the WIRE MECHANICS stay in the flow: the flow regenerates the
    Task-T prompt, builds the typed accept from your decision, advances the session context and
    sends round 2 over the configured transport.

    ``respond`` is synchronous — the flow calls it at the decision point. A customer implementation
    that needs asynchrony (waiting for a human, calling a remote service) owns that internally and
    blocks the flow until the decision is ready. The demo implementation is
    :class:`ScriptedNegotiationResponseProvider`; the assembly site carrying the ``INTEGRATION
    POINT`` annotation is ``client.client_main.main``.
    """

    def respond(self, propose: NegotiationProposeView, session: NegotiationContext) -> NegotiationDecision: ...


class ScriptedNegotiationResponseProvider:
    """Demo ``NegotiationResponseProvider`` replaying the scripted scenario answers.

    Wraps the legacy values the demo used to consume directly (``negotiation_params`` /
    ``negotiation_answers`` / the configured ``negotiation_kind`` / ``env_path``) and mirrors the
    previous selection logic verbatim: an information propose is accepted with the supplied answers
    as the filled parameters, a target propose with the scenario's confirmed-target sentence and a
    feasibility propose with the scenario's feasibility summary — the scenario accessors keep
    resolving the configured language through ``env_path``. The view's ``kind`` (resolved by the
    flow: the flag, else the propose's ``templateUri``) selects the answer, so the wire dispatch
    works unchanged; ``session`` is unused by the scripted demo (a customer implementation may
    branch on the round/performative).

    The constructor takes exactly the inputs ``client.client_main`` resolves today, so the assembly
    site builds it from the same values it used to pass to the flow.
    """

    def __init__(
        self,
        *,
        negotiation_params: dict[str, object] | None,
        negotiation_answers: dict[str, str] | None,
        negotiation_kind: str | None,
        env_path: Path | None = None,
    ) -> None:
        self._negotiation_params = negotiation_params
        self._negotiation_answers = negotiation_answers
        self._negotiation_kind = negotiation_kind
        self._env_path = env_path

    def respond(self, propose: NegotiationProposeView, session: NegotiationContext) -> NegotiationDecision:
        """Mirror the scripted selection: information -> answers, target / feasibility -> scenario text.

        ``propose`` selects the scripted answer by kind; ``session`` is unused (see the class
        docstring). ``negotiation_params`` is kept for assembly parity — the Task-T prompt refill
        stays a flow-owned wire mechanic fed from the caller, not part of the decision.
        """
        if propose.kind == NEGOTIATION_KIND_TARGET:
            return NegotiationDecision(
                conclusion="accept",
                confirmed_intent=negotiation_target_confirmation(env_path=self._env_path),
            )
        if propose.kind == NEGOTIATION_KIND_FEASIBILITY:
            return NegotiationDecision(
                conclusion="accept",
                feasibility_summary=negotiation_feasibility_summary(env_path=self._env_path),
            )
        filled_params: dict[str, object] = {name: value for name, value in (self._negotiation_answers or {}).items()}
        return NegotiationDecision(conclusion="accept", filled_params=filled_params)
