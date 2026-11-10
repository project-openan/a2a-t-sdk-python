"""Integration test of the pure A2A confirm path through the client runtime."""

from __future__ import annotations

import asyncio

from a2a.client.client import ClientCallContext
from a2a.types import SendMessageRequest
from demo.catalog import DemoRunCatalog
from demo.pure_confirmation import PureConfirmationAnswer
from protocol.client_runtime import MODE_CONFIRM, ClientRuntime
from shared.config import SampleConfig
from shared.models import NegotiationDecision
from shared.run_constants import FINAL_STATE_TASK_COMPLETED, PROTOCOL_PURE


class _FakeTransport:
    """Return one pre-built batch per send and record the requests."""

    def __init__(self, batches: list[list[dict[str, object]]]) -> None:
        self._batches = list(batches)
        self.requests: list[SendMessageRequest] = []

    async def send(
        self,
        request: SendMessageRequest,
        call_context: ClientCallContext,
    ) -> list[dict[str, object]]:
        del call_context
        self.requests.append(request)
        return self._batches.pop(0) if self._batches else []


def _relayed_question() -> dict[str, object]:
    return {
        "kind": "status",
        "task_id": "outer-1",
        "context_id": "ctx-1",
        "state": "TASK_STATE_INPUT_REQUIRED",
        "text": "Please provide a real region.",
        "metadata": {
            "pure": {
                "kind": "negotiation",
                "phase": "question",
                "round": 1,
                "maxRounds": 5,
                "recommendation": {
                    "action": "accept",
                    "rationale": "Region A",
                    "text": "Region A",
                    "filledParams": {"region": "Region A"},
                },
            }
        },
    }


def _completed() -> dict[str, object]:
    return {
        "kind": "status",
        "task_id": "outer-1",
        "context_id": "ctx-1",
        "state": "TASK_STATE_COMPLETED",
        "text": "The task is applied.",
    }


async def test_confirm_mode_answers_the_relayed_question(offline_config: SampleConfig) -> None:
    """The runtime publishes the question, waits for the operator and sends the text answer."""
    answer = PureConfirmationAnswer(mode=MODE_CONFIRM, timeout_seconds=1.0)
    transport = _FakeTransport([[_relayed_question()], [_completed()]])
    runtime = ClientRuntime(
        config=offline_config,
        run_spec=DemoRunCatalog().spec("information"),
        mode=MODE_CONFIRM,
        protocol=PROTOCOL_PURE,
        transport_name="sse",
        prompt_client=None,
        a2a_client=None,
        transport=transport,  # type: ignore[arg-type]
        provider=None,
        discovered=None,  # type: ignore[arg-type]
        sdk_env=None,
        manual_provider=answer,
        _httpx_client=None,
        pure_answer=answer,
    )
    proposals: list[object] = []
    recommendations: list[object] = []
    runtime.set_recommendation_sink(
        lambda decision, propose, session: recommendations.append(decision)
    )

    pending = asyncio.create_task(
        runtime.run(
            user_request="Create an energy saving task.",
            on_decision_requested=lambda propose, session: proposals.append(propose),
        )
    )
    for _ in range(100):
        if answer.pending:
            break
        await asyncio.sleep(0.01)

    assert answer.pending is True
    assert proposals
    assert recommendations and recommendations[0] is not None
    assert recommendations[0].rationale == "Region A"  # type: ignore[union-attr]
    answer.submit(
        NegotiationDecision(
            action="accept",
            rationale="Region A",
            filled_params={"region": "Region A"},
        )
    )
    result = await asyncio.wait_for(pending, timeout=5.0)

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert len(transport.requests) == 2
    assert transport.requests[1].message.parts[0].text == "The region is Region A."
    assert transport.requests[1].message.context_id == "ctx-1"


async def test_a_scripted_pure_answer_publishes_the_relayed_question(
    offline_config: SampleConfig,
) -> None:
    """The tester replays the P column through a plain callable; it must still report rounds."""
    answers = ["The region is A2ATestRegion."]

    def answer(event: dict[str, object]) -> str:
        del event
        return answers.pop(0)

    transport = _FakeTransport([[_relayed_question()], [_completed()]])
    runtime = ClientRuntime(
        config=offline_config,
        run_spec=DemoRunCatalog().spec("information"),
        mode="auto",
        protocol=PROTOCOL_PURE,
        transport_name="sse",
        prompt_client=None,
        a2a_client=None,
        transport=transport,  # type: ignore[arg-type]
        provider=None,
        discovered=None,  # type: ignore[arg-type]
        sdk_env=None,
        manual_provider=None,
        _httpx_client=None,
        pure_answer=answer,
    )
    proposals: list[object] = []

    result = await asyncio.wait_for(
        runtime.run(
            user_request="Create an energy saving task.",
            on_decision_requested=lambda propose, session: proposals.append(propose),
        ),
        timeout=5.0,
    )

    assert len(proposals) == 1
    assert proposals[0].content == "Please provide a real region."  # type: ignore[union-attr]
    assert proposals[0].round == 1  # type: ignore[union-attr]
    assert result.final_state == FINAL_STATE_TASK_COMPLETED


class _RecordingStructurer:
    """Minimal manual-answer structurer recording the request it is given."""

    def __init__(self) -> None:
        self.request: str | None = None
        self.context: str | None = None

    def set_user_request(self, user_request: str | None) -> None:
        """Record the operator's request."""
        self.request = user_request

    def set_conversation_context(self, conversation_context: str | None) -> None:
        """Record the conversation context."""
        self.context = conversation_context


async def test_the_pure_structurer_receives_the_operator_request(offline_config: SampleConfig) -> None:
    """A pure run used to leave the scenario's canned request in the structuring prompt."""
    structurer = _RecordingStructurer()
    transport = _FakeTransport([[_completed()]])
    runtime = ClientRuntime(
        config=offline_config,
        run_spec=DemoRunCatalog().spec("information"),
        mode=MODE_CONFIRM,
        protocol=PROTOCOL_PURE,
        transport_name="sse",
        prompt_client=None,
        a2a_client=None,
        transport=transport,  # type: ignore[arg-type]
        provider=None,
        discovered=None,  # type: ignore[arg-type]
        sdk_env=None,
        manual_provider=None,
        manual_structurer=structurer,
        _httpx_client=None,
        pure_answer=lambda event: "",
    )

    await asyncio.wait_for(
        runtime.run(
            user_request="Modify the outage target to 10Mbps.",
            conversation_context="Earlier turn: create in Region A.",
        ),
        timeout=5.0,
    )

    assert structurer.request == "Modify the outage target to 10Mbps."
    assert structurer.context == "Earlier turn: create in Region A."
