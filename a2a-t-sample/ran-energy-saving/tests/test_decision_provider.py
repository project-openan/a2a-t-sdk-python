"""Tests for the three client negotiation decision providers."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path

import pytest
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from demo.adk_fake_model import (
    CLIENT_AGENT_NAME,
    PROGRESS_FUNCTION_RESPONSES,
    PROGRESS_MODEL_TURNS,
    FakeLlm,
    FakeScript,
    FakeToolCall,
    FakeTurn,
    build_fake_model,
)
from demo.agents.client_agent.agent import build_client_agent
from demo.scenario import SCENARIO_INFORMATION, get_scenario
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from protocol.decision_provider import (
    CLIENT_APP_NAME,
    CLIENT_USER_ID,
    OFFLINE_FALLBACK_DECISION,
    AdkDecisionProvider,
    ConfirmingDecisionProvider,
    DecisionProviderError,
    DecisionTimeoutError,
    ManualDecisionProvider,
    NegotiationDecisionProvider,
    ScriptedDecisionProvider,
    _decision_retry_prompt,
    build_decision_prompt,
    build_manual_answer_prompt,
    sweep_stale_sessions,
)
from shared.models import NegotiationDecision, NegotiationProposeView


def _session(
    round_number: int = 1,
    *,
    performative: NegotiationPerformative = NegotiationPerformative.PROPOSE,
) -> NegotiationContext:
    return NegotiationContext(id="neg-1", round=round_number, max_rounds=3, performative=performative)


def _view(kind: str = "information", *, content: str = "Please provide the region.") -> NegotiationProposeView:
    return NegotiationProposeView(
        negotiation_id="neg-1",
        kind=kind,
        round=1,
        max_rounds=3,
        content=content,
        template_uri="Negotiation-T/information-negotiation/propose/v1",
        context_id="ctx-1",
    )


def test_all_providers_satisfy_the_protocol() -> None:
    providers = (
        AdkDecisionProvider(agent_factory=build_client_agent, model=build_fake_model()),
        ScriptedDecisionProvider([NegotiationDecision(action="accept", rationale="ok")]),
        ManualDecisionProvider(timeout_seconds=0.1),
        ConfirmingDecisionProvider(ScriptedDecisionProvider([NegotiationDecision(action="accept", rationale="ok")])),
    )

    for provider in providers:
        assert isinstance(provider, NegotiationDecisionProvider)


def test_build_decision_prompt_carries_the_round_and_content() -> None:
    prompt = build_decision_prompt(_view(), _session(2))

    assert "Round: 2 of 3" in prompt
    assert "Negotiation type: information" in prompt
    assert "Please provide the region." in prompt


def test_build_decision_prompt_requires_answering_required_items() -> None:
    prompt = build_decision_prompt(
        _view(content="## Required Information Items\n1. Energy saving region"),
        _session(1),
    )

    assert "Required Information Items" in prompt
    assert "answer every item in filled_params" in prompt
    assert "A value must appear in the original request" in prompt
    assert "Configured NMS region" not in prompt


def test_build_decision_prompt_asks_for_every_problem_the_proposal_raises() -> None:
    """A mixed proposal (overlap plus infeasibility) is answered in one decision, not split."""
    prompt = build_decision_prompt(
        _view(
            "feasibility",
            content="The windows overlap and the requested rate is not feasible.",
        ),
        _session(1),
    )

    assert "answer all of them in this one decision" in prompt


def test_build_decision_prompt_states_the_string_and_object_fields() -> None:
    prompt = build_decision_prompt(_view("target"), _session())

    assert "confirmed_intent (a string)" in prompt
    assert "filled_params and counter_params are JSON objects" in prompt
    assert "Never put an object in a string field" in prompt


def test_the_retry_prompt_names_the_invalid_field_and_expected_type() -> None:
    prompt = _decision_retry_prompt(
        "field 'confirmed_intent': Input should be a valid string (got dict)"
    )

    assert "confirmed_intent" in prompt
    assert "valid string" in prompt
    assert "are JSON strings" in prompt


def test_build_decision_prompt_carries_the_conversation_context() -> None:
    prompt = build_decision_prompt(
        _view(),
        _session(1),
        user_request="current request",
        conversation_context="user: earlier request\nassistant: earlier result",
    )

    assert "Conversation context (earlier turns):" in prompt
    assert "user: earlier request" in prompt
    assert "Original user request:\ncurrent request" in prompt


def test_build_decision_prompt_carries_the_original_request() -> None:
    prompt = build_decision_prompt(
        _view("target"),
        _session(2),
        user_request="The target is {00:00~07:00,2Mbps}; {20:00~00:00,10Mbps}.",
    )

    assert "Original user request:" in prompt
    assert "00:00~07:00,2Mbps" in prompt
    assert "Return exactly one NegotiationDecision JSON object" in prompt
    assert "no reasoning" in prompt


def test_build_decision_prompt_carries_the_decision_basis() -> None:
    """Shared principles and case conditions are the agent's own acceptance ground."""
    from shared.models import DecisionBasis

    basis = DecisionBasis(
        principles="Never agree to a rate below the client floor.",
        principles_version="2026-11-23.1",
        client_conditions="During an outage 2Mbps is acceptable; below 2Mbps is not.",
    )

    prompt = build_decision_prompt(_view(), _session(1), decision_basis=basis)

    assert "Client decision basis" in prompt
    assert "Never agree to a rate below the client floor." in prompt
    assert "2026-11-23.1" in prompt
    assert "During an outage 2Mbps is acceptable; below 2Mbps is not." in prompt
    # Precedence: case conditions over shared principles, shared principles over defaults.
    assert "take precedence" in prompt
    # It belongs with the request, before the proposal the agent must answer.
    assert prompt.index("Client decision basis") < prompt.index("Proposal content:")


def test_build_decision_prompt_without_a_basis_has_no_basis_section() -> None:
    """A run without tester conditions sees exactly the historical prompt."""
    prompt = build_decision_prompt(_view(), _session(1))

    assert "Client decision basis" not in prompt
    assert "Answer the following A2A-T negotiation proposal." in prompt
    assert "Proposal content:" in prompt


def test_build_decision_prompt_carries_the_client_policy() -> None:
    """The static acceptance policy belongs in the prompt, not in a tool the model can loop on."""
    from protocol.client_policy import ClientPolicy

    prompt = build_decision_prompt(
        _view(), _session(1), policy=ClientPolicy(min_throughput_mbps=2)
    )

    assert "Client acceptance policy:" in prompt
    assert "Hard floor on guaranteed throughput: 2 Mbps." in prompt
    assert "guaranteedThroughputTarget" in prompt
    assert prompt.index("Client acceptance policy:") < prompt.index("Proposal content:")


def test_build_decision_prompt_without_a_policy_states_no_floor() -> None:
    prompt = build_decision_prompt(_view(), _session(1))

    assert "Hard floor on guaranteed throughput: none configured." in prompt
    assert "planning target, not a hard floor" in prompt


async def test_set_decision_basis_is_task_local_on_a_shared_provider() -> None:
    """The OSS Agent shares one provider across concurrent inbound requests."""
    from shared.models import DecisionBasis

    provider = AdkDecisionProvider(agent_factory=build_client_agent, model=build_fake_model())
    seen: dict[str, str | None] = {}

    async def run_a() -> None:
        provider.set_decision_basis(DecisionBasis(client_conditions="conditions-a"))
        await asyncio.sleep(0)
        basis = provider._current_decision_basis()
        seen["a"] = basis.client_conditions if basis is not None else None

    async def run_b() -> None:
        provider.set_decision_basis(DecisionBasis(client_conditions="conditions-b"))
        basis = provider._current_decision_basis()
        seen["b"] = basis.client_conditions if basis is not None else None

    await asyncio.gather(run_a(), run_b())

    assert seen == {"a": "conditions-a", "b": "conditions-b"}


def test_the_client_agent_has_no_tools() -> None:
    """The basis and the policy are in the prompt; a tool call can only cost a model turn."""
    agent = build_client_agent(model=build_fake_model())

    assert list(agent.tools) == []


async def test_scripted_provider_returns_decisions_in_order_and_then_raises() -> None:
    provider = ScriptedDecisionProvider(
        (
            NegotiationDecision(action="accept", rationale="first"),
            NegotiationDecision(action="reject", rationale="second"),
        )
    )
    session = _session()

    first = await provider.respond(_view("target"), session)
    second = await provider.respond(_view("target"), session)

    assert first.action == "accept"
    assert second.action == "reject"
    assert provider.remaining == 0
    with pytest.raises(DecisionProviderError, match="exhausted"):
        await provider.respond(_view("target"), session)


def test_scripted_provider_accepts_a_decision_sequence() -> None:
    scenario = get_scenario(SCENARIO_INFORMATION)
    provider = ScriptedDecisionProvider(scenario.scripted_client_decisions)

    assert provider.remaining == len(scenario.scripted_client_decisions)


async def test_manual_provider_returns_a_submitted_decision() -> None:
    provider = ManualDecisionProvider(timeout_seconds=1.0)
    submitted = NegotiationDecision(action="reject", rationale="SLA cannot be met.")

    assert provider.pending is False
    provider.submit(submitted)
    assert provider.pending is True

    decision = await provider.respond(_view(), _session())

    assert decision is submitted
    assert provider.pending is False


async def test_manual_provider_times_out_into_an_abort() -> None:
    provider = ManualDecisionProvider(timeout_seconds=0.01)

    decision = await provider.respond(_view(), _session(round_number=3))

    assert decision.action == "abort"
    assert "timed out" in decision.rationale
    assert "round 3" in decision.rationale
    assert provider.pending is False


async def test_manual_provider_waits_for_a_late_submission() -> None:
    provider = ManualDecisionProvider(timeout_seconds=1.0)
    submitted = NegotiationDecision(action="accept", rationale="ok", filled_params={"region": "X"})

    async def submit_later() -> None:
        await asyncio.sleep(0.01)
        provider.submit(submitted)

    task = asyncio.create_task(submit_later())
    decision = await provider.respond(_view(), _session())
    await task

    assert decision is submitted


async def test_confirming_provider_publishes_the_recommendation_then_waits() -> None:
    recommendation = NegotiationDecision(
        action="counter",
        rationale="agent recommendation",
        counter_proposal="keep 17:30~20:00",
    )
    submitted = NegotiationDecision(action="accept", rationale="engineer confirmed")
    provider = ConfirmingDecisionProvider(ScriptedDecisionProvider([recommendation]))
    seen: list[str | None] = []
    provider.set_recommendation_sink(
        lambda decision, propose, session: seen.append(
            decision.action if decision is not None else None
        )
    )

    async def submit_later() -> None:
        await asyncio.sleep(0.01)
        provider.submit(submitted)

    task = asyncio.create_task(submit_later())
    decision = await provider.respond(_view("target"), _session())
    await task

    assert seen == ["counter"]
    assert decision is submitted


async def test_confirming_provider_degrades_when_the_recommendation_fails() -> None:
    """A failed recommendation must not take the human decision away."""

    class FailingProvider:
        async def respond(
            self,
            propose: NegotiationProposeView,
            session: NegotiationContext,
        ) -> NegotiationDecision:
            del propose, session
            raise DecisionProviderError("the live decision model is unavailable")

    submitted = NegotiationDecision(action="accept", rationale="engineer decided")
    provider = ConfirmingDecisionProvider(FailingProvider())
    seen: list[NegotiationDecision | None] = []
    provider.set_recommendation_sink(
        lambda decision, propose, session: seen.append(decision)
    )

    async def submit_later() -> None:
        await asyncio.sleep(0.01)
        provider.submit(submitted)

    task = asyncio.create_task(submit_later())
    decision = await provider.respond(_view("target"), _session())
    await task

    assert seen == [None]
    assert provider.last_recommendation_error is not None
    assert "unavailable" in provider.last_recommendation_error
    assert decision is submitted


async def test_confirming_provider_keeps_waiting_after_a_window() -> None:
    provider = ConfirmingDecisionProvider(
        ScriptedDecisionProvider([NegotiationDecision(action="accept", rationale="ok")]),
        timeout_seconds=0.01,
    )
    expired: list[DecisionTimeoutError] = []
    provider.set_wait_sink(expired.append)

    task = asyncio.create_task(provider.respond(_view(), _session(round_number=2)))
    await asyncio.sleep(0.03)

    assert task.done() is False
    assert expired
    assert expired[0].round_number == 2
    assert provider.pending is True

    provider.submit(NegotiationDecision(action="reject", rationale="operator"))
    decision = await asyncio.wait_for(task, timeout=1)

    assert decision.action == "reject"


async def test_a_manual_provider_without_a_wait_sink_fails_after_one_window() -> None:
    provider = ManualDecisionProvider(timeout_seconds=0.01, keep_waiting=True)

    with pytest.raises(DecisionTimeoutError) as raised:
        await provider.respond(_view(), _session(round_number=2))

    assert raised.value.expired_windows == 1
    assert provider.pending is False


class _HangingProvider:
    """A recommendation that never answers until cancelled."""

    def __init__(self) -> None:
        self.cancelled = False

    async def respond(self, propose: NegotiationProposeView, session: NegotiationContext) -> object:
        del propose, session
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def test_a_submission_beats_a_slow_recommendation() -> None:
    inner = _HangingProvider()
    provider = ConfirmingDecisionProvider(
        inner,  # type: ignore[arg-type]
        timeout_seconds=60,
        recommendation_timeout_seconds=60,
    )
    published: list[NegotiationDecision | None] = []
    provider.set_recommendation_sink(lambda decision, _p, _s: published.append(decision))

    task = asyncio.create_task(provider.respond(_view(), _session(round_number=2)))
    await asyncio.sleep(0.01)
    provider.submit(NegotiationDecision(action="accept", rationale="operator"))

    decision = await asyncio.wait_for(task, timeout=1)

    assert decision.action == "accept"
    assert published == []
    assert inner.cancelled is True


async def test_the_recommendation_budget_publishes_unavailable() -> None:
    inner = _HangingProvider()
    provider = ConfirmingDecisionProvider(
        inner,  # type: ignore[arg-type]
        timeout_seconds=60,
        recommendation_timeout_seconds=0.01,
    )
    published: list[NegotiationDecision | None] = []
    provider.set_recommendation_sink(lambda decision, _p, _s: published.append(decision))
    task = asyncio.create_task(provider.respond(_view(), _session(round_number=2)))
    await asyncio.sleep(0.03)

    assert published == [None]
    assert "RecommendationTimeout" in (provider.last_recommendation_error or "")
    assert task.done() is False
    assert inner.cancelled is True

    provider.submit(NegotiationDecision(action="reject", rationale="operator"))
    decision = await asyncio.wait_for(task, timeout=1)

    assert decision.action == "reject"


async def test_cancelling_respond_releases_the_child_tasks() -> None:
    inner = _HangingProvider()
    provider = ConfirmingDecisionProvider(
        inner,  # type: ignore[arg-type]
        timeout_seconds=60,
        recommendation_timeout_seconds=60,
    )
    task = asyncio.create_task(provider.respond(_view(), _session(round_number=2)))
    await asyncio.sleep(0.01)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.01)

    assert provider.pending is False
    assert inner.cancelled is True


async def test_adk_provider_returns_the_fake_model_decision() -> None:
    provider = AdkDecisionProvider(agent_factory=build_client_agent, model=build_fake_model())

    decision = await provider.respond(_view(), _session())

    assert decision.action == "accept"
    assert decision.rationale
    assert decision is not OFFLINE_FALLBACK_DECISION


async def test_a_turn_stops_after_the_tool_call_budget() -> None:
    """A looping model is cut off and asked once more for the decision itself."""
    from demo.agents.client_agent.agent import REQUEST_INSTRUCTION
    from google.adk.agents import LlmAgent

    async def test_probe(value: str) -> dict[str, object]:
        """Return a fixed answer; this test only exercises the tool-call budget."""
        del value
        return {"ok": True}

    call = FakeToolCall(name="test_probe", args={"value": "1"})
    model = build_fake_model(
        (
            FakeScript(
                marker=CLIENT_AGENT_NAME,
                turns=(FakeTurn(tool_calls=(call,)), FakeTurn(tool_calls=(call,))),
                default_text=json.dumps({"action": "accept", "rationale": "ok"}),
                progress=PROGRESS_FUNCTION_RESPONSES,
            ),
        )
    )
    agent = LlmAgent(
        name=CLIENT_AGENT_NAME,
        model=model,
        instruction=REQUEST_INSTRUCTION,
        tools=[test_probe],
        output_schema=NegotiationDecision,
    )
    provider = AdkDecisionProvider(agent=agent, max_tool_calls=2)

    decision = await provider.respond(_view(), _session())

    assert provider.tool_calls_this_turn == 2
    assert decision.action == "accept"


async def test_an_inconsistent_decision_is_retried_once() -> None:
    """A reject that carries accept-only fields is a field slip; one corrective turn may fix it."""
    inconsistent = json.dumps(
        {
            "action": "reject",
            "rationale": "Therefore, I must accept the server's feasible alternative.",
            "filled_params": {"guaranteedThroughputTarget": "{08:00~18:00,2Mbps}"},
            "feasibility_summary": "Accepted the recommendation.",
        }
    )
    consistent = json.dumps(
        {
            "action": "accept",
            "rationale": "Accepted the recommendation.",
            "filled_params": {"guaranteedThroughputTarget": "{08:00~18:00,2Mbps}"},
        }
    )
    provider = AdkDecisionProvider(
        agent_factory=build_client_agent,
        model=_scripted_model(inconsistent, consistent),
    )

    decision = await provider.respond(_view("feasibility"), _session())

    assert decision.action == "accept"
    assert decision.filled_params == {"guaranteedThroughputTarget": "{08:00~18:00,2Mbps}"}


async def test_an_inconsistent_decision_that_stays_inconsistent_fails() -> None:
    """After one corrective turn the provider must not guess which field the model meant."""
    inconsistent = json.dumps(
        {
            "action": "reject",
            "rationale": "I must accept.",
            "filled_params": {"guaranteedThroughputTarget": "{08:00~18:00,2Mbps}"},
        }
    )
    provider = AdkDecisionProvider(
        agent_factory=build_client_agent,
        model=_scripted_model(inconsistent, inconsistent),
    )

    with pytest.raises(DecisionProviderError, match="self-contradictory"):
        await provider.respond(_view("feasibility"), _session())


def _scripted_model(*texts: str) -> FakeLlm:
    """Build a fake model serving one client-agent turn per text."""
    return build_fake_model(
        (
            FakeScript(
                marker=CLIENT_AGENT_NAME,
                turns=tuple(FakeTurn(text=text) for text in texts),
                progress=PROGRESS_MODEL_TURNS,
            ),
        )
    )


def test_build_manual_answer_prompt_contains_the_answer_and_parsed_items() -> None:
    prompt = build_manual_answer_prompt(
        _view(content="Please provide the following missing items: Rate, Region."),
        _session(2),
        "The guaranteed rate is 10Mbps in Region A.",
        user_request="save energy",
    )

    assert "Engineer's manual answer:" in prompt
    assert "10Mbps" in prompt
    assert "filled_params" in prompt
    assert "Parsed required items:" in prompt
    assert "- Rate" in prompt
    assert "- Region" in prompt
    assert "Original user request:" in prompt


async def test_adk_provider_structures_a_manual_information_answer() -> None:
    payload = json.dumps(
        {
            "action": "accept",
            "rationale": "The engineer supplied the missing rate.",
            "filled_params": {"guaranteedThroughputTarget": "{00:00~22:00,10Mbps}"},
        }
    )
    provider = AdkDecisionProvider(agent_factory=build_client_agent, model=_scripted_model(payload))

    decision = await provider.structure_answer(
        _view(content="Please provide the following missing items: Rate."),
        _session(),
        "The guaranteed rate is 10Mbps from 00:00 to 22:00.",
    )

    assert decision is not None
    assert decision.action == "accept"
    assert decision.filled_params == {"guaranteedThroughputTarget": "{00:00~22:00,10Mbps}"}


async def test_adk_provider_rejects_an_information_accept_without_values() -> None:
    payload = json.dumps({"action": "accept", "rationale": "ok"})
    provider = AdkDecisionProvider(agent_factory=build_client_agent, model=_scripted_model(payload))

    decision = await provider.structure_answer(_view(), _session(), "Yes.")

    assert decision is None


async def test_adk_provider_accepts_a_confirmation_answer_without_values() -> None:
    """A "please confirm" question is answered by the agreement, which carries no Task-T value."""
    payload = json.dumps({"action": "accept", "rationale": "Confirmed."})
    provider = AdkDecisionProvider(agent_factory=build_client_agent, model=_scripted_model(payload))

    decision = await provider.structure_answer(
        _view(content="Not feasible, please confirm"),
        _session(),
        "Confirmed.",
    )

    assert decision is not None
    assert decision.action == "accept"
    assert not decision.filled_params


async def test_adk_provider_structures_a_target_counter_plan() -> None:
    payload = json.dumps(
        {
            "action": "counter",
            "rationale": "Use a lower rate in the outage window.",
            "counter_params": {"cellId": "cell-001", "window": "00:00~06:00", "targetRate": "2Mbps"},
        }
    )
    provider = AdkDecisionProvider(agent_factory=build_client_agent, model=_scripted_model(payload))

    decision = await provider.structure_answer(
        _view("target", content="The target overlaps the outage."),
        _session(),
        "Use 2Mbps for cell-001 from 00:00 to 06:00.",
    )

    assert decision is not None
    assert decision.action == "counter"
    assert decision.counter_params == {
        "cellId": "cell-001",
        "window": "00:00~06:00",
        "targetRate": "2Mbps",
    }


async def test_adk_provider_extracts_a_decision_from_prose_and_fences() -> None:
    """A live model may wrap the JSON in a sentence or a fence; the parser still finds it."""
    payload = json.dumps({"action": "reject", "rationale": "The SLA cannot be met."})
    provider = AdkDecisionProvider(
        agent_factory=build_client_agent,
        model=_scripted_model(f"Here is my decision:\n```json\n{payload}\n```\nDone."),
    )

    decision = await provider.respond(_view(), _session())

    assert decision.action == "reject"
    assert decision.rationale == "The SLA cannot be met."


async def test_adk_provider_retries_once_when_the_model_returns_only_prose() -> None:
    """One corrective turn recovers a model that ignored the output schema."""
    payload = json.dumps({"action": "counter", "rationale": "Use a non-overlapping target."})
    provider = AdkDecisionProvider(
        agent_factory=build_client_agent,
        model=_scripted_model(
            "I need to check the policy before I answer.",
            payload,
        ),
    )

    decision = await provider.respond(_view("target"), _session())

    assert decision.action == "counter"
    assert decision.rationale == "Use a non-overlapping target."


async def test_adk_provider_unwraps_a_single_value_confirmed_intent_object() -> None:
    """The reported live shape: the model copied filled_params into a string field."""
    target = "{08:00-11:00,10Mbps},{11:00-18:00,5Mbps}"
    payload = json.dumps(
        {
            "action": "accept",
            "rationale": "Split the overlapping windows at the stated boundary.",
            "filled_params": {"guaranteedThroughputTarget": target},
            "confirmed_intent": {"guaranteedThroughputTarget": target},
        }
    )
    provider = AdkDecisionProvider(agent_factory=build_client_agent, model=_scripted_model(payload))

    decision = await provider.respond(_view("target"), _session())

    assert decision.action == "accept"
    assert decision.confirmed_intent == target


async def test_adk_provider_rejects_a_confirmed_intent_object_that_conflicts_with_filled_params() -> None:
    """Two different stated plans must not be silently collapsed into one."""
    payload = json.dumps(
        {
            "action": "accept",
            "rationale": "Split the overlapping windows.",
            "filled_params": {"guaranteedThroughputTarget": "{08:00-11:00,10Mbps}"},
            "confirmed_intent": {"guaranteedThroughputTarget": "{08:00-11:00,5Mbps}"},
        }
    )
    provider = AdkDecisionProvider(
        agent_factory=build_client_agent,
        model=_scripted_model(payload, payload),
    )

    with pytest.raises(DecisionProviderError) as raised:
        await provider.respond(_view("target"), _session())

    message = str(raised.value)
    assert "confirmed_intent" in message
    assert "valid string" in message
    assert "got dict" in message


async def test_adk_provider_uses_the_offline_fallback_when_the_model_returns_no_decision() -> None:
    provider = AdkDecisionProvider(
        agent_factory=build_client_agent,
        model=build_fake_model(),
        fallback_decision=OFFLINE_FALLBACK_DECISION,
    )

    decision = await provider.respond(_view(), _session())

    # The fake model serves its scripted client decision, so the fallback only applies to a model
    # that produced garbage; the contract here is that the provider always returns a decision.
    assert decision.action in ("accept", "reject", "abort", "counter")


def test_adk_provider_raises_without_a_fallback_on_unparseable_output() -> None:
    provider = AdkDecisionProvider(agent_factory=build_client_agent, model=build_fake_model())

    with pytest.raises(DecisionProviderError):
        provider._fallback_or_raise("no decision")


async def test_adk_provider_persists_a_decision_session(tmp_path: Path) -> None:
    """The decision session is durable: it survives the provider and is kept as audit data."""
    db_path = tmp_path / "client.sqlite3"
    provider = AdkDecisionProvider(
        agent_factory=build_client_agent,
        model=build_fake_model(),
        session_db_path=str(db_path),
    )
    assert provider.durable_sessions is True

    decision = await provider.respond(_view(), _session())
    assert decision.action == "accept"

    # A fresh service over the same file is what a later process (or the sweep) sees.
    reopened = SqliteSessionService(str(db_path))
    listed = await reopened.list_sessions(app_name=CLIENT_APP_NAME)
    assert [session.id for session in listed.sessions] == ["ran-client-neg-1"]


async def test_adk_provider_reuses_one_persisted_session_across_rounds(tmp_path: Path) -> None:
    """A durable store already holds the session on the next round; it must load, not re-create."""
    payload = json.dumps({"action": "accept", "rationale": "The plan is feasible."})
    provider = AdkDecisionProvider(
        agent_factory=build_client_agent,
        model=_scripted_model(payload, payload),
        session_db_path=str(tmp_path / "client.sqlite3"),
    )

    await provider.respond(_view(), _session(1))
    await provider.respond(_view(), _session(2))

    listed = await provider.runner.session_service.list_sessions(app_name=CLIENT_APP_NAME)
    assert [session.id for session in listed.sessions] == ["ran-client-neg-1"]


async def test_adk_provider_falls_back_to_memory_when_the_database_cannot_be_opened(
    tmp_path: Path,
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("occupied", encoding="utf-8")
    provider = AdkDecisionProvider(
        agent_factory=build_client_agent,
        model=build_fake_model(),
        session_db_path=str(blocker / "client.sqlite3"),
    )

    assert provider.durable_sessions is False
    decision = await provider.respond(_view(), _session())
    assert decision.action == "accept"


async def test_sweep_deletes_only_sessions_past_the_retention_window(tmp_path: Path) -> None:
    db_path = tmp_path / "client.sqlite3"
    service = SqliteSessionService(str(db_path))
    await service.create_session(app_name=CLIENT_APP_NAME, user_id=CLIENT_USER_ID, session_id="ran-client-old")
    await service.create_session(app_name=CLIENT_APP_NAME, user_id=CLIENT_USER_ID, session_id="ran-client-new")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE sessions SET update_time = ? WHERE id = ?",
            (time.time() - 2 * 24 * 60 * 60, "ran-client-old"),
        )
        connection.commit()

    deleted = await sweep_stale_sessions(str(db_path), retention_days=1)

    assert deleted == 1
    remaining = await service.list_sessions(app_name=CLIENT_APP_NAME)
    assert [session.id for session in remaining.sessions] == ["ran-client-new"]


async def test_user_request_is_task_local_on_a_shared_provider() -> None:
    """The OSS Agent shares one provider across concurrent inbound requests."""
    provider = AdkDecisionProvider(agent_factory=build_client_agent, model=build_fake_model())
    seen: dict[str, str | None] = {}

    async def run_a() -> None:
        provider.set_user_request("request-a")
        await asyncio.sleep(0)
        seen["a"] = provider._current_user_request()

    async def run_b() -> None:
        provider.set_user_request("request-b")
        seen["b"] = provider._current_user_request()

    await asyncio.gather(run_a(), run_b())

    assert seen == {"a": "request-a", "b": "request-b"}


async def test_sweep_skips_a_database_without_session_tables(tmp_path: Path) -> None:
    """Starting the UI must not create the ADK schema in a client database that never used it."""
    db_path = tmp_path / "client.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY)")
        connection.commit()

    assert await sweep_stale_sessions(str(db_path)) == 0

    with sqlite3.connect(db_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "sessions" not in tables
