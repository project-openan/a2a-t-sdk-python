"""Structured models exchanged between the sample's ADK agents and protocol layers.

``NegotiationDecision`` is the client agent's ``output_schema``: the LLM only states an intent and
its rationale, while the deterministic client flow turns that intent into protocol content. The
``NegotiationProposeView`` is the read-only projection of an inbound server ``Propose`` handed to a
decision provider, so providers never parse raw A2A-T metadata themselves.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: Decision intents the client agent may return.
NegotiationAction = Literal["accept", "reject", "abort", "counter"]


class NegotiationDecision(BaseModel):
    """One client negotiation decision produced by an ADK agent or a manual/scripted provider.

    Attributes:
        action: Intent of the decision.
        rationale: Human-readable reason shown in the CLI, the Web UI, and the transcript.
        filled_params: Task-T parameters to fill on ``accept`` (missing-information negotiation).
        counter_proposal: Natural-language counter proposal on ``counter``; the flow layer turns it
            into protocol content with the SDK ``fromText`` generator.
        counter_params: On ``counter``, the sample-only ``{cellId, window, targetRate}`` plan the
            flow attaches as ``metadata["sampleProposal"]`` so the server policy can evaluate the
            counter proposal deterministically.
        confirmed_intent: On ``accept``, the task intent the client confirms, when the negotiation
            type requires one.
        feasibility_summary: On ``accept``, the client's feasibility summary, when the negotiation
            type requires one.
    """

    action: NegotiationAction
    rationale: str
    filled_params: dict[str, object] | None = None
    counter_proposal: str | None = None
    counter_params: dict[str, object] | None = None
    confirmed_intent: str | None = None
    feasibility_summary: str | None = None


class NegotiationAreaOption(BaseModel):
    """One selectable area on an information proposal.

    Attributes:
        value: Canonical English name of the area.
        labels: Localized canonical names.
        aliases: Other accepted spellings.
        score: Fuzzy-match score in ``[0, 1]``.
        recommended: Whether the server ranks this area as the closest match.
    """

    value: str
    labels: dict[str, str] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    score: float = 0.0
    recommended: bool = False

    def label(self, language: str) -> str:
        """Return the option label in ``language``, falling back to the canonical value."""
        return self.labels.get(language, self.value)


class NegotiationOptions(BaseModel):
    """Sample-only choices attached to one inbound proposal.

    Attributes:
        field: Task-T parameter the choices fill.
        requested: Value the client originally requested.
        keep: Task-T parameters the choice leaves unchanged.
        options: Selectable values, best match first.
    """

    field: str = "region"
    requested: str | None = None
    keep: list[str] = Field(default_factory=list)
    options: list[NegotiationAreaOption] = Field(default_factory=list)


class NegotiationProposeView(BaseModel):
    """Provider-facing view of one inbound ``Propose`` message.

    Attributes:
        negotiation_id: Negotiation session identifier (``negotiationContext.id``).
        kind: Negotiation type (``information``, ``target``, or ``feasibility``).
        round: Current 1-based negotiation round.
        max_rounds: Round budget of the session.
        content: Rendered Negotiation-T text of the proposal.
        performative: Performative of the inbound message, always ``PROPOSE`` in this view.
        template_uri: Negotiation-T template URI carried by the message.
        context_id: A2A context id the negotiation belongs to, when known.
        parameters: Proposal parameters, when the server sent structured content.
        options: Sample-only choices the client may select (for example served areas).
    """

    negotiation_id: str
    kind: str
    round: int = Field(ge=1)
    max_rounds: int = Field(ge=1)
    content: str
    performative: str = "PROPOSE"
    template_uri: str | None = None
    context_id: str | None = None
    parameters: dict[str, object] | None = None
    options: NegotiationOptions | None = None
