"""Tests for the deterministic ADK fake model."""

from __future__ import annotations

import json

import pytest
from demo.adk_fake_model import (
    CLIENT_AGENT_NAME,
    DEFAULT_FINAL_TEXT,
    PROGRESS_MODEL_TURNS,
    FakeLlm,
    FakeScript,
    FakeToolCall,
    FakeTurn,
    build_client_script,
    build_fake_model,
)
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from shared.models import NegotiationDecision


def _request(agent_name: str, user_text: str = "run the task") -> LlmRequest:
    return LlmRequest(
        model="fake-ran-energy-saving",
        contents=[types.Content(role="user", parts=[types.Part(text=user_text)])],
        config=types.GenerateContentConfig(
            system_instruction=f'You are an agent. Your internal name is "{agent_name}".',
        ),
    )


async def _collect(model: FakeLlm, request: LlmRequest) -> list[LlmResponse]:
    return [response async for response in model.generate_content_async(request)]


@pytest.mark.asyncio
async def test_client_script_returns_a_negotiation_decision() -> None:
    model = build_fake_model()
    request = _request(CLIENT_AGENT_NAME, "Decide on the proposed energy-saving plan.")

    responses = await _collect(model, request)

    assert len(responses) == 1
    content = responses[0].content
    assert content is not None
    decision = json.loads((content.parts or [])[0].text or "")
    assert decision["action"] == "accept"
    assert decision["rationale"]


def test_build_client_script_returns_the_decisions_in_order() -> None:
    decisions = (
        NegotiationDecision(
            action="counter",
            rationale="The first revision still overlaps.",
            counter_proposal="Keep the extra capacity in a free window.",
        ),
        NegotiationDecision(action="accept", rationale="The revision is acceptable."),
    )

    script = build_client_script(decisions)

    assert script.marker == CLIENT_AGENT_NAME
    assert script.progress == PROGRESS_MODEL_TURNS
    parsed = [json.loads(turn.text or "") for turn in script.turns]
    assert [item["action"] for item in parsed] == ["counter", "accept"]
    assert parsed[0]["counter_proposal"] == "Keep the extra capacity in a free window."
    assert "filled_params" not in parsed[1]


@pytest.mark.asyncio
async def test_client_script_advances_on_model_turns() -> None:
    script = build_client_script(
        [
            NegotiationDecision(action="counter", rationale="counter once"),
            NegotiationDecision(action="accept", rationale="accept after the revision"),
        ]
    )
    model = build_fake_model([script])
    request = _request(CLIENT_AGENT_NAME)

    first = (await _collect(model, request))[0]
    first_text = (first.content.parts or [])[0].text if first.content is not None else None
    assert json.loads(first_text or "")["action"] == "counter"

    request.contents.append(types.Content(role="model", parts=[types.Part(text=first_text or "")]))
    second = (await _collect(model, request))[0]
    second_text = (second.content.parts or [])[0].text if second.content is not None else None
    assert json.loads(second_text or "")["action"] == "accept"


@pytest.mark.asyncio
async def test_unknown_marker_serves_default_text() -> None:
    model = build_fake_model()
    request = _request("some_other_agent")

    responses = await _collect(model, request)

    content = responses[0].content
    assert content is not None
    assert (content.parts or [])[0].text == DEFAULT_FINAL_TEXT


@pytest.mark.asyncio
async def test_custom_script_can_be_injected() -> None:
    script = FakeScript(
        marker="custom_agent",
        turns=(FakeTurn(tool_calls=(FakeToolCall(name="custom_tool", args={"value": 1}),)),),
        default_text="custom done",
    )
    model = build_fake_model([script])
    request = _request("custom_agent")

    responses = await _collect(model, request)

    assert responses[0].content is not None
    function_call = (responses[0].content.parts or [])[0].function_call
    assert function_call is not None
    assert function_call.name == "custom_tool"
    assert function_call.args == {"value": 1}
