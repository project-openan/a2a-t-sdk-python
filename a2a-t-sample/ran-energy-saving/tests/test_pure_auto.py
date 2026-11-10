"""Automatic pure-A2A answer: deterministic plan, grounded model answer, operator fallback."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from a2a_t.core.metadata import NegotiationContext
from demo.pure_auto import AutoPureAnswer, build_auto_pure_answer
from protocol.client_policy import ClientPolicy
from shared.models import NegotiationDecision, NegotiationProposeView


class _Provider:
    """Decision provider double recording every call."""

    def __init__(self, decision: NegotiationDecision) -> None:
        self.decision = decision
        self.calls: list[str] = []
        self.request_text = ""

    async def respond(
        self,
        propose: NegotiationProposeView,
        session: NegotiationContext,
    ) -> NegotiationDecision:
        del session
        self.calls.append(propose.content)
        return self.decision

    def set_user_request(self, request_text: str | None) -> None:
        self.request_text = request_text or ""

    def grounding_text(self) -> str:
        return self.request_text


class _Fallback:
    """Confirmation-card double."""

    def __init__(self, text: str = "OPERATOR ANSWER") -> None:
        self.text = text
        self.calls = 0

    async def __call__(self, event: Mapping[str, object]) -> str:
        del event
        self.calls += 1
        return self.text


def _event(text: str = "Not feasible, please confirm") -> dict[str, object]:
    return {"kind": "message", "text": text, "context_id": "ctx-1"}


@pytest.mark.asyncio
async def test_a_bitrate_term_is_answered_by_the_deterministic_plan() -> None:
    provider = _Provider(NegotiationDecision(action="accept", rationale="unused"))
    answer = build_auto_pure_answer(provider, ClientPolicy())
    answer.set_user_request(
        "Implement energy-saving in A2ATestRegion region, the time range is from 00:00 to 22:00. "
        "ensuring user experience for 720p video."
    )

    text = await answer(_event())

    assert text == "Set the guaranteed throughput targets: 00:00-22:00→2Mbps."
    assert provider.calls == []


@pytest.mark.asyncio
async def test_the_model_answer_is_rendered_as_free_text() -> None:
    provider = _Provider(
        NegotiationDecision(
            action="accept",
            rationale="answer",
            filled_params={"region": "A2ATestRegion"},
        )
    )
    answer = build_auto_pure_answer(provider, ClientPolicy())
    answer.set_user_request("Create an energy saving task for A2ATestRegion.")

    text = await answer(_event("The region name is missing. Please provide the correct region name."))

    assert text == "The region is A2ATestRegion."
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_a_bare_accept_falls_back_to_the_operator() -> None:
    provider = _Provider(NegotiationDecision(action="accept", rationale="Confirmed."))
    fallback = _Fallback()
    answer = build_auto_pure_answer(provider, ClientPolicy(), fallback=fallback)
    answer.set_user_request(
        "Implement energy saving in A2ATestRegion region. Set the gain target to 20% - 30%."
    )

    text = await answer(_event())

    assert text == "OPERATOR ANSWER"
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_the_fallback_is_used_when_the_model_cannot_answer() -> None:
    provider = _Provider(NegotiationDecision(action="reject", rationale="cannot answer"))
    fallback = _Fallback()
    answer = build_auto_pure_answer(provider, ClientPolicy(), fallback=fallback)
    answer.set_user_request("Create an energy saving task with no usable value.")

    text = await answer(_event())

    assert text == "OPERATOR ANSWER"


@pytest.mark.asyncio
async def test_without_a_fallback_the_answer_is_empty_so_the_flow_relays() -> None:
    provider = _Provider(NegotiationDecision(action="reject", rationale="cannot answer"))
    answer = build_auto_pure_answer(provider, ClientPolicy())
    answer.set_user_request("Create an energy saving task with no usable value.")

    assert await answer(_event()) == ""


@pytest.mark.asyncio
async def test_a_question_that_states_a_plan_keeps_the_plain_affirmative() -> None:
    provider = _Provider(NegotiationDecision(action="accept", rationale="Confirmed."))
    answer = build_auto_pure_answer(provider, ClientPolicy())
    answer.set_user_request(
        "A power outage from 08:00 to 18:00; the guaranteed rate is 10Mbps."
    )

    text = await answer(_event("Reduce the throughput target from 10Mbps to 2Mbps; confirm it."))

    assert text == "Accepted. Please proceed."


def test_the_answer_object_exposes_the_runtime_hooks() -> None:
    answer = build_auto_pure_answer(
        _Provider(NegotiationDecision(action="accept", rationale="x")),
        ClientPolicy(),
        fallback=_Fallback(),
    )

    assert isinstance(answer, AutoPureAnswer)
    assert callable(answer.set_user_request)
    assert callable(answer.set_proposal_sink)
    assert callable(answer.set_decision_sink)
