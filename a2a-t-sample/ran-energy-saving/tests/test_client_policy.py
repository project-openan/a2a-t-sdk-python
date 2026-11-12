"""Deterministic client acceptance policy of an automatic negotiation decision."""

from __future__ import annotations

from protocol.client_policy import (
    ClientPolicy,
    PolicyOutcome,
    agreed_target_value,
    apply_automated_decision,
)
from shared.models import (
    NegotiationAreaOption,
    NegotiationDecision,
    NegotiationOptions,
    NegotiationProposeView,
)

_BASELINE = {
    "operationType": "Modify",
    "region": "A2ATestRegion",
    "siteList": '["LNR_KPI_7745","NES_27A_321071"]',
    "backgroundEvent": "power outage from 08:00 to 18:00",
    "guaranteedThroughputTarget": "{08:00~18:00,10Mbps}",
}

_FEASIBILITY = (
    "The current rate goal cannot satisfy the power outage duration. "
    "Reduce rate target to 2Mbps or less. "
    "If lowering the rate is unacceptable, reduce power outage duration to 4 hours or less."
)


def _propose(
    content: str = _FEASIBILITY,
    *,
    kind: str = "feasibility",
    options: NegotiationOptions | None = None,
) -> NegotiationProposeView:
    return NegotiationProposeView(
        negotiation_id="negotiation-1",
        kind=kind,
        round=1,
        max_rounds=3,
        content=content,
        context_id="context-1",
        options=options,
    )


def _apply(
    decision: NegotiationDecision,
    *,
    kind: str = "feasibility",
    propose: NegotiationProposeView | None = None,
    baseline: dict[str, object] | None = None,
    intent: dict[str, object] | None = None,
    policy: ClientPolicy | None = None,
) -> tuple[NegotiationDecision, PolicyOutcome]:
    return apply_automated_decision(
        decision,
        kind=kind,
        propose=propose or _propose(),
        baseline=baseline if baseline is not None else _BASELINE,
        intent_fields=intent,
        policy=policy or ClientPolicy(),
    )


def test_a_model_invented_entity_value_is_dropped() -> None:
    decision, outcome = _apply(
        NegotiationDecision(action="accept", rationale="accept", filled_params={"region": "Region Z"})
    )

    assert "region" not in (decision.filled_params or {})
    assert outcome.dropped_fields == frozenset({"region"})
    assert "dropped region" in outcome.notes[0]


def test_an_entity_value_the_operator_stated_is_kept() -> None:
    decision, outcome = _apply(
        NegotiationDecision(action="accept", rationale="accept", filled_params={"region": "Region Z"}),
        intent={"region": "Region Z"},
    )

    assert (decision.filled_params or {}).get("region") == "Region Z"
    assert outcome.dropped_fields == frozenset()


def test_a_structured_server_option_is_adopted_when_the_model_omits_it() -> None:
    propose = _propose(
        "Energy saving region is not recognized.",
        kind="information",
        options=NegotiationOptions(
            field="region",
            requested="A2ATestRegion",
            options=[NegotiationAreaOption(value="Served Region")],
        ),
    )

    decision, outcome = _apply(
        NegotiationDecision(action="accept", rationale="accept", filled_params={}),
        kind="information",
        propose=propose,
    )

    assert decision.filled_params == {"region": "Served Region"}
    assert "adopted the server's region" in outcome.notes[0]


def test_a_feasible_alternative_turns_the_models_reject_into_an_accept() -> None:
    decision, outcome = _apply(NegotiationDecision(action="reject", rationale="cannot confirm"))

    assert decision.action == "accept"
    assert outcome.forced_accept is True
    assert decision.filled_params == {"guaranteedThroughputTarget": "{08:00-18:00,2Mbps}"}


def test_the_throughput_floor_keeps_the_models_reject() -> None:
    decision, outcome = _apply(
        NegotiationDecision(action="reject", rationale="cannot confirm"),
        policy=ClientPolicy(min_throughput_mbps=5),
    )

    assert decision.action == "reject"
    assert outcome.forced_accept is False
    assert any("below the client's configured minimum" in note for note in outcome.notes)


def test_an_accept_below_the_floor_is_refused() -> None:
    decision, _outcome = _apply(
        NegotiationDecision(action="accept", rationale="accept"),
        policy=ClientPolicy(min_throughput_mbps=5),
    )

    assert decision.action == "reject"
    assert "below the client's configured minimum" in decision.rationale


def test_agreed_target_applies_the_alternative_rate_to_the_outage_window() -> None:
    assert agreed_target_value(_propose(), _BASELINE) == "{08:00-18:00,2Mbps}"


def test_agreed_target_splits_a_window_that_extends_past_the_outage() -> None:
    """Only the outage portion takes the alternative rate; the rest keeps its own rate."""
    baseline = {**_BASELINE, "guaranteedThroughputTarget": "{00:00~24:00,10Mbps}"}

    assert agreed_target_value(_propose(), baseline) == (
        "{00:00-08:00,10Mbps}, {08:00-18:00,2Mbps}, {18:00-24:00,10Mbps}"
    )


def test_agreed_target_keeps_a_window_that_does_not_overlap_the_outage() -> None:
    baseline = {
        **_BASELINE,
        "guaranteedThroughputTarget": "{00:00~07:00,5Mbps}, {08:00~18:00,10Mbps}",
    }

    assert agreed_target_value(_propose(), baseline) == (
        "{00:00-07:00,5Mbps}, {08:00-18:00,2Mbps}"
    )


def test_agreed_target_reads_a_chinese_outage_window() -> None:
    baseline = {
        **_BASELINE,
        "backgroundEvent": "停电 08:00到18:00",
        "guaranteedThroughputTarget": "{00:00~08:00,5Mbps}, {08:00~18:00,10Mbps}",
    }

    assert agreed_target_value(_propose(), baseline) == (
        "{00:00-08:00,5Mbps}, {08:00-18:00,2Mbps}"
    )


def test_agreed_target_reads_the_vendor_notation() -> None:
    """A baseline written ``{08:00-18:00: 10Mbps}`` still yields the agreed outage window."""
    baseline = {**_BASELINE, "guaranteedThroughputTarget": "{08:00-18:00: 10Mbps}"}

    assert agreed_target_value(_propose(), baseline) == "{08:00-18:00,2Mbps}"


def test_agreed_target_keeps_a_segment_outside_the_background_event() -> None:
    baseline = {
        **_BASELINE,
        "guaranteedThroughputTarget": "{00:00~08:00,10Mbps}, {08:00~18:00,10Mbps}",
    }

    assert agreed_target_value(_propose(), baseline) == (
        "{00:00-08:00,10Mbps}, {08:00-18:00,2Mbps}"
    )


def test_a_counter_below_the_floor_is_refused() -> None:
    decision, _outcome = _apply(
        NegotiationDecision(
            action="counter",
            rationale="counter",
            counter_proposal="Use {00:00~06:00,1Mbps}.",
            counter_params={"targetRate": "1Mbps"},
        ),
        policy=ClientPolicy(min_throughput_mbps=5),
    )

    assert decision.action == "reject"
    assert "counter proposal" in decision.rationale
