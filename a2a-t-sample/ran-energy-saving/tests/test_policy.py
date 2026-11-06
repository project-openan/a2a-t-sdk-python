"""Tests for the deterministic server negotiation policy."""

from __future__ import annotations

from functools import partial

import pytest
from a2a_t.core.errors.exceptions import SlotValidationError
from a2a_t.core.metadata import NegotiationPerformative
from a2a_t.core.standard_templates import (
    FEASIBILITY_NEGOTIATION_PROPOSE_URI,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
    NEGOTIATION_ABORT_URI,
    TARGET_NEGOTIATION_PROPOSE_URI,
)
from demo.plan_evaluator import DEFAULT_PLAN, RanDataPlanEvaluator
from protocol.negotiation_policy import (
    AbortContent,
    InboundDecision,
    NegotiationKind,
    NegotiationState,
    PolicyDecision,
    ProposalContent,
    SampleProposal,
    negotiation_kind_for_error_code,
    negotiation_kind_for_errors,
    negotiation_kind_for_template_uri,
)
from protocol.negotiation_policy import (
    decide as _decide,
)

decide = partial(_decide, evaluator=RanDataPlanEvaluator())

ACCEPT = InboundDecision(performative=NegotiationPerformative.ACCEPT)
REJECT = InboundDecision(performative=NegotiationPerformative.REJECT)
CLIENT_ABORT = InboundDecision(performative=NegotiationPerformative.ABORT)


def _counter(sample: SampleProposal | None = None) -> InboundDecision:
    return InboundDecision(performative=NegotiationPerformative.PROPOSE, sample_proposal=sample)


def _state(
    kind: NegotiationKind,
    *,
    round_number: int = 1,
    inbound: InboundDecision | None = None,
    missing: tuple[str, ...] = (),
) -> NegotiationState:
    return NegotiationState(
        kind=kind,
        round=round_number,
        max_rounds=3,
        inbound=inbound,
        missing_items=missing,
    )


def _proposal(decision: PolicyDecision) -> ProposalContent:
    assert decision.action == "propose"
    assert isinstance(decision.content, ProposalContent)
    return decision.content


def _abort(decision: PolicyDecision) -> AbortContent:
    assert decision.action == "abort"
    assert isinstance(decision.content, AbortContent)
    return decision.content


@pytest.mark.parametrize(
    ("kind", "missing", "expected_uri"),
    [
        (NegotiationKind.INFORMATION, ("region",), INFORMATION_NEGOTIATION_PROPOSE_URI),
        (NegotiationKind.TARGET, (), TARGET_NEGOTIATION_PROPOSE_URI),
        (NegotiationKind.FEASIBILITY, (), FEASIBILITY_NEGOTIATION_PROPOSE_URI),
    ],
)
def test_initial_validation_failure_opens_round_one(
    kind: NegotiationKind, missing: tuple[str, ...], expected_uri: str
) -> None:
    content = _proposal(decide(_state(kind, missing=missing)))

    assert content.round == 1
    assert content.kind is kind
    assert content.template_uri == expected_uri
    assert content.text


def test_information_propose_lists_the_missing_items() -> None:
    content = _proposal(decide(_state(NegotiationKind.INFORMATION, missing=("region", "siteList"))))

    assert "region" in content.text
    assert "siteList" in content.text


@pytest.mark.parametrize("kind", list(NegotiationKind))
def test_client_accept_executes_the_task(kind: NegotiationKind) -> None:
    decision = decide(_state(kind, inbound=ACCEPT))

    assert decision.action == "execute"
    assert decision.content is None


@pytest.mark.parametrize("kind", list(NegotiationKind))
def test_client_reject_ends_the_negotiation(kind: NegotiationKind) -> None:
    decision = decide(_state(kind, inbound=REJECT))

    assert decision.action == "end"
    assert decision.content is None


@pytest.mark.parametrize("kind", list(NegotiationKind))
def test_client_abort_ends_the_negotiation(kind: NegotiationKind) -> None:
    decision = decide(_state(kind, inbound=CLIENT_ABORT))

    assert decision.action == "end"
    assert decision.content is None


@pytest.mark.parametrize(
    ("round_number", "sample", "expected_round"),
    [
        (1, SampleProposal("cell-001", "17:30~23:00", "20"), 2),
        (2, SampleProposal("cell-001", "17:30~23:00", "20"), 3),
    ],
)
def test_counter_propose_advances_the_server_round(
    round_number: int, sample: SampleProposal, expected_round: int
) -> None:
    content = _proposal(decide(_state(NegotiationKind.TARGET, round_number=round_number, inbound=_counter(sample))))

    assert content.round == expected_round
    assert content.template_uri == TARGET_NEGOTIATION_PROPOSE_URI


def test_infeasible_counter_propose_is_revised_to_a_feasible_plan() -> None:
    sample = SampleProposal("cell-001", "17:30~23:00", "20")

    content = _proposal(decide(_state(NegotiationKind.TARGET, inbound=_counter(sample))))

    assert content.round == 2
    assert "not feasible" in content.text
    assert "00:00~06:00" in content.text
    assert content.sample_proposal == sample


def test_feasible_counter_propose_is_published_as_the_final_proposal() -> None:
    sample = SampleProposal("cell-001", "00:00~06:00", "30")

    content = _proposal(decide(_state(NegotiationKind.TARGET, inbound=_counter(sample))))

    assert content.round == 2
    assert "acceptable" in content.text
    assert content.sample_proposal == sample


@pytest.mark.parametrize("round_number", [1, 2])
def test_counter_propose_without_a_sample_advances_the_preset_sequence(round_number: int) -> None:
    content = _proposal(decide(_state(NegotiationKind.FEASIBILITY, round_number=round_number, inbound=_counter())))

    assert content.round == round_number + 1
    assert content.kind is NegotiationKind.FEASIBILITY
    assert "Revised proposal" in content.text


@pytest.mark.parametrize("kind", [NegotiationKind.TARGET, NegotiationKind.FEASIBILITY])
def test_counter_propose_at_max_rounds_aborts_and_keeps_the_round(kind: NegotiationKind) -> None:
    sample = SampleProposal("cell-001", "17:30~23:00", "20")

    content = _abort(decide(_state(kind, round_number=3, inbound=_counter(sample))))

    assert content.round == 3
    assert content.template_uri == NEGOTIATION_ABORT_URI
    assert content.reason == "max-rounds"


def test_information_counter_propose_still_lists_the_missing_items() -> None:
    content = _proposal(decide(_state(NegotiationKind.INFORMATION, inbound=_counter(), missing=("region",))))

    assert content.round == 2
    assert "region" in content.text


def test_information_counter_propose_at_max_rounds_aborts() -> None:
    content = _abort(decide(_state(NegotiationKind.INFORMATION, round_number=3, inbound=_counter())))

    assert content.round == 3
    assert content.template_uri == NEGOTIATION_ABORT_URI


def test_template_and_error_code_mapping() -> None:
    assert negotiation_kind_for_error_code("content.param_missing") is NegotiationKind.INFORMATION
    assert negotiation_kind_for_error_code("content.entry_field_missing") is NegotiationKind.INFORMATION
    assert negotiation_kind_for_error_code("content.value_not_allowed") is NegotiationKind.TARGET
    assert negotiation_kind_for_error_code("content.semantic_conflict") is NegotiationKind.TARGET
    assert negotiation_kind_for_error_code("content.rule_violation") is NegotiationKind.FEASIBILITY
    assert negotiation_kind_for_error_code("content.format_error") is None
    assert negotiation_kind_for_template_uri(TARGET_NEGOTIATION_PROPOSE_URI) is NegotiationKind.TARGET
    assert negotiation_kind_for_template_uri(NEGOTIATION_ABORT_URI) is None


def test_vendor_information_template_segment_is_recognized() -> None:
    """The vendor abbreviates ``information-negotiation`` as ``info-negotiation``."""
    assert negotiation_kind_for_template_uri("Negotiation-T/info-negotiation/propose/v1") is NegotiationKind.INFORMATION


def test_semantic_conflicts_are_split_into_target_and_feasibility() -> None:
    target = SlotValidationError(
        slot_name="guaranteedThroughputTarget",
        code="content.semantic_conflict",
        message="overlap",
        facts={"reason": "17:30~23:00 and 20:00~00:00 overlap with different rates"},
    )
    feasibility = SlotValidationError(
        slot_name="guaranteedThroughputTarget",
        code="content.semantic_conflict",
        message="outage",
        facts={"reason": "The guaranteed rate conflicts with the power outage from 00:00 to 18:00"},
    )

    assert negotiation_kind_for_errors([target]) is NegotiationKind.TARGET
    assert negotiation_kind_for_errors([feasibility]) is NegotiationKind.FEASIBILITY


def test_errors_are_mapped_by_their_first_mapped_entry() -> None:
    errors = [
        SlotValidationError(slot_name="region", code="content.format_error", message="format"),
        SlotValidationError(slot_name="region", code="content.value_not_allowed", message="bad"),
        SlotValidationError(slot_name="region", code="content.param_missing", message="missing"),
    ]

    assert negotiation_kind_for_errors(errors) is NegotiationKind.TARGET
    assert negotiation_kind_for_errors([]) is None


def test_a_missing_value_outranks_a_reported_semantic_conflict() -> None:
    """A live model may report an overlap before the missing target; the kind must not flip.

    The information scenario's Task-T has no throughput target at all. A model that also claims the
    (absent) target overlaps must still open the information negotiation, not the target one.
    """
    conflict = SlotValidationError(
        slot_name="guaranteedThroughputTarget",
        code="content.semantic_conflict",
        message="overlap",
        facts={"reason": "17:30~23:00 and 20:00~00:00 overlap with different rates"},
    )
    missing = SlotValidationError(
        slot_name="guaranteedThroughputTarget",
        code="content.param_missing",
        message="missing",
        facts={"slot_label": "Task Context"},
    )

    assert negotiation_kind_for_errors([conflict, missing]) is NegotiationKind.INFORMATION
    assert negotiation_kind_for_errors([missing, conflict]) is NegotiationKind.INFORMATION


def test_revision_caps_the_rate_for_a_cell_with_a_lower_limit() -> None:
    sample = SampleProposal("cell-002", "01:00~05:00", "90")

    content = _proposal(decide(_state(NegotiationKind.TARGET, inbound=_counter(sample))))

    assert "cell-002" in content.text
    assert "target rate 20%" in content.text


def test_default_plan_matches_the_dataset_contract() -> None:
    assert DEFAULT_PLAN.cell_id == "cell-001"
    assert DEFAULT_PLAN.window == "00:00~06:00"
    assert DEFAULT_PLAN.target_rate == "30"
