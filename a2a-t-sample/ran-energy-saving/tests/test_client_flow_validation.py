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
from shared.run_constants import FINAL_STATE_INPUT_INVALID, FINAL_STATE_TASK_COMPLETED
from shared.trace import PHASE_TASK_PROMPT

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
    enforce_required_fields: bool = False,
    require_rate_target: bool = True,
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
        require_rate_target=require_rate_target,
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
    """A site-level request still needs its concrete guaranteed rate."""
    prompt_client = ValidatingPromptClient(
        filled_data={
            "operationType": "Create",
            "region": "A2ATestRegion",
            "siteList": "NRES_XCS_6102937",
            "guaranteedThroughputTarget": None,
        }
    )
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(
        prompt_client=prompt_client,
        transport=transport,
        trace_sink=traces,
        enforce_required_fields=True,
    )

    assert result.final_state == FINAL_STATE_INPUT_INVALID
    assert transport.requests == []
    detail = _validation_spans(traces)[0]["detail"]
    assert isinstance(detail, dict)
    assert detail["verdict"] == "blocked"
    assert detail["errors"][0]["code"] == "content.param_missing"
    assert detail["errors"][0]["facts"]["section_label"] == "Rate guarantee goal"
    assert "Rate guarantee goal" in detail["errors"][0]["message"]


@pytest.mark.asyncio
async def test_an_area_level_request_may_omit_the_rate_target() -> None:
    """An area-level Create without a rate is valid; the server executes its default mode."""
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
        enforce_required_fields=True,
        require_rate_target=False,
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert len(transport.requests) == 1
    assert _validation_spans(traces)[0]["detail"]["verdict"] == "passed"


@pytest.mark.asyncio
async def test_a_caller_can_disable_the_required_gate() -> None:
    """A direct caller may still opt out; the sample's entry points do not."""
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
async def test_unavailable_validation_reports_the_validator_failure_not_empty_fields() -> None:
    """The business gate must not hide a broken validator behind "field is empty"."""
    validator_error = _content_error(
        ErrorCatalog.LLM_RESPONSE_INVALID,
        slot_errors=[_slot_error("llm.response_invalid", slot_name="_llm")],
    )
    prompt_client = ValidatingPromptClient(validation_errors=[validator_error, validator_error])
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(
        prompt_client=prompt_client,
        transport=transport,
        trace_sink=traces,
        enforce_required_fields=True,
    )

    assert result.final_state == FINAL_STATE_INPUT_INVALID
    assert transport.requests == []
    detail = _validation_spans(traces)[0]["detail"]
    assert isinstance(detail, dict)
    assert detail["verdict"] == "blocked"
    assert detail["reason"] == "llm.response_invalid"
    assert detail["attempts"] == 2
    codes = [error["code"] for error in detail["errors"]]
    assert codes[0] == "llm.response_invalid"
    assert "content.param_missing" in codes
    missing = [error for error in detail["errors"] if error["code"] == "content.param_missing"]
    assert any(error["facts"]["section_label"] == "Operation Type" for error in missing)


@pytest.mark.asyncio
async def test_a_schema_echo_is_retried_before_the_validation_is_used() -> None:
    """``llm.response_invalid`` is retryable; the second attempt may return the contract shape."""
    validator_error = _content_error(
        ErrorCatalog.LLM_RESPONSE_INVALID,
        slot_errors=[_slot_error("llm.response_invalid", slot_name="_llm")],
    )
    prompt_client = ValidatingPromptClient(
        validation_errors=[validator_error, None],
        filled_data={
            "operationType": "Create",
            "region": "A2ATestRegion",
            "guaranteedThroughputTarget": "{00:00~06:00,2Mbps}",
        },
    )
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(prompt_client=prompt_client, transport=transport, trace_sink=traces)

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert len(prompt_client.validation_calls) == 2
    detail = _validation_spans(traces)[0]["detail"]
    assert isinstance(detail, dict)
    assert detail["verdict"] == "passed"
    assert detail["attempts"] == 2


@pytest.mark.asyncio
async def test_a_semantic_rejection_is_not_retried() -> None:
    """Only the contract failure is retried; a real semantic rejection is final."""
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
    assert len(prompt_client.validation_calls) == 1
    detail = _validation_spans(traces)[0]["detail"]
    assert isinstance(detail, dict)
    assert detail["attempts"] == 1


def test_required_field_labels_come_from_the_schema_title() -> None:
    from protocol.client_flow import _missing_required_fields

    schema = {
        "type": "object",
        "properties": {
            "siteList": {"type": "string", "title": "Site List", "x-title-zh-CN": "站点列表"},
        },
        "required": ["siteList"],
    }

    assert _missing_required_fields({}, schema, "en-US")[0]["facts"]["section_label"] == "Site List"
    assert _missing_required_fields({}, schema, "zh-CN")[0]["facts"]["section_label"] == "站点列表"


def test_a_required_field_without_a_title_falls_back_to_its_name() -> None:
    from protocol.client_flow import _missing_required_fields

    schema = {
        "type": "object",
        "properties": {"newField": {"type": "string"}},
        "required": ["newField"],
    }

    assert _missing_required_fields({}, schema, "en-US")[0]["facts"]["section_label"] == "newField"


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
    # ``llm.response_invalid`` is retried once before the failure is reported, so the double must
    # fail on both attempts; the other codes fail immediately.
    attempts = 2 if code == ErrorCatalog.LLM_RESPONSE_INVALID else 1
    prompt_client = ValidatingPromptClient(validation_errors=[_content_error(code) for _ in range(attempts)])
    transport = RecordingTransport([_completed_task()])
    traces: list[dict[str, object]] = []

    result = await _run(prompt_client=prompt_client, transport=transport, trace_sink=traces)

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert len(transport.requests) == 1
    assert len(prompt_client.validation_calls) == attempts
    detail = _validation_spans(traces)[0]["detail"]
    assert isinstance(detail, dict)
    assert detail["verdict"] == "unavailable"
    assert detail["attempts"] == attempts


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


