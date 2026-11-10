"""Deterministic free-text plan for a terse pure-A2A confirmation question."""

from __future__ import annotations

from demo.pure_concession import plan_pure_answer
from protocol.client_policy import ClientPolicy
from shared.models import NegotiationProposeView


def _question(content: str = "Not feasible, please confirm", *, kind: str = "feasibility") -> NegotiationProposeView:
    return NegotiationProposeView(
        negotiation_id="pure-question",
        kind=kind,
        round=1,
        max_rounds=3,
        content=content,
        context_id="pure-question",
        parameters={"pureConfirmation": True},
    )


def test_a_bitrate_quality_supplies_the_documented_rate() -> None:
    request = (
        "Implement energy-saving in A2ATestRegion region, the time range is from 00:00 to 22:00. "
        "ensuring user experience for 720p video."
    )

    plan = plan_pure_answer(request, _question(), ClientPolicy())

    assert plan is not None
    assert plan.text == "Set the guaranteed throughput targets: 00:00-22:00→2Mbps."


def test_a_bare_rate_in_a_rate_quality_sentence_is_read_as_mbps() -> None:
    request = (
        "Implement energy-saving in A2ATestRegion region, the time range is from 00:00 to 22:00. "
        "Ensure the UX target is at least 10."
    )

    plan = plan_pure_answer(request, _question(), ClientPolicy())

    assert plan is not None
    assert plan.text == "Set the guaranteed throughput targets: 00:00-22:00→10Mbps."


def test_a_long_outage_concedes_one_step_down() -> None:
    request = (
        "Due to a power outage from 08:00 to 18:00, modify their guaranteed throughput to 10 Mbps."
    )

    plan = plan_pure_answer(request, _question(), ClientPolicy())

    assert plan is not None
    assert plan.text == "Lower the guaranteed throughput target to 5Mbps during the outage."


def test_the_ladder_reaches_the_vendor_floor() -> None:
    request = "A power outage from 08:00 to 18:00; the guaranteed rate is 5Mbps."

    plan = plan_pure_answer(request, _question(), ClientPolicy())

    assert plan is not None
    assert plan.text == "Lower the guaranteed throughput target to 2Mbps during the outage."


def test_a_concession_below_the_client_floor_is_not_offered() -> None:
    request = "A power outage from 08:00 to 18:00; the guaranteed rate is 10Mbps."

    assert plan_pure_answer(request, _question(), ClientPolicy(min_throughput_mbps=8)) is None


def test_a_complete_request_is_left_to_the_decision_agent() -> None:
    """A RAT/mode/gain question must not be answered by restating the unchanged rate."""
    request = (
        "Implement energy-saving in A2ATestRegion region, during 19:00-22:00 guarantee 10 Mbps "
        "throughput. Cell RAT for energy saving: LNR."
    )

    assert plan_pure_answer(request, _question(), ClientPolicy()) is None


def test_a_question_that_states_an_alternative_is_left_alone() -> None:
    question = _question(
        "Reduce the throughput target from 10Mbps to 2Mbps for the outage period; confirm it."
    )
    request = "A power outage from 08:00 to 18:00; the guaranteed rate is 10Mbps."

    assert plan_pure_answer(request, question, ClientPolicy()) is None


def test_an_information_question_does_not_use_the_rate_plan() -> None:
    request = "A power outage from 08:00 to 18:00; the guaranteed rate is 10Mbps."

    assert (
        plan_pure_answer(request, _question("Which region?", kind="information"), ClientPolicy())
        is None
    )
