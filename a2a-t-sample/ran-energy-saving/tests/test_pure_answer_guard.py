"""Grounding of an automatic pure-A2A answer."""

from __future__ import annotations

import pytest
from a2a_t.core.metadata import NegotiationContext
from protocol.client_policy import ClientPolicy
from protocol.oss_agent import build_live_pure_answer
from protocol.pure_answer_guard import sanitize_pure_decision
from shared.models import NegotiationDecision, NegotiationProposeView


def _propose(content: str) -> NegotiationProposeView:
    return NegotiationProposeView(
        negotiation_id="pure-question",
        kind="information",
        round=1,
        max_rounds=3,
        content=content,
        context_id="pure-question",
    )


def _sanitize(
    decision: NegotiationDecision,
    *,
    request_text: str,
    question: str = "Please provide the correct region name.",
    policy: ClientPolicy | None = None,
) -> tuple[NegotiationDecision | None, list[str]]:
    return sanitize_pure_decision(
        decision,
        request_text=request_text,
        propose=_propose(question),
        policy=policy or ClientPolicy(),
    )


def test_a_region_the_operator_stated_is_kept() -> None:
    decision, notes = _sanitize(
        NegotiationDecision(
            action="accept",
            rationale="answer",
            filled_params={"region": "A2ATestRegion"},
        ),
        request_text="Create an energy saving task for A2ATestRegion.",
    )

    assert decision is not None
    assert decision.filled_params == {"region": "A2ATestRegion"}
    assert notes == []


def test_an_invented_region_leaves_nothing_to_answer() -> None:
    decision, notes = _sanitize(
        NegotiationDecision(
            action="accept",
            rationale="answer",
            filled_params={"region": "A2ATestRegion"},
        ),
        request_text="Create an energy saving task in the Songshanhu area.",
        question="The region name 'Songshanhu' is not recognized. Provide the correct region name.",
    )

    assert decision is None
    assert any("dropped region" in note for note in notes)


def test_a_value_the_question_offers_affirmatively_is_kept() -> None:
    decision, _notes = _sanitize(
        NegotiationDecision(
            action="accept",
            rationale="answer",
            filled_params={"region": "A2ATestRegion"},
        ),
        request_text="Create an energy saving task.",
        question="Available test region: A2ATestRegion. Confirm it.",
    )

    assert decision is not None
    assert decision.filled_params == {"region": "A2ATestRegion"}


def test_a_value_the_question_rejects_does_not_ground() -> None:
    decision, _notes = _sanitize(
        NegotiationDecision(
            action="accept",
            rationale="answer",
            filled_params={"region": "TestRegion"},
        ),
        request_text="Create an energy saving task.",
        question="The region name 'TestRegion' is not recognized. Provide the correct region name.",
    )

    assert decision is None


def test_a_reject_is_not_sent_as_an_answer() -> None:
    decision, notes = _sanitize(
        NegotiationDecision(action="reject", rationale="I cannot confirm the region."),
        request_text="Create an energy saving task.",
    )

    assert decision is None
    assert any("no reject channel" in note for note in notes)


def test_a_counter_is_not_an_information_answer() -> None:
    decision, _notes = _sanitize(
        NegotiationDecision(
            action="counter",
            rationale="counter",
            counter_proposal="Use {00:00~06:00,10Mbps}.",
        ),
        request_text="Create an energy saving task.",
    )

    assert decision is None


def test_a_target_below_the_floor_is_dropped() -> None:
    decision, notes = _sanitize(
        NegotiationDecision(
            action="accept",
            rationale="answer",
            filled_params={"guaranteedThroughputTarget": "{00:00~06:00,2Mbps}"},
        ),
        request_text="Set the guaranteed throughput targets: {00:00~06:00,2Mbps}.",
        policy=ClientPolicy(min_throughput_mbps=5),
    )

    assert decision is None
    assert any("below the client minimum" in note for note in notes)


def test_an_answer_with_no_values_is_not_sent() -> None:
    decision, notes = _sanitize(
        NegotiationDecision(action="accept", rationale="Accepted. Please proceed."),
        request_text="Create an energy saving task.",
    )

    assert decision is None
    assert any("carries no value" in note for note in notes)


class _PureProvider:
    """Decision provider double carrying one decision and the operator request."""

    def __init__(self, decision: NegotiationDecision, request_text: str) -> None:
        self._decision = decision
        self._request_text = request_text

    async def respond(
        self,
        propose: NegotiationProposeView,
        session: NegotiationContext,
    ) -> NegotiationDecision:
        del propose, session
        return self._decision

    def grounding_text(self) -> str:
        return self._request_text


@pytest.mark.asyncio
async def test_the_live_pure_answer_renders_only_grounded_values() -> None:
    provider = _PureProvider(
        NegotiationDecision(
            action="accept",
            rationale="answer",
            filled_params={"region": "A2ATestRegion"},
        ),
        request_text="Create an energy saving task for A2ATestRegion.",
    )
    answer = build_live_pure_answer(provider)

    text = await answer(
        {"kind": "message", "text": "Please provide the correct region name.", "context_id": "c1"}
    )

    assert "A2ATestRegion" in text


@pytest.mark.asyncio
async def test_the_live_pure_answer_is_empty_when_nothing_is_grounded() -> None:
    provider = _PureProvider(
        NegotiationDecision(
            action="accept",
            rationale="answer",
            filled_params={"region": "A2ATestRegion"},
        ),
        request_text="Create an energy saving task in the Songshanhu area.",
    )
    answer = build_live_pure_answer(provider)

    text = await answer(
        {
            "kind": "message",
            "text": "The region name 'Songshanhu' is not recognized. Provide the correct region name.",
            "context_id": "c1",
        }
    )

    assert text == ""


def test_a_confirmation_question_is_answered_without_a_task_t_value() -> None:
    decision, notes = _sanitize(
        NegotiationDecision(action="accept", rationale="Confirmed."),
        request_text=(
            "Modify the energy-saving plan for LTE_26441_LNR_OCL. There will be a power outage "
            "tomorrow. Set the throughput rate guarantee to 10Mbps during the outage."
        ),
        question="Not feasible, please confirm",
    )

    assert decision is not None
    assert decision.filled_params is None
    assert notes == ["an affirmative answer to a confirmation question carries no Task-T value"]


def test_an_information_question_still_needs_a_value() -> None:
    decision, notes = _sanitize(
        NegotiationDecision(action="accept", rationale="Confirmed."),
        request_text="Create an energy saving task in the Songshanhu area.",
        question="Please provide the correct region name.",
    )

    assert decision is None
    assert notes == ["the decision carries no value to answer the question"]
