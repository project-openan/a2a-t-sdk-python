"""Recorded decision sequences used by protocol unit tests (test-only data)."""

from __future__ import annotations

from demo.scenario import (
    FEASIBILITY_ACCEPT_SUMMARY,
    FEASIBILITY_CONTEXT,
    FEASIBILITY_TARGET,
    SCENARIO_ABORT,
    SCENARIO_FEASIBILITY,
    SCENARIO_INFORMATION,
    SCENARIO_NIGHT,
    SCENARIO_OUTAGE,
    SCENARIO_REJECT,
    SCENARIO_TARGET,
    SCENARIO_UNKNOWN_AREA,
    TARGET_ACCEPT_CONFIRMATION,
    TARGET_TARGET,
    UNKNOWN_AREA_TARGET,
)
from shared.models import NegotiationDecision

SCRIPTED_DECISIONS: dict[str, tuple[NegotiationDecision, ...]] = {
    SCENARIO_NIGHT: (),
    SCENARIO_INFORMATION: (),
    SCENARIO_UNKNOWN_AREA: (
        NegotiationDecision(
            action="accept",
            rationale=(
                "The area is not served; the client confirms Region A and keeps the "
                "requested guaranteed throughput windows."
            ),
            filled_params={"region": "Region A", "guaranteedThroughputTarget": UNKNOWN_AREA_TARGET},
        ),
    ),
    SCENARIO_TARGET: (
        NegotiationDecision(
            action="accept",
            rationale="The revised plan has non-overlapping segments and meets the throughput target.",
            filled_params={"guaranteedThroughputTarget": TARGET_TARGET},
            confirmed_intent=TARGET_ACCEPT_CONFIRMATION,
        ),
    ),
    SCENARIO_FEASIBILITY: (
        NegotiationDecision(
            action="accept",
            rationale="The outage-aware alternative plan is feasible and keeps the SLA margin.",
            filled_params={"guaranteedThroughputTarget": FEASIBILITY_TARGET, **FEASIBILITY_CONTEXT},
            feasibility_summary=FEASIBILITY_ACCEPT_SUMMARY,
        ),
    ),
    SCENARIO_REJECT: (
        NegotiationDecision(
            action="reject",
            rationale="The client cannot confirm a served area for the requested region.",
        ),
    ),
    SCENARIO_ABORT: (
        NegotiationDecision(
            action="abort",
            rationale="The client cannot choose a served area; the negotiation is aborted.",
        ),
    ),
    SCENARIO_OUTAGE: (),
}


def scripted_decisions_for(name: str) -> tuple[NegotiationDecision, ...]:
    """Return the recorded decision sequence of one scenario name."""
    return SCRIPTED_DECISIONS.get(name, ())
