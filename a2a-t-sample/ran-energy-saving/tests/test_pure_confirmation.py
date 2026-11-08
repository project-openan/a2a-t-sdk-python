"""Tests of the pure A2A confirmation answer and its plain-text rendering."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from demo.pure_confirmation import PureConfirmationAnswer
from demo.pure_policy import pure_answer_from_recommendation, render_pure_answer
from protocol.client_runtime import MODE_CONFIRM, MODE_MANUAL
from shared.models import NegotiationDecision


def _question(*, recommendation: str | None = "Region A") -> dict[str, object]:
    pure: dict[str, object] = {
        "kind": "negotiation",
        "phase": "question",
        "round": 1,
        "maxRounds": 5,
    }
    if recommendation is not None:
        pure["recommendation"] = recommendation
    return {
        "kind": "status",
        "task_id": "outer-1",
        "context_id": "ctx-1",
        "state": "TASK_STATE_INPUT_REQUIRED",
        "text": "Please provide a real region.",
        "metadata": {"pure": pure},
    }


def test_render_pure_answer_uses_the_supplied_values() -> None:
    decision = NegotiationDecision(action="accept", rationale="Region A", filled_params={"region": "Region A"})

    assert render_pure_answer(decision) == "Region A"


def test_render_pure_answer_keeps_the_throughput_target_first() -> None:
    decision = NegotiationDecision(
        action="accept",
        rationale="Keep the agreed target.",
        filled_params={"region": "Region A", "guaranteedThroughputTarget": "{00:00~06:00,2Mbps}"},
    )

    assert render_pure_answer(decision) == "{00:00~06:00,2Mbps}"


def test_automatic_mode_answers_with_the_relayed_recommendation() -> None:
    assert pure_answer_from_recommendation(_question()) == "Region A"


def test_automatic_mode_falls_back_to_the_structured_policy() -> None:
    event: dict[str, object] = {
        "kind": "status",
        "task_id": "task-1",
        "context_id": "ctx-1",
        "state": "TASK_STATE_INPUT_REQUIRED",
        "text": "Please provide the target.",
        "metadata": {"pure": {"kind": "negotiation", "phase": "information", "round": 1}},
    }

    assert pure_answer_from_recommendation(event) == "{00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~24:00,10Mbps}"


async def test_confirm_mode_publishes_the_recommendation_and_sends_the_answer() -> None:
    answer = PureConfirmationAnswer(mode=MODE_CONFIRM, timeout_seconds=1.0)
    proposals: list[object] = []
    recommendations: list[NegotiationDecision | None] = []
    decisions: list[NegotiationDecision] = []
    answer.set_proposal_sink(lambda propose, session: proposals.append(propose))
    answer.set_recommendation_sink(
        lambda decision, propose, session: recommendations.append(decision)
    )
    answer.set_decision_sink(lambda decision, propose, session: decisions.append(decision))

    pending = asyncio.create_task(answer(_question()))
    await asyncio.sleep(0)
    answer.submit(NegotiationDecision(action="accept", rationale="Region A", filled_params={"region": "Region A"}))
    text = await pending

    assert text == "Region A"
    assert proposals[0].content == "Please provide a real region."  # type: ignore[union-attr]
    assert recommendations and recommendations[0] is not None
    assert recommendations[0].rationale == "Region A"
    assert decisions and decisions[0].filled_params == {"region": "Region A"}


async def test_manual_mode_does_not_publish_a_recommendation() -> None:
    answer = PureConfirmationAnswer(mode=MODE_MANUAL, timeout_seconds=1.0)
    recommendations: list[NegotiationDecision | None] = []
    answer.set_recommendation_sink(
        lambda decision, propose, session: recommendations.append(decision)
    )

    pending = asyncio.create_task(answer(_question()))
    await asyncio.sleep(0)
    answer.submit(NegotiationDecision(action="accept", rationale="Region A", filled_params={"region": "Region A"}))
    await pending

    assert recommendations == []


async def test_a_confirmation_timeout_sends_nothing() -> None:
    answer = PureConfirmationAnswer(mode=MODE_CONFIRM, timeout_seconds=0.01)

    assert await answer(_question()) == ""


def test_the_question_metadata_is_only_sample_local() -> None:
    """Sanity: the fixture matches the event shape the pure flow normalizes."""
    metadata = _question()["metadata"]
    assert isinstance(metadata, Mapping)
    assert metadata["pure"]["kind"] == "negotiation"
