"""Tests for the pre-task A2A-T negotiation handler."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import pytest
from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext, UnauthenticatedUser
from a2a.types import Message, Role, SendMessageRequest, Task, TaskState, TaskStatus
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATError, ContentValidationError, SlotValidationError
from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    TASK_T_EXTENSION_URI,
    TEMPLATE_URI_METADATA_KEY,
    MetadataContent,
    NegotiationContext,
    NegotiationPerformative,
)
from a2a_t.core.standard_templates import (
    FEASIBILITY_NEGOTIATION_PROPOSE_URI,
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
    NEGOTIATION_ABORT_URI,
    TARGET_NEGOTIATION_ACCEPT_REJECT_URI,
    TARGET_NEGOTIATION_PROPOSE_URI,
)
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.server.a2at_server import A2ATServer
from demo.composition import install_offline_provider
from demo.scenario import (
    EXAMPLE_REQUESTS,
    SCENARIO_FEASIBILITY,
    SCENARIO_INFORMATION,
    SCENARIO_TARGET,
)
from demo.task_t_precheck import RanDataTaskTPrecheck
from google.protobuf.json_format import MessageToDict
from protocol.messages import (
    END_MESSAGE_TEXT,
    SAMPLE_NEGOTIATION_OPTIONS_METADATA_KEY,
    SAMPLE_PROPOSAL_METADATA_KEY,
    STRUCTURED_ERRORS_METADATA_KEY,
)
from protocol.pre_task_negotiation import NegotiationOutcome, PreTaskNegotiationHandler
from protocol.server_app import prepare_sdk_env_path
from shared.config import SampleConfig

CONTEXT_ID = "ctx-negotiation"
TASK_PROMPT = "Create a RAN energy saving task in Region A."


def _metadata(message: Message) -> dict[str, Any]:
    """Decode message metadata into a plain dict (the protobuf stubs model no dynamic keys)."""
    return MessageToDict(message.metadata)


class FakePromptServer:
    """Recording test double of the A2ATServer negotiation surface."""

    def __init__(self) -> None:
        self.task_error: Exception | None = None
        self.accept_error: Exception | None = None
        self.propose_error: Exception | None = None
        self.propose_generation_error: Exception | None = None
        self.reject_error: Exception | None = None
        self.abort_error: Exception | None = None
        self.task_calls: list[tuple[str, Mapping[str, object], str]] = []
        self.accept_calls: list[tuple[str | None, NegotiationContext | None, str]] = []
        self.propose_calls: list[tuple[str | None, NegotiationContext | None, str]] = []
        self.reject_calls: list[tuple[str | None, NegotiationContext | None, str]] = []
        self.abort_calls: list[tuple[str | None, NegotiationContext | None, str]] = []
        self.propose_generations: list[tuple[str | None, NegotiationContext, str]] = []
        self.abort_generations: list[tuple[str | None, NegotiationContext, str]] = []
        self.task_params: dict[str, object] | None = None

    def validate_task_prompt_and_data_filling(
        self, prompt: str, schema: Mapping[str, object], template_uri: str
    ) -> FilledParamData:
        self.task_calls.append((prompt, schema, template_uri))
        if self.task_error is not None:
            raise self.task_error
        return FilledParamData(
            self.task_params
            if self.task_params is not None
            else {
                "operationType": "Create",
                "region": "Region A",
                "guaranteedThroughputTarget": "{00:00~06:00,2Mbps}",
            }
        )

    def validate_accept_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: str,
    ) -> FilledParamData:
        del schema
        self.accept_calls.append((prompt, context, template_uri))
        if self.accept_error is not None:
            raise self.accept_error
        return FilledParamData({"round": context.round if context else 0})

    def validate_propose_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: str,
    ) -> FilledParamData:
        del schema
        self.propose_calls.append((prompt, context, template_uri))
        if self.propose_error is not None:
            raise self.propose_error
        return FilledParamData({"round": context.round if context else 0})

    def validate_reject_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: str,
    ) -> FilledParamData:
        del schema
        self.reject_calls.append((prompt, context, template_uri))
        if self.reject_error is not None:
            raise self.reject_error
        return FilledParamData({})

    def validate_abort_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: str,
    ) -> FilledParamData:
        del schema
        self.abort_calls.append((prompt, context, template_uri))
        if self.abort_error is not None:
            raise self.abort_error
        return FilledParamData({})

    def generate_negotiation_propose_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent:
        self.propose_generations.append((text, context, template_uri))
        if self.propose_generation_error is not None:
            raise self.propose_generation_error
        return MetadataContent(
            template_uri=template_uri,
            prompt_text=f"Generated propose for round {context.round}",
            extension_uri=NEGOTIATION_T_EXTENSION_URI,
            negotiation_context=context,
        )

    def generate_negotiation_abort_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent:
        self.abort_generations.append((text, context, template_uri))
        return MetadataContent(
            template_uri=template_uri,
            prompt_text=f"Generated abort for round {context.round}",
            extension_uri=NEGOTIATION_T_EXTENSION_URI,
            negotiation_context=context,
        )


def _content_error(code: str, slot_name: str = "region") -> ContentValidationError:
    return ContentValidationError(
        ErrorCatalog.CONTENT_SEMANTIC_CONFLICT,
        {"section_label": "task_context", "reason": "rejected"},
        errors=[SlotValidationError(slot_name=slot_name, code=code, message="rejected")],
    )


def _conflict_error(target: str) -> ContentValidationError:
    """A live-validator verdict that the target has overlapping segments."""
    return ContentValidationError(
        ErrorCatalog.CONTENT_SEMANTIC_CONFLICT,
        {"section_label": "task_context", "reason": "overlapping segments"},
        errors=[
            SlotValidationError(
                slot_name="guaranteedThroughputTarget",
                code="content.semantic_conflict",
                message="overlap",
            )
        ],
        params={
            "operationType": "Create",
            "region": "Region A",
            "guaranteedThroughputTarget": target,
        },
    )


def _make_context(
    message: Message,
    *,
    extensions: tuple[str, ...] = (TASK_T_EXTENSION_URI,),
    current_task: Task | None = None,
) -> RequestContext:
    request = SendMessageRequest(message=message)
    call_context = ServerCallContext(
        state={},
        user=UnauthenticatedUser(),
        requested_extensions=set(extensions),
    )
    return RequestContext(call_context=call_context, request=request, task=current_task)


def _initial_message(prompt: str = TASK_PROMPT, context_id: str = CONTEXT_ID) -> Message:
    message = Message(message_id="initial", role=Role.ROLE_USER, context_id=context_id)
    message.parts.add(text="create the task")
    message.metadata[TASK_T_EXTENSION_URI] = prompt
    return message


def _negotiation_message(
    performative: NegotiationPerformative,
    *,
    round_number: int,
    template_uri: str,
    prompt: str = "negotiation content",
    task_prompt: str | None = None,
    sample: Mapping[str, object] | None = None,
) -> Message:
    message = Message(message_id=f"negotiation-{performative.value}", role=Role.ROLE_USER, context_id=CONTEXT_ID)
    message.parts.add(text="negotiation")
    if task_prompt is not None:
        message.metadata[TASK_T_EXTENSION_URI] = task_prompt
    message.metadata[NEGOTIATION_T_EXTENSION_URI] = prompt
    message.metadata[TEMPLATE_URI_METADATA_KEY] = template_uri
    message.metadata[NEGOTIATION_CONTEXT_METADATA_KEY] = {
        "id": CONTEXT_ID,
        "round": round_number,
        "maxRounds": 3,
        "performative": performative.value,
    }
    if sample is not None:
        message.metadata[SAMPLE_PROPOSAL_METADATA_KEY] = dict(sample)  # ty: ignore[invalid-assignment]
    return message


def _gate(server: FakePromptServer | None = None) -> tuple[PreTaskNegotiationHandler, FakePromptServer]:
    resolved = server or FakePromptServer()
    return PreTaskNegotiationHandler(resolved, precheck=RanDataTaskTPrecheck()), resolved


def _assert_no_task(outcome: NegotiationOutcome) -> Message:
    assert outcome.short_circuit is True
    assert outcome.task_request is None
    assert outcome.terminal_task is None
    assert outcome.message is not None
    assert outcome.message.task_id == ""
    return outcome.message


async def test_initial_task_success_hands_the_request_to_the_executor() -> None:
    gate, server = _gate()
    context = _make_context(_initial_message())

    outcome = await gate.handle(context)

    assert outcome.short_circuit is False
    assert outcome.task_request is context
    assert len(server.task_calls) == 1
    assert server.task_calls[0][0] == TASK_PROMPT
    assert context.message is not None
    assert context.message.parts[0].text == TASK_PROMPT


async def test_initial_task_with_a_missing_required_field_opens_information_negotiation() -> None:
    """A model-approved semantic verdict must not bypass the caller schema's required fields."""
    gate, server = _gate()
    server.task_params = {
        "operationType": "Create",
        "region": "Region A",
        "guaranteedThroughputTarget": None,
    }

    message = _assert_no_task(await gate.handle(_make_context(_initial_message())))

    metadata = _metadata(message)
    session = metadata[NEGOTIATION_CONTEXT_METADATA_KEY]
    assert metadata[TEMPLATE_URI_METADATA_KEY] == INFORMATION_NEGOTIATION_PROPOSE_URI
    assert session["round"] == 1
    assert session["performative"] == NegotiationPerformative.PROPOSE.value
    assert server.propose_generations
    proposal_text = server.propose_generations[0][0]
    assert isinstance(proposal_text, str)
    assert "guaranteedThroughputTarget" in proposal_text


_NON_OVERLAPPING_PROMPT = (
    "## Task Context\n\nGuaranteed throughput target: {00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~23:59,10Mbps}."
)

_OVERLAPPING_PROMPT = "## Task Context\n\nGuaranteed throughput target: {17:30~23:00,20Mbps}, {20:00~00:00,10Mbps}."


async def test_a_false_conflict_verdict_does_not_open_a_negotiation() -> None:
    """A live validator can claim an overlap the Task-T prompt does not have."""
    gate, server = _gate()
    server.task_error = _conflict_error("{00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~23:59,10Mbps}")
    context = _make_context(_initial_message(prompt=_NON_OVERLAPPING_PROMPT))

    outcome = await gate.handle(context)

    assert outcome.short_circuit is False
    assert outcome.task_request is context
    assert server.propose_generations == []


async def test_a_verified_conflict_still_opens_the_target_negotiation() -> None:
    """The deterministic check keeps the real overlap case working."""
    gate, server = _gate()
    server.task_error = _conflict_error("{17:30~23:00,20Mbps}, {20:00~00:00,10Mbps}")

    message = _assert_no_task(await gate.handle(_make_context(_initial_message(prompt=_OVERLAPPING_PROMPT))))

    metadata = _metadata(message)
    assert metadata[TEMPLATE_URI_METADATA_KEY] == TARGET_NEGOTIATION_PROPOSE_URI


async def test_unknown_area_proposal_carries_the_served_area_options() -> None:
    gate, server = _gate()
    server.task_params = {
        "operationType": "Create",
        "region": "Region Z",
        "guaranteedThroughputTarget": "{00:00~07:00,2Mbps}, {07:00~23:00,20Mbps}",
    }

    message = _assert_no_task(await gate.handle(_make_context(_initial_message())))

    metadata = _metadata(message)
    options = metadata[SAMPLE_NEGOTIATION_OPTIONS_METADATA_KEY]
    assert options["field"] == "region"
    assert options["requested"] == "Region Z"
    assert [option["value"] for option in options["options"]] == [
        "Region A",
        "Region B",
    ]
    assert all(option["recommended"] is False for option in options["options"])
    assert "Region B" in server.propose_generations[0][0]


async def test_without_a_precheck_the_handler_trusts_the_sdk_verdict() -> None:
    """The seam is optional: a deployment that injects nothing keeps the model verdict."""
    server = FakePromptServer()
    server.task_params = {
        "operationType": "Create",
        "region": "Region A",
        "guaranteedThroughputTarget": None,
    }
    handler = PreTaskNegotiationHandler(server)
    context = _make_context(_initial_message())

    outcome = await handler.handle(context)

    assert outcome.short_circuit is False
    assert outcome.task_request is context


@pytest.mark.parametrize(
    ("task_params", "expected_uri"),
    [
        (
            {
                "operationType": "Create",
                "region": "Region A",
                "guaranteedThroughputTarget": ("{17:30~23:00,20Mbps}, {20:00~00:00,10Mbps}"),
            },
            TARGET_NEGOTIATION_PROPOSE_URI,
        ),
        (
            {
                "operationType": "Create",
                "region": "Region A",
                "guaranteedThroughputTarget": ("{00:00~06:00,2Mbps}, {06:00~18:00,10Mbps}"),
                "backgroundEvent": "power outage from 00:00 to 18:00",
            },
            FEASIBILITY_NEGOTIATION_PROPOSE_URI,
        ),
    ],
)
async def test_semantically_accepted_sample_conflicts_still_open_negotiation(
    task_params: dict[str, object], expected_uri: str
) -> None:
    """Deterministic sample checks cover live models that miss an overlap or outage conflict."""
    gate, server = _gate()
    server.task_params = task_params

    message = _assert_no_task(await gate.handle(_make_context(_initial_message())))

    metadata = _metadata(message)
    assert metadata[TEMPLATE_URI_METADATA_KEY] == expected_uri
    assert metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["round"] == 1


@pytest.mark.parametrize(
    ("code", "expected_uri", "missing_slot"),
    [
        ("content.param_missing", INFORMATION_NEGOTIATION_PROPOSE_URI, "region"),
        ("content.entry_field_missing", INFORMATION_NEGOTIATION_PROPOSE_URI, "guaranteedThroughputTarget"),
        ("content.value_not_allowed", TARGET_NEGOTIATION_PROPOSE_URI, "guaranteedThroughputTarget"),
        ("content.semantic_conflict", TARGET_NEGOTIATION_PROPOSE_URI, "guaranteedThroughputTarget"),
        ("content.rule_violation", FEASIBILITY_NEGOTIATION_PROPOSE_URI, "guaranteedThroughputTarget"),
    ],
)
async def test_initial_validation_failure_emits_a_propose_without_a_task(
    code: str, expected_uri: str, missing_slot: str
) -> None:
    gate, server = _gate()
    server.task_error = _content_error(code, missing_slot)

    message = _assert_no_task(await gate.handle(_make_context(_initial_message())))

    metadata = _metadata(message)
    session = metadata[NEGOTIATION_CONTEXT_METADATA_KEY]
    assert metadata[TEMPLATE_URI_METADATA_KEY] == expected_uri
    assert session["id"] == CONTEXT_ID
    assert session["round"] == 1
    assert session["maxRounds"] == 3
    assert session["performative"] == NegotiationPerformative.PROPOSE.value
    assert len(server.propose_generations) == 1
    assert server.propose_generations[0][1].round == 1
    assert server.propose_generations[0][2] == expected_uri


async def test_entry_field_missing_names_the_business_field_in_the_proposal() -> None:
    """The proposal asks for "Rate", not the internal camelCase slot name."""
    gate, server = _gate()
    server.task_error = ContentValidationError(
        ErrorCatalog.CONTENT_ENTRY_FIELD_MISSING,
        {"section_label": "Task Context"},
        errors=[
            SlotValidationError(
                slot_name="guaranteedThroughputTarget",
                code="content.entry_field_missing",
                message="Entry 1 of 'Task Context' is missing required field 'Rate'",
                facts={"section_label": "Task Context", "index": 1, "field_label": "Rate"},
            )
        ],
    )

    message = _assert_no_task(await gate.handle(_make_context(_initial_message())))

    metadata = _metadata(message)
    assert metadata[TEMPLATE_URI_METADATA_KEY] == INFORMATION_NEGOTIATION_PROPOSE_URI
    proposal_text = server.propose_generations[0][0]
    assert proposal_text is not None
    assert "Rate" in proposal_text
    assert "guaranteedThroughputTarget" not in proposal_text


async def test_proposal_generation_failure_returns_a_structured_message() -> None:
    """A timed-out SDK proposal must reach the client as a message, not as a transport error."""
    gate, server = _gate()
    server.task_error = _content_error("content.param_missing")
    server.propose_generation_error = A2ATError("LLM call timed out after 30s")

    outcome = await gate.handle(_make_context(_initial_message()))

    message = _assert_no_task(outcome)
    assert "Negotiation proposal generation failed" in message.parts[0].text
    assert "LLM call timed out after 30s" in message.parts[0].text
    assert CONTEXT_ID == message.context_id
    metadata = _metadata(message)
    assert NEGOTIATION_CONTEXT_METADATA_KEY not in metadata
    assert len(server.propose_generations) == 1


async def test_unmapped_validation_failure_returns_a_rejected_task() -> None:
    gate, server = _gate()
    server.task_error = _content_error("content.format_error")
    context = _make_context(_initial_message())

    outcome = await gate.handle(context)

    assert outcome.short_circuit is True
    assert outcome.task_request is None
    assert outcome.message is None
    assert outcome.terminal_task is not None
    task = outcome.terminal_task
    assert task.status.state == TaskState.TASK_STATE_REJECTED
    assert task.id == context.task_id
    assert task.context_id == CONTEXT_ID
    assert task.status.message.parts[0].text
    structured = _metadata(task.status.message)[STRUCTURED_ERRORS_METADATA_KEY]
    assert structured["code"]
    assert structured["errors"][0]["slotName"] == "region"
    assert structured["errors"][0]["code"] == "content.format_error"
    assert structured["errors"][0]["message"]
    assert len(task.history) == 1
    assert server.task_calls  # validation ran, but the agent never executes


async def test_missing_task_extension_returns_an_error_without_a_task() -> None:
    gate, server = _gate()
    context = _make_context(_initial_message(), extensions=())

    message = _assert_no_task(await gate.handle(context))

    assert TASK_T_EXTENSION_URI in message.parts[0].text
    assert server.task_calls == []


async def test_accept_validates_and_hands_the_agreed_task_prompt_to_the_executor() -> None:
    gate, server = _gate()
    message = _negotiation_message(
        NegotiationPerformative.ACCEPT,
        round_number=2,
        template_uri=TARGET_NEGOTIATION_ACCEPT_REJECT_URI,
        task_prompt=TASK_PROMPT,
    )
    context = _make_context(message)

    outcome = await gate.handle(context)

    assert outcome.short_circuit is False
    assert outcome.task_request is context
    assert len(server.accept_calls) == 1
    accepted_context = server.accept_calls[0][1]
    assert accepted_context is not None
    assert accepted_context.round == 2
    assert len(server.task_calls) == 1
    assert context.message is not None
    assert context.message.parts[0].text == TASK_PROMPT


async def test_a_false_conflict_verdict_does_not_end_a_valid_accept() -> None:
    """The same false verdict must not turn an accepted negotiation into a terminal error."""
    gate, server = _gate()
    server.task_error = _conflict_error("{00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~23:59,10Mbps}")
    message = _negotiation_message(
        NegotiationPerformative.ACCEPT,
        round_number=1,
        template_uri=INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
        task_prompt=_NON_OVERLAPPING_PROMPT,
    )
    context = _make_context(message)

    outcome = await gate.handle(context)

    assert outcome.short_circuit is False
    assert outcome.task_request is context


async def test_accept_validation_failure_returns_an_error_without_a_task() -> None:
    gate, server = _gate()
    server.accept_error = ValueError("bad accept")
    message = _negotiation_message(
        NegotiationPerformative.ACCEPT,
        round_number=1,
        template_uri=INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
        task_prompt=TASK_PROMPT,
    )

    result = _assert_no_task(await gate.handle(_make_context(message)))

    assert NEGOTIATION_CONTEXT_METADATA_KEY not in result.metadata


async def test_accept_without_a_task_prompt_returns_an_error_without_a_task() -> None:
    gate, server = _gate()
    message = _negotiation_message(
        NegotiationPerformative.ACCEPT,
        round_number=1,
        template_uri=INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    )

    _assert_no_task(await gate.handle(_make_context(message)))

    assert server.task_calls == []


@pytest.mark.parametrize(
    ("performative", "template_uri"),
    [
        (NegotiationPerformative.REJECT, INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI),
        (NegotiationPerformative.ABORT, NEGOTIATION_ABORT_URI),
    ],
)
async def test_terminal_client_performative_returns_the_plain_end_message(
    performative: NegotiationPerformative, template_uri: str
) -> None:
    gate, server = _gate()
    message = _negotiation_message(performative, round_number=2, template_uri=template_uri)

    result = _assert_no_task(await gate.handle(_make_context(message)))

    assert result.parts[0].text == END_MESSAGE_TEXT
    assert NEGOTIATION_T_EXTENSION_URI not in result.metadata
    assert NEGOTIATION_CONTEXT_METADATA_KEY not in result.metadata
    assert len(server.reject_calls) + len(server.abort_calls) == 1


async def test_counter_propose_round_one_emits_a_round_two_propose_without_a_task() -> None:
    gate, server = _gate()
    message = _negotiation_message(
        NegotiationPerformative.PROPOSE,
        round_number=1,
        template_uri=TARGET_NEGOTIATION_PROPOSE_URI,
        sample={"cellId": "cell-001", "window": "17:30~23:00", "targetRate": 20},
    )

    result = _assert_no_task(await gate.handle(_make_context(message)))

    session = _metadata(result)[NEGOTIATION_CONTEXT_METADATA_KEY]
    assert session["id"] == CONTEXT_ID
    assert session["round"] == 2
    assert session["maxRounds"] == 3
    assert session["performative"] == NegotiationPerformative.PROPOSE.value
    assert len(server.propose_calls) == 1
    assert len(server.propose_generations) == 1
    assert server.propose_generations[0][1].round == 2


async def test_counter_propose_at_max_rounds_emits_an_abort_without_a_task() -> None:
    gate, server = _gate()
    message = _negotiation_message(
        NegotiationPerformative.PROPOSE,
        round_number=3,
        template_uri=TARGET_NEGOTIATION_PROPOSE_URI,
        sample={"cellId": "cell-001", "window": "17:30~23:00", "targetRate": 20},
    )

    result = _assert_no_task(await gate.handle(_make_context(message)))

    session = _metadata(result)[NEGOTIATION_CONTEXT_METADATA_KEY]
    assert session["round"] == 3
    assert session["performative"] == NegotiationPerformative.ABORT.value
    assert _metadata(result)[TEMPLATE_URI_METADATA_KEY] == NEGOTIATION_ABORT_URI
    assert len(server.abort_generations) == 1
    assert server.abort_generations[0][1].round == 3


async def test_counter_propose_without_a_sample_advances_the_preset_sequence() -> None:
    gate, server = _gate()
    message = _negotiation_message(
        NegotiationPerformative.PROPOSE,
        round_number=2,
        template_uri=TARGET_NEGOTIATION_PROPOSE_URI,
    )

    result = _assert_no_task(await gate.handle(_make_context(message)))

    assert _metadata(result)[NEGOTIATION_CONTEXT_METADATA_KEY]["round"] == 3
    assert server.propose_generations[0][1].round == 3


async def test_counter_propose_validation_failure_returns_an_error_without_a_task() -> None:
    gate, server = _gate()
    server.propose_error = ValueError("bad counter proposal")
    message = _negotiation_message(
        NegotiationPerformative.PROPOSE,
        round_number=1,
        template_uri=TARGET_NEGOTIATION_PROPOSE_URI,
    )

    result = _assert_no_task(await gate.handle(_make_context(message)))

    assert NEGOTIATION_CONTEXT_METADATA_KEY not in result.metadata
    assert server.propose_generations == []


async def test_pre_task_detection_uses_current_task_not_the_generated_task_id() -> None:
    gate, server = _gate()
    context = _make_context(_initial_message())

    assert context.task_id  # RequestContext generates one, but no Task exists yet
    outcome = await gate.handle(context)

    assert outcome.short_circuit is False
    assert len(server.task_calls) == 1


async def test_modify_follow_up_is_validated_and_returns_a_modify_outcome() -> None:
    gate, server = _gate()
    server.task_params = {
        "operationType": "Modify",
        "region": "Region A",
        "guaranteedThroughputTarget": ("{00:00~03:00,10Mbps}, {03:00~05:00,2Mbps}, {05:00~24:00,10Mbps}"),
        "siteList": "Site 1,Site 2",
        "backgroundEvent": "planned power outage tomorrow 03:00~05:00",
    }
    message = _initial_message("## Operation Type\n\nModify\n\n## Task Type\n\nRAN energy saving")
    task = Task(
        id="task-1",
        context_id=CONTEXT_ID,
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )

    outcome = await gate.handle(_make_context(message, current_task=task))

    assert outcome.short_circuit is False
    assert outcome.task_request is not None
    assert outcome.modify_prompt is not None
    assert outcome.modify_params is not None
    assert outcome.modify_params["operationType"] == "Modify"
    assert len(server.task_calls) == 1


async def test_modify_follow_up_rejects_a_completed_task() -> None:
    gate, server = _gate()
    server.task_params = {
        "operationType": "Modify",
        "region": "Region A",
        "guaranteedThroughputTarget": ("{00:00~03:00,10Mbps}, {03:00~05:00,2Mbps}, {05:00~24:00,10Mbps}"),
        "siteList": "Site 1,Site 2",
        "backgroundEvent": "planned power outage tomorrow 03:00~05:00",
    }
    message = _initial_message("## Operation Type\n\nModify")
    task = Task(
        id="task-1",
        context_id=CONTEXT_ID,
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
    )

    result = _assert_no_task(await gate.handle(_make_context(message, current_task=task)))

    assert "not in a modifiable state" in result.parts[0].text


async def test_initial_modify_task_t_is_rejected_without_an_existing_task() -> None:
    gate, server = _gate()
    server.task_params = {
        "operationType": "Modify",
        "region": "Region A",
        "guaranteedThroughputTarget": "{00:00~03:00,10Mbps}",
    }

    result = _assert_no_task(await gate.handle(_make_context(_initial_message("## Operation Type\n\nModify"))))

    assert "Modify Task-T must continue an existing WORKING Task" in result.parts[0].text


async def test_existing_task_bypasses_the_pre_task_handler() -> None:
    gate, server = _gate()
    task = Task(id="task-1", context_id=CONTEXT_ID)
    context = _make_context(_initial_message(), current_task=task)

    outcome = await gate.handle(context)

    assert outcome.short_circuit is False
    assert outcome.task_request is context
    assert server.task_calls == []


@pytest.fixture
def real_server(offline_config: SampleConfig) -> A2ATServer:
    """Build a real A2ATServer on the scripted SDK provider (no network)."""
    install_offline_provider()
    return A2ATServer(env_path=prepare_sdk_env_path(offline_config))


@pytest.mark.parametrize(
    ("prompt", "expected_uri"),
    [
        (EXAMPLE_REQUESTS[SCENARIO_INFORMATION]["en-US"], INFORMATION_NEGOTIATION_PROPOSE_URI),
        (EXAMPLE_REQUESTS[SCENARIO_TARGET]["en-US"], TARGET_NEGOTIATION_PROPOSE_URI),
        (EXAMPLE_REQUESTS[SCENARIO_FEASIBILITY]["en-US"], FEASIBILITY_NEGOTIATION_PROPOSE_URI),
    ],
)
async def test_real_server_opens_the_matching_negotiation(
    real_server: A2ATServer, prompt: str, expected_uri: str
) -> None:
    gate = PreTaskNegotiationHandler(real_server, precheck=RanDataTaskTPrecheck())
    context_id = str(uuid.uuid4())

    result = _assert_no_task(await gate.handle(_make_context(_initial_message(prompt, context_id=context_id))))

    assert result.context_id == context_id
    result_metadata = _metadata(result)
    assert result_metadata[TEMPLATE_URI_METADATA_KEY] == expected_uri
    assert result_metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["id"] == context_id
    assert result_metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["round"] == 1
    assert result_metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["maxRounds"] == 3
