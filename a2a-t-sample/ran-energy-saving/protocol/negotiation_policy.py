"""Deterministic server-side negotiation policy of the RAN energy-saving sample.

The policy is a pure function: :func:`decide` maps the current negotiation state to a
:class:`PolicyDecision`. It never calls an LLM and never sends anything; the gate turns the decision
into SDK-generated protocol content. This keeps protocol correctness (rounds, performatives,
terminal actions) in deterministic code and leaves the ADK agent only the accepted task.

Server-side state machine (design §6.2/§6.3):

* the server is the initiator and is the only side that advances the round;
* a client ``Accept`` hands the agreed Task-T prompt to the ADK executor (``execute``);
* a client ``Reject``/``Abort`` is terminal (``end``);
* a client counter-``Propose`` advances to the next round (``propose``) while the round budget is
  not exhausted; at ``round == maxRounds`` without convergence the server aborts (``abort``) and
  keeps the current round;
* the server never emits ``Accept``: it expresses agreement by publishing a final acceptable
  ``Propose`` that the client can accept.

Sample convention: a client counter proposal may carry ``metadata["sampleProposal"]`` with
``{cellId, window, targetRate}``. The policy evaluates it with :func:`demo.ran_data.evaluate_plan`,
so the demo behaves deterministically. The field is not part of A2A-T; without it the policy advances
through the preset revision sequence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal, Protocol

from a2a_t.core.errors.exceptions import SlotValidationError
from a2a_t.core.metadata import NegotiationPerformative
from a2a_t.core.standard_templates import (
    FEASIBILITY_NEGOTIATION_PROPOSE_URI,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
    NEGOTIATION_ABORT_URI,
    TARGET_NEGOTIATION_PROPOSE_URI,
)
from shared.run_constants import MAX_ROUNDS


#: Negotiation type the server opened.
class NegotiationKind(StrEnum):
    """Server-side negotiation type family."""

    INFORMATION = "information"
    TARGET = "target"
    FEASIBILITY = "feasibility"


#: Policy action of one decision.
PolicyAction = Literal["propose", "abort", "execute", "end"]

#: Sample convention mapping the first Task-T validation error code to the negotiation kind.
NEGOTIATION_KIND_BY_ERROR_CODE: Final[dict[str, NegotiationKind]] = {
    "content.param_missing": NegotiationKind.INFORMATION,
    # A missing field inside one entry is still missing information: the operator can supply it,
    # so it must reach the server instead of being blocked client-side.
    "content.entry_field_missing": NegotiationKind.INFORMATION,
    "content.value_not_allowed": NegotiationKind.TARGET,
    # A conflict in the time-segmented target is the target-negotiation case (overlapping
    # windows). Feasibility conflicts are distinguished by their facts in
    # :func:`negotiation_kind_for_errors`.
    "content.semantic_conflict": NegotiationKind.TARGET,
    "content.rule_violation": NegotiationKind.FEASIBILITY,
}

#: Facts that identify a semantic conflict as the feasibility/outage case rather than an
#: overlapping target. Reasons are model-generated in the process language, so both languages
#: are matched.
_FEASIBILITY_CONFLICT_MARKERS: Final[tuple[str, ...]] = (
    "outage",
    "power",
    "background event",
    "停电",
    "背景事件",
    "site",
    "站点",
)

#: Negotiation-T propose template URI per kind.
PROPOSE_TEMPLATE_URI_BY_KIND: Final[dict[NegotiationKind, str]] = {
    NegotiationKind.INFORMATION: INFORMATION_NEGOTIATION_PROPOSE_URI,
    NegotiationKind.TARGET: TARGET_NEGOTIATION_PROPOSE_URI,
    NegotiationKind.FEASIBILITY: FEASIBILITY_NEGOTIATION_PROPOSE_URI,
}

#: Common abort template URI.
ABORT_TEMPLATE_URI: Final[str] = NEGOTIATION_ABORT_URI

#: Template URI segment per negotiation kind, used to recover the kind from an inbound propose.
_KIND_BY_URI_SEGMENT: Final[dict[str, NegotiationKind]] = {
    "information-negotiation": NegotiationKind.INFORMATION,
    "target-negotiation": NegotiationKind.TARGET,
    "feasibility-negotiation": NegotiationKind.FEASIBILITY,
    # The vendor NMS abbreviates the information kind. Both spellings name the same negotiation,
    # so an NMS proposal must not be rejected as "not a sample negotiation template".
    "info-negotiation": NegotiationKind.INFORMATION,
}


@dataclass(frozen=True)
class SampleProposal:
    """One deterministic plan a client counter proposal carries (sample-only convention).

    Attributes:
        cell_id: RAN cell the plan applies to.
        window: energy-saving window (``HH:MM-HH:MM``).
        target_rate: energy-saving target rate as a percentage string.
    """

    cell_id: str
    window: str
    target_rate: str

    @classmethod
    def try_parse(cls, raw: object) -> SampleProposal | None:
        """Parse a ``{cellId, window, targetRate}`` mapping, tolerating snake_case keys."""
        if not isinstance(raw, Mapping):
            return None
        cell_id = raw.get("cellId", raw.get("cell_id"))
        window = raw.get("window")
        target_rate = raw.get("targetRate", raw.get("target_rate"))
        if not isinstance(cell_id, str) or not cell_id.strip():
            return None
        if not isinstance(window, str) or not window.strip():
            return None
        if isinstance(target_rate, bool) or not isinstance(target_rate, (str, int, float)):
            return None
        rendered_rate = str(target_rate).strip()
        if not rendered_rate:
            return None
        return cls(cell_id=cell_id.strip(), window=window.strip(), target_rate=rendered_rate)


@dataclass(frozen=True)
class PlanAssessment:
    """The deployment's verdict on one counter-proposed plan.

    Attributes:
        feasible: whether the plan can be applied.
        reasons: human-readable rejection reasons, empty when feasible.
        revised: the plan the server proposes instead; the requested plan when feasible.
    """

    feasible: bool
    reasons: str
    revised: SampleProposal


class PlanEvaluator(Protocol):
    """Business evaluation of a sample counter proposal, supplied by the NMS integration."""

    def evaluate(self, plan: SampleProposal) -> PlanAssessment:
        """Assess one plan and derive the server's revised proposal."""
        ...

    def preset(self, round_number: int) -> SampleProposal:
        """Return the server's preset plan for a round without a client plan."""
        ...


@dataclass(frozen=True)
class InboundDecision:
    """The client's inbound performative and its optional sample counter proposal.

    Attributes:
        performative: performative of the inbound message.
        sample_proposal: parsed sample-only counter proposal, when carried.
    """

    performative: NegotiationPerformative
    sample_proposal: SampleProposal | None = None


@dataclass(frozen=True)
class NegotiationState:
    """Input of one policy decision.

    Attributes:
        kind: negotiation type family.
        round: current round carried by the inbound message (1-based).
        max_rounds: round budget of the negotiation.
        inbound: inbound performative; ``None`` for the initial Task-T validation failure.
        missing_items: slot names reported by a failed Task-T validation (information kind).
        proposal_text: business text the deployment wants in the first proposal, when it has one.
        options: sample-only choices the client may select (for example served areas).
    """

    kind: NegotiationKind
    round: int
    max_rounds: int = MAX_ROUNDS
    inbound: InboundDecision | None = None
    missing_items: tuple[str, ...] = ()
    proposal_text: str = ""
    options: Mapping[str, object] | None = None


@dataclass(frozen=True)
class ProposalContent:
    """Content of the next server proposal.

    Attributes:
        kind: negotiation type family.
        round: round the proposal is emitted at.
        template_uri: Negotiation-T propose template URI.
        text: natural-language input rendered into the protocol content by the SDK.
        sample_proposal: plan the proposal is based on, when known.
        options: sample-only choices the client may select (for example served areas).
    """

    kind: NegotiationKind
    round: int
    template_uri: str
    text: str
    sample_proposal: SampleProposal | None = None
    options: Mapping[str, object] | None = None


@dataclass(frozen=True)
class AbortContent:
    """Content of the server abort message.

    Attributes:
        round: round the abort is emitted at (kept at the last round).
        template_uri: common abort template URI.
        text: natural-language termination reason.
        reason: short machine-readable reason for logs and tests.
    """

    round: int
    template_uri: str
    text: str
    reason: str


@dataclass(frozen=True)
class PolicyDecision:
    """One deterministic policy decision.

    Attributes:
        action: what the gate must do next.
        content: proposal or abort content, ``None`` for ``execute``/``end``.
        rationale: human-readable reason for logs and the transcript.
    """

    action: PolicyAction
    content: ProposalContent | AbortContent | None
    rationale: str


def negotiation_kind_for_error_code(code: str) -> NegotiationKind | None:
    """Map one Task-T validation error code to the negotiation kind it triggers."""
    return NEGOTIATION_KIND_BY_ERROR_CODE.get(code)


def negotiation_kind_for_errors(
    errors: Sequence[SlotValidationError] | None,
) -> NegotiationKind | None:
    """Map a failed Task-T validation to the negotiation kind its errors call for.

    ``content.semantic_conflict`` is the only model-judged entry: it covers both overlapping target
    windows and feasibility conflicts, and its facts decide which negotiation applies. The other
    codes state a concrete fact about the Task-T (a required value is absent, a value is not
    allowed, a rule is violated), so they outrank a conflict regardless of the order the model
    lists them in. A live model can report "the target overlaps" before "the target is missing";
    the missing value must still open the information negotiation. Errors without a mapping are
    skipped, so a leading format/unknown entry never hides a negotiable failure.
    """
    if not errors:
        return None
    conflict: SlotValidationError | None = None
    for error in errors:
        code = str(error.code)
        if code == "content.semantic_conflict":
            conflict = conflict or error
            continue
        kind = NEGOTIATION_KIND_BY_ERROR_CODE.get(code)
        if kind is not None:
            return kind
    if conflict is not None:
        return _semantic_conflict_kind(conflict)
    return None


def _semantic_conflict_kind(error: SlotValidationError) -> NegotiationKind:
    """Classify one ``content.semantic_conflict`` as target or feasibility from its facts."""
    slot_name = error.slot_name.strip().lower()
    facts = error.facts or {}
    reason = str(facts.get("reason", "")).lower()
    if slot_name in {"backgroundevent", "sitelist"}:
        return NegotiationKind.FEASIBILITY
    if any(marker in reason for marker in _FEASIBILITY_CONFLICT_MARKERS):
        return NegotiationKind.FEASIBILITY
    return NegotiationKind.TARGET


def negotiation_kind_for_template_uri(template_uri: str | None) -> NegotiationKind | None:
    """Recover the negotiation kind from a Negotiation-T template URI.

    Returns:
        The kind for a typed propose/accept-reject URI, or ``None`` for the common abort template
        and for URIs outside the sample's negotiation templates.
    """
    if not isinstance(template_uri, str):
        return None
    parts = template_uri.split("/")
    if len(parts) < 3 or parts[0] != "Negotiation-T":
        return None
    return _KIND_BY_URI_SEGMENT.get(parts[1])


#: Wire spelling of the type in a vendor ``responseMetadata.negotiationType``.
#: The vendor's negotiation generator spells the target kind ``CLARIFICATION`` internally and
#: resolves it to the target-negotiation template URI; accept both spellings as the same kind.
_KIND_BY_NEGOTIATION_TYPE: Final[dict[str, NegotiationKind]] = {
    "information": NegotiationKind.INFORMATION,
    "target": NegotiationKind.TARGET,
    "clarification": NegotiationKind.TARGET,
    "feasibility": NegotiationKind.FEASIBILITY,
}


def negotiation_kind_for_negotiation_type(value: object) -> NegotiationKind | None:
    """Return the kind named by a vendor ``responseMetadata.negotiationType``.

    The vendor NMS renders its Negotiation-T body as a JSON string whose nested
    ``responseMetadata`` names the negotiation type directly. That field outranks any inference
    from a body heading or from a template path the client does not share.

    Args:
        value: the raw ``negotiationType`` field of the vendor metadata.

    Returns:
        The kind it names, or ``None`` when the field is absent or unknown. ``None`` means
        "do not guess"; the caller keeps failing loudly.
    """
    if not isinstance(value, str):
        return None
    return _KIND_BY_NEGOTIATION_TYPE.get(value.strip().lower())


#: Heading title of each propose template, in both languages the sample renders. The vendor NMS
#: omits ``templateUri`` on the proposal that continues a negotiation, so the body heading is the
#: only typed signal left.
_PROPOSE_HEADING_KIND_BY_MARKER: Final[tuple[tuple[str, NegotiationKind], ...]] = (
    ("information negotiation", NegotiationKind.INFORMATION),
    ("信息协商", NegotiationKind.INFORMATION),
    ("target negotiation", NegotiationKind.TARGET),
    ("目标协商", NegotiationKind.TARGET),
    ("feasibility negotiation", NegotiationKind.FEASIBILITY),
    ("可行性协商", NegotiationKind.FEASIBILITY),
)

#: The markdown heading that opens one propose body.
_PROPOSE_HEADING_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\s{0,3}#{1,6}\s*(?P<title>[^\n#]+?)\s*$", re.MULTILINE
)


def negotiation_kind_for_propose_content(content: str | None) -> NegotiationKind | None:
    """Recover the negotiation kind from the heading that opens one propose body.

    A conformant A2A-T propose carries ``templateUri``; the vendor NMS omits it on the proposal
    that continues a negotiation, leaving the rendered body as the only kind signal. Every propose
    template opens with one typed heading (``## Information Negotiation`` / ``## 信息协商``, ...).

    Args:
        content: rendered Negotiation-T body of an inbound propose, or ``None``.

    Returns:
        The kind named by the opening heading, or ``None`` when the body carries no heading of a
        known negotiation type. ``None`` means "do not guess": the caller keeps failing loudly.
    """
    if not isinstance(content, str):
        return None
    heading = _PROPOSE_HEADING_PATTERN.search(content)
    if heading is None:
        return None
    title = heading.group("title").strip().lower()
    for marker, kind in _PROPOSE_HEADING_KIND_BY_MARKER:
        if marker in title:
            return kind
    return None


def decide(
    state: NegotiationState,
    *,
    evaluator: PlanEvaluator | None = None,
) -> PolicyDecision:
    """Return the deterministic server decision for the given negotiation state.

    Args:
        state: current negotiation state.
        evaluator: business evaluation of a counter-proposed plan; supplied by the NMS
            integration. Without one, a counter proposal is answered by asking for a concrete
            plan instead of judging feasibility.

    Returns:
        The next action: ``propose`` on the initial failure and on an acceptable counter,
        ``abort`` when the round budget is exhausted, ``execute`` on accept and ``end`` on
        reject/abort.
    """
    inbound = state.inbound
    if inbound is None:
        return _initial_decision(state)
    if inbound.performative is NegotiationPerformative.ACCEPT:
        return PolicyDecision("execute", None, "The client accepted the proposal.")
    if inbound.performative is NegotiationPerformative.REJECT:
        return PolicyDecision("end", None, "The client rejected the proposal; the negotiation ends.")
    if inbound.performative is NegotiationPerformative.ABORT:
        return PolicyDecision("end", None, "The client aborted the negotiation; the negotiation ends.")
    return _counter_decision(state, evaluator)


def _initial_decision(state: NegotiationState) -> PolicyDecision:
    """Return the first server proposal triggered by a failed Task-T validation."""
    if state.round >= state.max_rounds:
        return _abort_decision(
            state,
            reason="initial-validation",
            text=(
                f"The {state.kind.value} negotiation reached the maximum number of rounds "
                f"({state.max_rounds}) without agreement; the RAN energy saving task cannot start."
            ),
        )
    if state.kind is NegotiationKind.INFORMATION:
        items = ", ".join(state.missing_items) if state.missing_items else "the required task parameters"
        text = state.proposal_text or (
            "Additional information is required before the RAN energy saving task can start. "
            f"Please provide the following missing items: {items}."
        )
    elif state.kind is NegotiationKind.TARGET:
        text = (
            "The target negotiation needs clarification: the time-segment-based guaranteed "
            "throughput target contains overlapping time segments, so the guaranteed rate for the "
            "overlapping period cannot be determined uniquely. Please provide a non-overlapping "
            "time-segment target, or accept a revised target."
        )
    else:
        text = (
            "The feasibility negotiation found that the requested guaranteed throughput target "
            "cannot be satisfied together with the declared background event (power outage). "
            "Please accept an alternative: lower the guaranteed rate during the outage window, or "
            "shorten the required outage duration."
        )
    return PolicyDecision(
        "propose",
        ProposalContent(
            kind=state.kind,
            round=1,
            template_uri=PROPOSE_TEMPLATE_URI_BY_KIND[state.kind],
            text=text,
            options=state.options,
        ),
        f"Task-T validation requires an {state.kind.value} negotiation.",
    )


def _counter_decision(
    state: NegotiationState,
    evaluator: PlanEvaluator | None,
) -> PolicyDecision:
    """Return the server answer to a client counter proposal."""
    # At the last round there is no room for the server's confirming Propose: the server would
    # have to advance past ``maxRounds``. It aborts at the current round instead, even when the
    # counter proposal itself became feasible.
    if state.round >= state.max_rounds:
        return _abort_decision(
            state,
            reason="max-rounds",
            text=(
                f"The {state.kind.value} negotiation reached the maximum number of rounds "
                f"({state.max_rounds}) without convergence; the RAN energy saving task cannot start."
            ),
        )

    next_round = state.round + 1
    sample = state.inbound.sample_proposal if state.inbound is not None else None
    if state.kind is NegotiationKind.INFORMATION:
        items = ", ".join(state.missing_items) if state.missing_items else "the required task parameters"
        text = (
            "The counter proposal does not provide the missing information. Please provide the "
            f"following items before the energy saving task can start: {items}."
        )
        rationale = "The information negotiation still misses required task parameters."
    elif sample is not None and evaluator is not None:
        assessment = evaluator.evaluate(sample)
        if assessment.feasible:
            text = (
                f"The plan proposed in round {state.round} is acceptable: cell {sample.cell_id}, "
                f"window {sample.window}, target rate {sample.target_rate}%. The server publishes it "
                f"as the final agreement in round {next_round}; please accept it."
            )
            rationale = "The client counter proposal is feasible, so the server publishes the final proposal."
        else:
            revised = assessment.revised
            text = (
                f"The plan proposed in round {state.round} is not feasible ({assessment.reasons}). "
                f"Revised proposal for round {next_round}: cell {revised.cell_id}, window "
                f"{revised.window}, target rate {revised.target_rate}%. Please accept this revised "
                "plan."
            )
            rationale = "The client counter proposal is infeasible, so the server proposes a revised plan."
    elif evaluator is not None:
        preset = evaluator.preset(next_round)
        text = (
            f"Revised proposal for round {next_round}: cell {preset.cell_id}, window "
            f"{preset.window}, target rate {preset.target_rate}%. Please accept this revised plan."
        )
        rationale = "No sample counter proposal was carried, so the server advances its preset sequence."
    else:
        text = (
            "The counter proposal cannot be evaluated: no plan evaluator is configured. Provide a "
            "concrete plan (cell, window and target rate) so the server can answer."
        )
        rationale = "No plan evaluator is configured; the server asks for a concrete plan."
    return PolicyDecision(
        "propose",
        ProposalContent(
            kind=state.kind,
            round=next_round,
            template_uri=PROPOSE_TEMPLATE_URI_BY_KIND[state.kind],
            text=text,
            sample_proposal=sample,
        ),
        rationale,
    )


def _abort_decision(state: NegotiationState, *, reason: str, text: str) -> PolicyDecision:
    """Return the abort decision at the current round (never advancing past ``maxRounds``)."""
    return PolicyDecision(
        "abort",
        AbortContent(
            round=state.round,
            template_uri=ABORT_TEMPLATE_URI,
            text=text,
            reason=reason,
        ),
        f"The negotiation cannot converge within {state.max_rounds} rounds.",
    )
