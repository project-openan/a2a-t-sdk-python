"""Tests of the plain A2A client flow."""

from __future__ import annotations

from a2a.client.client import ClientCallContext
from a2a.types import SendMessageRequest
from protocol.plain_client_flow import run_plain_client_flow
from shared.run_constants import (
    FINAL_STATE_TASK_COMPLETED,
    FINAL_STATE_TASK_REJECTED,
)

CONTEXT_ID = "ctx-plain-flow"
REQUEST = "Create a RAN energy saving task in Region A with a guaranteed target."


class _Transport:
    """Fake transport replaying one event batch and recording the request."""

    def __init__(self, batch: list[dict[str, object]]) -> None:
        self._batch = batch
        self.requests: list[SendMessageRequest] = []
        self.call_contexts: list[ClientCallContext] = []

    async def send(
        self,
        request: SendMessageRequest,
        call_context: ClientCallContext,
    ) -> list[dict[str, object]]:
        self.requests.append(request)
        self.call_contexts.append(call_context)
        return self._batch


def _status(state: str) -> dict[str, object]:
    return {
        "kind": "status",
        "task_id": "task-1",
        "context_id": CONTEXT_ID,
        "state": state,
    }


def _report() -> dict[str, object]:
    return {
        "kind": "artifact",
        "task_id": "task-1",
        "context_id": CONTEXT_ID,
        "artifact_id": "artifact-1",
        "name": "energy saving intent report",
        "data": {"summary": "Plan applied", "estimated_saving_kwh": 12.5},
    }


async def test_plain_flow_sends_one_message_without_extensions() -> None:
    transport = _Transport(
        [_status("TASK_STATE_WORKING"), _report(), _status("TASK_STATE_COMPLETED")]
    )

    result = await run_plain_client_flow(transport=transport, user_request=REQUEST)

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert result.task_id == "task-1"
    assert result.report == {"summary": "Plan applied", "estimated_saving_kwh": 12.5}
    assert result.decisions == []
    request = transport.requests[0]
    assert request.message.parts[0].text == REQUEST
    assert not request.message.metadata
    assert transport.call_contexts[0].service_parameters["A2A-Extensions"] == ""


async def test_plain_flow_reports_a_rejected_task_without_a_report() -> None:
    transport = _Transport([_status("TASK_STATE_REJECTED")])

    result = await run_plain_client_flow(transport=transport, user_request=REQUEST)

    assert result.final_state == FINAL_STATE_TASK_REJECTED
    assert result.report is None


async def test_plain_flow_marks_the_outbound_span_as_plain() -> None:
    spans: list[dict[str, object]] = []
    transport = _Transport([_status("TASK_STATE_COMPLETED")])

    await run_plain_client_flow(
        transport=transport,
        user_request=REQUEST,
        trace_sink=spans.append,
    )

    sent = next(span for span in spans if span["kind"] == "message_sent")
    detail = sent["detail"]
    assert isinstance(detail, dict)
    assert detail["protocol"] == "plain"
    assert detail["summary"] == REQUEST
    assert detail["metadataKeys"] == []
