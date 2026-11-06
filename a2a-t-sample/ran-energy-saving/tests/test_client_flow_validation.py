"""Tests of the client-side pre-send Task-T validation gate."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest
from a2a.client.client import ClientCallContext
from a2a.types import SendMessageRequest
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import ContentValidationError, PromptGenerationError, SlotValidationError
from a2a_t.core.metadata import TASK_T_EXTENSION_URI, MetadataContent
from a2a_t.core.standard_templates import ENERGY_SAVING_URI
from a2a_t.core.validation_pipeline import FilledParamData
from protocol.client_flow import run_client_flow
from protocol.decision_provider import ScriptedDecisionProvider
from protocol.run_spec import ClientPolicy
from shared.run_constants import FINAL_STATE_INPUT_INVALID, FINAL_STATE_TASK_COMPLETED
from shared.task_schema import TASK_PARAM_SCHEMA
from shared.trace import PHASE_TASK_MODIFY, PHASE_TASK_PROMPT

_CONTEXT_ID = "ctx-validation"


@dataclass
class ValidatingPromptClient:
    """SDK client double that scripts one validation outcome per call."""

    validation_errors: list[Exception | None] = field(default_factory=list)
    generation_errors: list[Exception | None] = field(default_factory=list)
    filled_data: Mapping[str, object] | None = None
    validation_calls: list[tuple[str, Mapping[str, object], str]] = field(default_factory=list)
    generation_calls: list[str] = field(default_factory=list)

    def generate_task_prompt_from_text(self, text: str, template_uri: str) -> MetadataContent:
        self.generation_calls.append(text)
        if self.generation_errors:
            error = self.generation_errors.pop(0)
            if error is not None:
                raise error
        return MetadataContent(
            template_uri=template_uri,
            prompt_text=f"Task-T prompt for: {text}",
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

    def validate_task_prompt_and_data_filling(
        self,
        prompt: str,
        schema: Mapping[str, object],
        template_uri: str,
    ) -> object:
        self.validation_calls.append((prompt, schema, template_uri))
        error = self.validation_errors.pop(0) if self.validation_errors else None
        if error is not None:
            raise error
        if self.filled_data is not None:
            return FilledParamData(data=dict(self.filled_data))
        return object()


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


def _content_error(
    code: ErrorCatalog,
    *,
    slot_errors: list[SlotValidationError] | None = None,
) -> ContentValidationError:
    return ContentValidationError(
        code,
        {"reason": "test"},
        message=f"test failure: {code.value}",
        errors=slot_errors,
    )


def _slot_error(code: str, slot_name: str = "guaranteedThroughputTarget") -> SlotValidationError:
    return SlotValidationError(
        slot_name=slot_name,
        code=code,
        message=f"test slot failure: {code}",
        facts={"section_label": "task_context"},
    )


def _working_task() -> list[dict[str, object]]:
    return [
        {
            "kind": "status",
            "task_id": "task-1",
            "context_id": _CONTEXT_ID,
            "state": "TASK_STATE_WORKING",
        }
    ]


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
    prompt_client: ValidatingPromptClient,
    transport: RecordingTransport,
    trace_sink: list[dict[str, object]],
    validate: bool = True,
    enforce_required_fields: bool = True,
    policy: ClientPolicy | None = None,
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
        validate_before_send=validate,
        enforce_required_fields=enforce_required_fields,
        policy=policy or ClientPolicy(),
    )


def _validation_spans(traces: list[dict[str, object]]) -> list[dict[str, object]]:
    return [span for span in traces if span.get("kind") == "task_validation"]


@pytest.mark.asyncio
async def test_blocked_validation_sends_nothing_and_ends_input_invalid() -> None:
    prompt_client = ValidatingPromptClient(
        validation_errors=[
            _content_error(
                ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED,
                slot_errors=[_slot_error("content.format_error")],
            )
        ]
    )
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(prompt_client=prompt_client, transport=transport, trace_sink=traces)

    assert result.final_state == FINAL_STATE_INPUT_INVALID
    assert transport.requests == []
    assert len(prompt_client.validation_calls) == 1
    spans = _validation_spans(traces)
    assert len(spans) == 1
    detail = spans[0]["detail"]
    assert isinstance(detail, dict)
    assert detail["verdict"] == "blocked"
    assert detail["blocking"] is True
    assert detail["errors"][0]["code"] == "content.format_error"
    assert spans[0]["phase"] == PHASE_TASK_PROMPT


@pytest.mark.asyncio
async def test_a_negotiable_validation_error_still_reaches_the_server() -> None:
    prompt_client = ValidatingPromptClient(
        validation_errors=[
            _content_error(
                ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED,
                slot_errors=[_slot_error("content.param_missing")],
            )
        ]
    )
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(prompt_client=prompt_client, transport=transport, trace_sink=traces)

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert len(transport.requests) == 1
    detail = _validation_spans(traces)[0]["detail"]
    assert isinstance(detail, dict)
    assert detail["verdict"] == "negotiable"
    assert detail["blocking"] is False
    assert detail["negotiationKind"] == "information"


@pytest.mark.asyncio
async def test_a_missing_entry_field_is_negotiable_information() -> None:
    """A missing Rate inside Task Context is information the operator can supply."""
    prompt_client = ValidatingPromptClient(
        validation_errors=[
            _content_error(
                ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED,
                slot_errors=[_slot_error("content.entry_field_missing")],
            )
        ]
    )
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(prompt_client=prompt_client, transport=transport, trace_sink=traces)

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert len(transport.requests) == 1
    detail = _validation_spans(traces)[0]["detail"]
    assert isinstance(detail, dict)
    assert detail["verdict"] == "negotiable"
    assert detail["negotiationKind"] == "information"


@pytest.mark.asyncio
async def test_the_oss_agent_intercepts_a_missing_required_field() -> None:
    """The caller schema's required values are the OSS agent's own business gate."""
    prompt_client = ValidatingPromptClient(
        filled_data={
            "operationType": "Create",
            "region": "A2ATestRegion",
            "guaranteedThroughputTarget": None,
        }
    )
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(prompt_client=prompt_client, transport=transport, trace_sink=traces)

    assert result.final_state == FINAL_STATE_INPUT_INVALID
    assert transport.requests == []
    detail = _validation_spans(traces)[0]["detail"]
    assert isinstance(detail, dict)
    assert detail["verdict"] == "blocked"
    assert detail["errors"][0]["code"] == "content.param_missing"
    assert detail["errors"][0]["facts"]["section_label"] == "Rate guarantee goal"
    assert "Rate guarantee goal" in detail["errors"][0]["message"]


@pytest.mark.asyncio
async def test_a_curated_request_can_skip_the_oss_required_gate() -> None:
    """The information-negotiation demo deliberately omits a required value."""
    prompt_client = ValidatingPromptClient(
        filled_data={
            "operationType": "Create",
            "region": "A2ATestRegion",
            "guaranteedThroughputTarget": None,
        }
    )
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(
        prompt_client=prompt_client,
        transport=transport,
        trace_sink=traces,
        enforce_required_fields=False,
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert len(transport.requests) == 1
    assert _validation_spans(traces)[0]["detail"]["verdict"] == "passed"


@pytest.mark.asyncio
async def test_a_complete_caller_schema_still_reaches_the_server() -> None:
    prompt_client = ValidatingPromptClient(
        filled_data={
            "operationType": "Create",
            "region": "A2ATestRegion",
            "guaranteedThroughputTarget": "{00:00~06:00,2Mbps}",
        }
    )
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(prompt_client=prompt_client, transport=transport, trace_sink=traces)

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert len(transport.requests) == 1
    assert _validation_spans(traces)[0]["detail"]["verdict"] == "passed"


@pytest.mark.asyncio
async def test_a_slot_generation_failure_blocks_with_structured_errors() -> None:
    """A failed extraction has no Task-T to send; the operator edits the request and retries."""
    error = PromptGenerationError(
        ErrorCatalog.SLOT_CONSTRAINT_VIOLATED,
        {"slot_label": "Task Target", "actual": "3G"},
        message="The value of 'Task Target' (3G) is not within the allowed range",
        failed_parameters=[
            SlotValidationError(
                slot_name="task_target",
                code="slot.constraint_violated",
                message="The value of 'Task Target' (3G) is not within the allowed range",
                facts={"slot_label": "Task Target", "actual": "3G"},
            )
        ],
    )
    prompt_client = ValidatingPromptClient(generation_errors=[error])
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(prompt_client=prompt_client, transport=transport, trace_sink=traces)

    assert result.final_state == FINAL_STATE_INPUT_INVALID
    assert transport.requests == []
    assert prompt_client.validation_calls == []
    spans = _validation_spans(traces)
    assert len(spans) == 1
    detail = spans[0]["detail"]
    assert isinstance(detail, dict)
    assert detail["verdict"] == "blocked"
    assert detail["blocking"] is True
    assert detail["errors"][0]["slotName"] == "task_target"
    assert detail["errors"][0]["code"] == "slot.constraint_violated"
    assert detail["errors"][0]["facts"]["actual"] == "3G"


@pytest.mark.asyncio
async def test_an_infrastructure_generation_failure_is_not_misreported_as_input() -> None:
    """Only business slot errors become an editable blocked result."""
    error = PromptGenerationError(
        ErrorCatalog.LLM_INVOCATION_FAILED,
        {"provider": "openai", "reason": "Request timed out."},
        message="LLM invocation failed (provider openai): Request timed out.",
    )
    prompt_client = ValidatingPromptClient(generation_errors=[error])
    transport = RecordingTransport([_completed_task()])

    with pytest.raises(PromptGenerationError):
        await _run(prompt_client=prompt_client, transport=transport, trace_sink=[])

    assert transport.requests == []


@pytest.mark.parametrize(
    "code",
    [
        ErrorCatalog.LLM_INVOCATION_FAILED,
        ErrorCatalog.LLM_NOT_CONFIGURED,
        ErrorCatalog.LLM_RESPONSE_INVALID,
    ],
)
@pytest.mark.asyncio
async def test_validation_infrastructure_failures_let_the_request_through(
    code: ErrorCatalog,
) -> None:
    prompt_client = ValidatingPromptClient(validation_errors=[_content_error(code)])
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(prompt_client=prompt_client, transport=transport, trace_sink=traces)

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert len(transport.requests) == 1
    detail = _validation_spans(traces)[0]["detail"]
    assert isinstance(detail, dict)
    assert detail["verdict"] == "unavailable"


@pytest.mark.asyncio
async def test_unexpected_validation_exceptions_are_treated_as_unavailable() -> None:
    prompt_client = ValidatingPromptClient(validation_errors=[RuntimeError("boom")])
    transport = RecordingTransport([_completed_task()])

    result = await _run(prompt_client=prompt_client, transport=transport, trace_sink=[])

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_disabled_validation_never_calls_the_sdk() -> None:
    prompt_client = ValidatingPromptClient()
    transport = RecordingTransport([_completed_task()])

    result = await _run(
        prompt_client=prompt_client,
        transport=transport,
        trace_sink=[],
        validate=False,
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert prompt_client.validation_calls == []
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_a_blocked_modify_cancels_the_baseline_task() -> None:
    policy = ClientPolicy(
        initial_data=lambda language: (
            {
                "operationType": "Create",
                "region": "Region A",
                "guaranteedThroughputTarget": "{00:00~07:00,2Mbps}",
            },
            TASK_PARAM_SCHEMA,
        ),
        follow_up_data=lambda language: (
            {
                "operationType": "Modify",
                "region": "Region A",
                "guaranteedThroughputTarget": "{00:00~07:00,10Mbps}",
            },
            TASK_PARAM_SCHEMA,
        ),
    )
    prompt_client = ValidatingPromptClient(
        validation_errors=[
            None,
            _content_error(
                ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED,
                slot_errors=[_slot_error("content.format_error")],
            ),
        ]
    )
    transport = RecordingTransport([_working_task()])
    traces: list[dict[str, object]] = []

    result = await _run(
        prompt_client=prompt_client,
        transport=transport,
        trace_sink=traces,
        policy=policy,
    )

    assert result.final_state == FINAL_STATE_INPUT_INVALID
    assert len(transport.requests) == 1  # the baseline only; the Modify never leaves
    assert transport.cancelled == ["task-1"]
    spans = _validation_spans(traces)
    assert [span["phase"] for span in spans] == [PHASE_TASK_PROMPT, PHASE_TASK_MODIFY]
    modify_detail = spans[1]["detail"]
    assert isinstance(modify_detail, dict)
    assert modify_detail["verdict"] == "blocked"
