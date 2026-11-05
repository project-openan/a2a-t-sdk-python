"""Tests of the pure A2A client flow and its INPUT_REQUIRED continuation."""

from __future__ import annotations

from a2a.client.client import ClientCallContext
from a2a.types import SendMessageRequest
from protocol.pure_client_flow import run_pure_client_flow
from shared.run_constants import FINAL_STATE_TASK_COMPLETED


class _FakeTransport:
    """Return pre-built event batches and record every request."""

    def __init__(self, batches: list[list[dict[str, object]]]) -> None:
        self._batches = list(batches)
        self.requests: list[tuple[SendMessageRequest, ClientCallContext]] = []

    async def send(
        self,
        request: SendMessageRequest,
        call_context: ClientCallContext,
    ) -> list[dict[str, object]]:
        self.requests.append((request, call_context))
        if not self._batches:
            return []
        return self._batches.pop(0)


def _input_required(*, round_number: int = 1) -> dict[str, object]:
    return {
        "kind": "status",
        "task_id": "task-1",
        "context_id": "ctx-1",
        "state": "TASK_STATE_INPUT_REQUIRED",
        "text": "Please provide the target.",
        "metadata": {
            "pure": {
                "kind": "negotiation",
                "phase": "information",
                "round": round_number,
                "maxRounds": 3,
            }
        },
    }


def _completed() -> dict[str, object]:
    return {
        "kind": "status",
        "task_id": "task-1",
        "context_id": "ctx-1",
        "state": "TASK_STATE_COMPLETED",
        "text": "RAN energy-saving task completed.",
    }


async def test_the_flow_continues_the_same_task_after_input_required() -> None:
    transport = _FakeTransport([[_input_required()], [_completed()]])
    spans: list[dict[str, object]] = []

    result = await run_pure_client_flow(
        transport=transport,  # type: ignore[arg-type]
        user_request="Create a RAN energy saving task.",
        answer=lambda event: "Use {00:00~06:00,10Mbps}.",
        trace_sink=spans.append,
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert len(transport.requests) == 2
    first_request, first_context = transport.requests[0]
    assert first_request.message.parts[0].text == "Create a RAN energy saving task."
    assert first_context.service_parameters == {"A2A-Extensions": ""}

    follow_up, follow_context = transport.requests[1]
    assert follow_up.message.task_id == "task-1"
    assert follow_up.message.context_id == "ctx-1"
    assert follow_up.message.parts[0].text == "Use {00:00~06:00,10Mbps}."
    assert follow_context.service_parameters == {"A2A-Extensions": ""}
    assert len(spans) == 2
    assert all(span["detail"]["protocol"] == "pure" for span in spans)


async def test_the_run_id_travels_as_sample_metadata_on_both_messages() -> None:
    transport = _FakeTransport([[_input_required()], [_completed()]])

    await run_pure_client_flow(
        transport=transport,  # type: ignore[arg-type]
        user_request="Create a RAN energy saving task.",
        answer=lambda event: "Use {00:00~06:00,10Mbps}.",
        run_id="run-42",
    )

    first_request, _ = transport.requests[0]
    follow_up, _ = transport.requests[1]
    assert first_request.message.metadata["sampleRunId"] == "run-42"
    assert follow_up.message.metadata["sampleRunId"] == "run-42"
    # The sample key is the only metadata; no A2A-T extension metadata is attached.
    assert set(first_request.message.metadata) == {"sampleRunId"}


async def test_the_default_answer_uses_the_server_proposal() -> None:
    proposal = "{00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~23:59,10Mbps}"
    event = _input_required()
    event["metadata"] = {"pure": {"kind": "negotiation", "phase": "target", "proposal": proposal}}
    transport = _FakeTransport([[event], [_completed()]])

    await run_pure_client_flow(
        transport=transport,  # type: ignore[arg-type]
        user_request="Create a RAN energy saving task.",
    )

    assert transport.requests[1][0].message.parts[0].text == proposal


async def test_no_answer_stops_the_flow() -> None:
    transport = _FakeTransport([[_input_required()]])

    result = await run_pure_client_flow(
        transport=transport,  # type: ignore[arg-type]
        user_request="Create a RAN energy saving task.",
        answer=lambda event: "",
    )

    assert len(transport.requests) == 1
    assert result.final_state != FINAL_STATE_TASK_COMPLETED


async def test_input_required_without_sample_metadata_is_still_detected() -> None:
    """A real NMS returns a standard A2A status without the sample's ``pure`` metadata."""
    event = _input_required()
    event["metadata"] = {}
    transport = _FakeTransport([[event], [_completed()]])

    result = await run_pure_client_flow(
        transport=transport,  # type: ignore[arg-type]
        user_request="Create a RAN energy saving task.",
        answer=lambda event: "Use {00:00~06:00,10Mbps}.",
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert len(transport.requests) == 2
    assert transport.requests[1][0].message.task_id == "task-1"


async def test_an_async_answer_callback_is_awaited() -> None:
    transport = _FakeTransport([[_input_required()], [_completed()]])

    async def answer(event: dict[str, object]) -> str:
        del event
        return "Use {00:00~06:00,10Mbps}."

    await run_pure_client_flow(
        transport=transport,  # type: ignore[arg-type]
        user_request="Create a RAN energy saving task.",
        answer=answer,
    )

    assert transport.requests[1][0].message.parts[0].text == "Use {00:00~06:00,10Mbps}."


async def test_followup_can_carry_only_the_context_id() -> None:
    transport = _FakeTransport([[_input_required()], [_completed()]])

    await run_pure_client_flow(
        transport=transport,  # type: ignore[arg-type]
        user_request="Create a RAN energy saving task.",
        answer=lambda event: "Use {00:00~06:00,10Mbps}.",
        followup="context",
    )

    follow_up = transport.requests[1][0]
    assert follow_up.message.task_id == ""
    assert follow_up.message.context_id == "ctx-1"


async def test_followup_can_carry_only_the_task_id() -> None:
    transport = _FakeTransport([[_input_required()], [_completed()]])

    await run_pure_client_flow(
        transport=transport,  # type: ignore[arg-type]
        user_request="Create a RAN energy saving task.",
        answer=lambda event: "Use {00:00~06:00,10Mbps}.",
        followup="task",
    )

    follow_up = transport.requests[1][0]
    assert follow_up.message.task_id == "task-1"
    assert follow_up.message.context_id == ""


async def test_an_approval_input_required_is_not_answered() -> None:
    event = _input_required()
    event["metadata"] = {"pure": {"kind": "approval", "phase": "approval", "round": 1}}
    transport = _FakeTransport([[event]])

    result = await run_pure_client_flow(
        transport=transport,  # type: ignore[arg-type]
        user_request="Create a RAN energy saving task.",
        answer=lambda event: "should not be sent",
    )

    assert len(transport.requests) == 1
    assert result.final_state != FINAL_STATE_TASK_COMPLETED
