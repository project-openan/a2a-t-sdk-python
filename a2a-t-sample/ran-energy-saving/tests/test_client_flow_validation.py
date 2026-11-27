"""Tests of the client-side pre-send Task-T evidence.

The client no longer calls the SDK content validation. The only local interception is the slot
extraction (``slot.*``), and a generated Task-T is recorded as local passing evidence whose values
are read back from the prompt itself.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest
from a2a.client.client import ClientCallContext
from a2a.types import SendMessageRequest
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import PromptGenerationError, SlotValidationError
from a2a_t.core.metadata import TASK_T_EXTENSION_URI, MetadataContent
from a2a_t.core.standard_templates import ENERGY_SAVING_URI
from google.protobuf.json_format import MessageToDict
from protocol.client_flow import (
    CorrectionProvider,
    CorrectionRequest,
    run_client_flow,
    scripted_correction,
)
from tests.support.decision_provider import ScriptedDecisionProvider
from shared.run_constants import FINAL_STATE_INPUT_INVALID, FINAL_STATE_TASK_COMPLETED
from shared.trace import PHASE_TASK_PROMPT

_CONTEXT_ID = "ctx-validation"

_TASK_T = """## Operation Type
Create

## Task Object
A2ATestRegion

## Task Target
Maximize energy saving under the task context.

## Task Context
Time-segmented rate guarantee target: {08:00-12:00, 10Mbps}

## Expected Output
Output the intent report.
"""

_TASK_T_WITHOUT_RATE = """## Operation Type
Modify

## Task Object
A2ATestRegion

## Task Target
Maximize energy saving under the task context.

## Task Context
Site list: ["NES_27A_321071", "NRES_XCS_6102937"], Background event: power outage from 08:00 to 12:00.

## Expected Output
Output the intent report.
"""


@dataclass
class ScriptedPromptClient:
    """SDK client double that scripts one generation outcome per call."""

    outcomes: list[MetadataContent | Exception] = field(default_factory=list)
    generation_calls: list[str] = field(default_factory=list)

    def generate_task_prompt_from_text(self, text: str, template_uri: str) -> MetadataContent:
        self.generation_calls.append(text)
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is not None:
            return outcome
        return MetadataContent(
            template_uri=template_uri,
            prompt_text=_TASK_T,
            extension_uri=TASK_T_EXTENSION_URI,
        )

    def generate_task_prompt_from_data_with_schema(
        self,
        data: Mapping[str, object],
        schema: Mapping[str, object],
        template_uri: str,
    ) -> MetadataContent:
        del schema
        return MetadataContent(
            template_uri=template_uri,
            prompt_text=f"Task-T data: {json.dumps(dict(data), sort_keys=True)}",
            extension_uri=TASK_T_EXTENSION_URI,
        )


class RecordingTransport:
    """Replay one batch per send, recording requests and cancels."""

    def __init__(self, batches: list[list[dict[str, object]]]) -> None:
        self._batches = list(batches)
        self.requests: list[SendMessageRequest] = []
        self.cancelled: list[str] = []

    async def send(
        self,
        request: SendMessageRequest,
        call_context: ClientCallContext,
    ) -> list[dict[str, object]]:
        del call_context
        self.requests.append(request)
        return self._batches.pop(0)

    async def cancel(self, task_id: str, call_context: ClientCallContext) -> None:
        del call_context
        self.cancelled.append(task_id)


def _slot_failure(
    *,
    slot_name: str = "task_target",
    code: str = "slot.constraint_violated",
    label: str = "Task Target",
) -> PromptGenerationError:
    return PromptGenerationError(
        ErrorCatalog.SLOT_CONSTRAINT_VIOLATED,
        {"slot_label": label},
        message=f"The value of '{label}' is not allowed",
        failed_parameters=[
            SlotValidationError(
                slot_name=slot_name,
                code=code,
                message=f"The value of '{label}' is not allowed",
                facts={"slot_label": label},
            )
        ],
    )


def _completed_task() -> list[dict[str, object]]:
    return [
        {
            "kind": "status",
            "task_id": "task-1",
            "context_id": _CONTEXT_ID,
            "state": "TASK_STATE_WORKING",
        },
        {
            "kind": "artifact",
            "task_id": "task-1",
            "context_id": _CONTEXT_ID,
            "artifact_id": "artifact-1",
            "name": "energy saving intent report",
            "data": {"summary": "done"},
        },
        {
            "kind": "status",
            "task_id": "task-1",
            "context_id": _CONTEXT_ID,
            "state": "TASK_STATE_COMPLETED",
        },
    ]


async def _run(
    *,
    prompt_client: ScriptedPromptClient,
    transport: RecordingTransport,
    trace_sink: list[dict[str, object]],
    correction: CorrectionProvider | None = None,
):
    return await run_client_flow(
        prompt_client=prompt_client,
        transport=transport,
        user_request="create ran energy saving task",
        provider=ScriptedDecisionProvider([]),
        task_template_uri=ENERGY_SAVING_URI,
        language="en-US",
        trace_sink=trace_sink.append,
        single_extension_per_message=False,
        correction=correction,
    )


def _validation_spans(traces: list[dict[str, object]]) -> list[dict[str, object]]:
    return [span for span in traces if span.get("kind") == "task_validation"]


def _detail(span: dict[str, object]) -> dict[str, object]:
    detail = span.get("detail")
    assert isinstance(detail, dict)
    return detail


def _sent_task_t(transport: RecordingTransport) -> str:
    metadata = MessageToDict(transport.requests[0].message.metadata)
    return str(next(value for key, value in metadata.items() if "Task-T" in key))


@pytest.mark.asyncio
async def test_a_generated_task_t_is_sent_with_local_passing_evidence() -> None:
    prompt_client = ScriptedPromptClient()
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(prompt_client=prompt_client, transport=transport, trace_sink=traces)

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert len(transport.requests) == 1
    spans = _validation_spans(traces)
    assert len(spans) == 1
    detail = _detail(spans[0])
    assert detail["verdict"] == "passed"
    assert detail["source"] == "local"
    assert detail["blocking"] is False
    assert spans[0]["phase"] == PHASE_TASK_PROMPT
    # The values the accept refill uses are read back from the rendered prompt.
    assert detail["filled"]["operationType"] == "Create"
    assert detail["filled"]["region"] == "A2ATestRegion"
    assert detail["filled"]["guaranteedThroughputTarget"] == "{08:00-12:00,10Mbps}"


@pytest.mark.asyncio
async def test_a_site_level_request_without_a_rate_is_still_sent() -> None:
    """There is no local required-field gate any more; the server negotiates the missing rate."""
    prompt_client = ScriptedPromptClient(
        outcomes=[
            MetadataContent(
                template_uri=ENERGY_SAVING_URI,
                prompt_text=_TASK_T_WITHOUT_RATE,
                extension_uri=TASK_T_EXTENSION_URI,
            )
        ]
    )
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(prompt_client=prompt_client, transport=transport, trace_sink=traces)

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert len(transport.requests) == 1
    detail = _detail(_validation_spans(traces)[0])
    assert detail["verdict"] == "passed"
    assert detail["filled"]["siteList"] == "NES_27A_321071, NRES_XCS_6102937"
    assert detail["filled"]["backgroundEvent"].startswith("power outage")
    assert "guaranteedThroughputTarget" not in detail["filled"]


@pytest.mark.asyncio
async def test_a_slot_generation_failure_blocks_with_structured_errors() -> None:
    """A failed extraction has no Task-T to send; the operator edits the request and retries."""
    prompt_client = ScriptedPromptClient(outcomes=[_slot_failure()])
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(prompt_client=prompt_client, transport=transport, trace_sink=traces)

    assert result.final_state == FINAL_STATE_INPUT_INVALID
    assert transport.requests == []
    spans = _validation_spans(traces)
    assert len(spans) == 1
    detail = _detail(spans[0])
    assert detail["verdict"] == "blocked"
    assert detail["blocking"] is True
    assert detail["errors"][0]["slotName"] == "task_target"
    assert detail["errors"][0]["code"] == "slot.constraint_violated"
    assert detail["errors"][0]["facts"]["slot_label"] == "Task Target"


@pytest.mark.asyncio
async def test_an_infrastructure_generation_failure_is_not_misreported_as_input() -> None:
    """Only business slot errors become an editable blocked result."""
    error = PromptGenerationError(
        ErrorCatalog.LLM_INVOCATION_FAILED,
        {"provider": "openai", "reason": "Request timed out."},
        message="LLM invocation failed (provider openai): Request timed out.",
    )
    prompt_client = ScriptedPromptClient(outcomes=[error])
    transport = RecordingTransport([_completed_task()])

    with pytest.raises(PromptGenerationError):
        await _run(prompt_client=prompt_client, transport=transport, trace_sink=[])

    assert transport.requests == []


@pytest.mark.asyncio
async def test_a_blocked_request_is_corrected_inside_the_run_and_then_sent() -> None:
    """The slot block is a run phase: the correction is a later input of the same run."""
    corrected = MetadataContent(
        template_uri=ENERGY_SAVING_URI,
        prompt_text=f"{_TASK_T}\n<!-- for NR cells -->\n",
        extension_uri=TASK_T_EXTENSION_URI,
    )
    prompt_client = ScriptedPromptClient(outcomes=[_slot_failure(), corrected])
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(
        prompt_client=prompt_client,
        transport=transport,
        trace_sink=traces,
        correction=scripted_correction("create ran energy saving task for NR cells"),
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert prompt_client.generation_calls == [
        "create ran energy saving task",
        "create ran energy saving task for NR cells",
    ]
    assert "for NR cells" in _sent_task_t(transport)

    # A slot block happens before a Task-T exists, so only the corrected attempt renders one.
    rendered = [span for span in traces if span.get("kind") == "task_prompt_rendered"]
    assert [_detail(span)["attempt"] for span in rendered] == [2]

    corrections = [span for span in traces if span.get("kind") == "correction_applied"]
    assert len(corrections) == 1
    detail = _detail(corrections[0])
    assert detail["attempt"] == 1
    assert detail["originalRequest"] == "create ran energy saving task"
    assert detail["correctedRequest"] == "create ran energy saving task for NR cells"
    assert detail["verdict"] == "blocked"

    kinds = [span.get("kind") for span in traces]
    assert kinds.index("correction_applied") > kinds.index("task_validation")
    assert len(_validation_spans(traces)) == 2
    assert _detail(_validation_spans(traces)[1])["verdict"] == "passed"


@pytest.mark.asyncio
async def test_a_corrected_request_that_blocks_again_ends_input_invalid() -> None:
    prompt_client = ScriptedPromptClient(outcomes=[_slot_failure(), _slot_failure()])
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(
        prompt_client=prompt_client,
        transport=transport,
        trace_sink=traces,
        correction=scripted_correction("create ran energy saving task for NR cells"),
    )

    assert result.final_state == FINAL_STATE_INPUT_INVALID
    assert transport.requests == []
    assert len(prompt_client.generation_calls) == 2
    assert len([span for span in traces if span.get("kind") == "correction_applied"]) == 1


@pytest.mark.asyncio
async def test_a_passing_gate_never_asks_for_a_correction() -> None:
    prompt_client = ScriptedPromptClient()
    transport = RecordingTransport([_completed_task()])
    asked: list[CorrectionRequest] = []

    async def provider(request: CorrectionRequest) -> str | None:
        asked.append(request)
        return "should not be used"

    result = await _run(
        prompt_client=prompt_client,
        transport=transport,
        trace_sink=[],
        correction=provider,
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert asked == []
    assert prompt_client.generation_calls == ["create ran energy saving task"]
    assert "for NR cells" not in _sent_task_t(transport)
