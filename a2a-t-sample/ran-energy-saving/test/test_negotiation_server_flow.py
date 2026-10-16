"""Offline tests of the server-side negotiation flow of the ran-energy-saving demo.

Drives ``execute_server_flow`` with the real A2A-T facades, the scripted mock LLM and a real
a2a-sdk ``EventQueue``: round 1 (missing region) answers SUBMITTED + INPUT_REQUIRED carrying the
Negotiation-T information propose metadata, round 2 (continuation) streams the UC1 result and
closes with COMPLETED, a failure without slot-named errors keeps the plain REJECTED branch, and
the target/feasibility content-validation failures propose their per-type Negotiation-T content
over both generation channels (from-data without and from-text with one extraction LLM call).
The task-execution seam is covered too: the demo service satisfies the ``TaskExecutionService``
Protocol, an injected fake replaces the demo behavior end-to-end, and an execution failure emits
FAILED with the generic text.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import EventQueueLegacy, QueueShutDown
from a2a.types import Role, SendMessageRequest, Task, TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent
from a2a_t.client.a2at_client import A2ATClient
from a2a_t.core.errors.exceptions import ContentValidationError, SlotValidationError
from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    TEMPLATE_URI_METADATA_KEY,
    MetadataContent,
    NegotiationContext,
    NegotiationPerformative,
)
from a2a_t.core.standard_templates import (
    FEASIBILITY_NEGOTIATION_PROPOSE_URI,
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    TARGET_NEGOTIATION_PROPOSE_URI,
)
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.negotiation.content.enums import NegotiationConclusion
from a2a_t.negotiation.content.models import (
    InformationEndingContent,
    NegotiationEndingData,
    NegotiationItem,
)
from a2a_t.server.a2at_server import A2ATServer
from google.protobuf.json_format import MessageToDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from client import scenario_data
from client.scenario_data import (
    ENERGY_SAVING_TEMPLATE_URI,
    build_prompt_input,
    negotiation_answers,
    negotiation_feasibility_contents,
    negotiation_feasibility_description,
    negotiation_filled_params,
    negotiation_target_confirm_request,
    negotiation_target_description,
)
from common import mock_llm
from common.llm_logger import install_llm_logger
from server.constants_data import (
    CHANNEL_DATA,
    CHANNEL_ENV_KEY,
    CHANNEL_TEXT,
    COMPLETED_MESSAGE,
    ENERGY_SAVING_REGION,
    INPUT_REQUIRED_MESSAGE,
    NEGOTIATION_RELATIONSHIP,
    NEGOTIATION_T_EXTENSION_URI,
    TASK_T_EXTENSION_URI,
    get_energy_saving_intent_report,
    get_energy_saving_progress_steps,
    resolve_negotiation_channel,
)
from server.server_flow import (
    _NEGOTIATION_TYPE_BY_ERROR_CODE,
    NEGOTIATION_TYPE_FEASIBILITY,
    NEGOTIATION_TYPE_INFORMATION,
    NEGOTIATION_TYPE_TARGET,
    _emit_negotiation_propose,
    _missing_item,
    _missing_items_from_errors,
    _negotiation_propose_text,
    _numbered_items,
    execute_server_flow,
)
from server.task_execution import TaskExecutionResult, TaskExecutionService, Uc1DemoExecutionService
from server.task_schema import TASK_PARAM_SCHEMA

#: An offline ``.env`` (no API key) written per test; the mock force-installs against it.
_OFFLINE_ENV_TEXT = "A2AT_LANGUAGE={language}\nA2AT_LLM_PROVIDER=openai\nA2AT_LLM_MODEL=mock-llm\nA2AT_LLM_API_KEY=\n"


def _write_offline_env(directory: Path, language: str) -> Path:
    """Write the offline ``.env`` of one language and return its path."""
    env_path = directory / ".env"
    env_path.write_text(_OFFLINE_ENV_TEXT.format(language=language), encoding="utf-8")
    return env_path


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Force-install the scripted mock LLM (en-US) for every test, regardless of any real ``.env``."""
    monkeypatch.chdir(PROJECT_ROOT)
    monkeypatch.delenv(scenario_data.SCENARIO_ENV_KEY, raising=False)
    monkeypatch.delenv(scenario_data.CHANNEL_ENV_KEY, raising=False)
    env_path = _write_offline_env(tmp_path, "en-US")
    mock_llm.install_mock_llm(env_path=env_path)
    install_llm_logger(role="test", verbose=False)
    return env_path


async def _no_sleep(_seconds: float) -> None:
    """Stand-in for ``asyncio.sleep`` so the streamed progress steps run without pacing."""
    return None


def _build_request_context(
    *,
    metadata: dict[str, object],
    requested_extensions: set[str],
) -> RequestContext:
    """Build one RequestContext the way the a2a-sdk request handler does for a send-message call."""
    return RequestContext(
        call_context=ServerCallContext(requested_extensions=requested_extensions),
        request=_build_send_message_request(metadata=metadata),
    )


def _build_send_message_request(metadata: dict[str, object]) -> SendMessageRequest:
    """Build one SendMessageRequest whose message metadata carries the given map."""
    request = SendMessageRequest()
    request.message.message_id = str(uuid.uuid4())
    request.message.role = Role.ROLE_USER
    request.message.parts.add(text="create the RAN energy saving task")
    for key, value in metadata.items():
        request.message.metadata[key] = value
    return request


async def _run_flow(
    *,
    metadata: dict[str, object],
    prompt_server: object,
    requested_extensions: set[str] | None = None,
    channel: str = CHANNEL_DATA,
    env_path: Path | None = None,
    execution_service: TaskExecutionService | None = None,
) -> list[object]:
    """Run one ``execute_server_flow`` round on a real EventQueue and return the drained events."""
    queue = EventQueueLegacy()
    request_context = _build_request_context(
        metadata=metadata,
        requested_extensions=requested_extensions if requested_extensions is not None else {TASK_T_EXTENSION_URI},
    )
    await execute_server_flow(
        request_context=request_context,
        event_queue=queue,
        prompt_server=prompt_server,
        sleep_fn=_no_sleep,
        channel=channel,
        env_path=env_path,
        execution_service=execution_service,
    )
    return await _drain_events(queue)


async def _drain_events(queue: EventQueueLegacy) -> list[object]:
    """Close the queue and collect every enqueued event, in order, until the queue shuts down.

    ``close()`` waits for the queue to drain (one ``task_done`` per dequeued event), so it runs
    as a background task while the events are collected.
    """
    closer = asyncio.create_task(queue.close())
    events: list[object] = []
    while True:
        try:
            events.append(await queue.dequeue_event())
            queue.task_done()
        except QueueShutDown:
            break
    await closer
    return events


def _event_state(event: object) -> TaskState | None:
    """Return the task state carried by a Task / TaskStatusUpdateEvent, ``None`` for other events."""
    status = getattr(event, "status", None)
    if status is None:
        return None
    return getattr(status, "state", None)


def _event_states(events: list[object]) -> list[TaskState]:
    """Return the ordered task states of the status-bearing events of one drained stream."""
    return [state for state in (_event_state(event) for event in events) if state is not None]


def _message_metadata(event: object) -> dict[str, object]:
    """Return the metadata map of one event's status message as a plain dict."""
    status = getattr(event, "status", None)
    assert status is not None and status.message is not None
    return MessageToDict(status.message.metadata)


def _missing_region_prompt(client: A2ATClient, env_path: Path) -> MetadataContent:
    """Generate the round-1 Task-T prompt (missing region) through the real client facade."""
    return client.generate_task_prompt_from_text(
        build_prompt_input(env_path=env_path, scenario=scenario_data.SCENARIO_NEGOTIATION),
        ENERGY_SAVING_TEMPLATE_URI,
    )


def _run_two_round_flow(env_path: Path) -> tuple[list[object], list[object], dict[str, object]]:
    """Drive the two negotiation rounds (missing region, then the filled continuation).

    Returns the round-1 events, the round-2 events and the round-1 propose context as a dict.
    """
    client = A2ATClient(env_path=env_path)
    server = A2ATServer(env_path=env_path)

    # Round 1: the client submits the missing-region prompt, the server proposes.
    round1_prompt = _missing_region_prompt(client, env_path)
    round1_events = asyncio.run(
        _run_flow(
            metadata={
                round1_prompt.extension_uri: round1_prompt.prompt_text,
                TEMPLATE_URI_METADATA_KEY: ENERGY_SAVING_TEMPLATE_URI,
            },
            prompt_server=server,
        )
    )
    propose_context = dict(_message_metadata(round1_events[-1])[NEGOTIATION_CONTEXT_METADATA_KEY])

    # Round 2: the client answers with the accept prompt and the filled Task-T prompt.
    accept_context = NegotiationContext(
        id=str(propose_context["id"]),
        round=int(propose_context["round"]) + 1,
        max_rounds=int(propose_context["maxRounds"]),
        performative=NegotiationPerformative.ACCEPT,
    )
    accept = client.generate_negotiation_accept_prompt_from_data(
        NegotiationEndingData(
            context=accept_context,
            content=InformationEndingContent(
                conclusion=NegotiationConclusion.ACCEPT,
                items=[
                    NegotiationItem(name=name, value=value)
                    for name, value in negotiation_answers(env_path=env_path).items()
                ],
            ),
        ),
        INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    )
    filled_prompt = client.generate_task_prompt_from_data_with_schema(
        negotiation_filled_params(env_path=env_path), TASK_PARAM_SCHEMA, ENERGY_SAVING_TEMPLATE_URI
    )
    round2_events = asyncio.run(
        _run_flow(
            metadata={
                TASK_T_EXTENSION_URI: filled_prompt.prompt_text,
                NEGOTIATION_T_EXTENSION_URI: accept.prompt_text,
                TEMPLATE_URI_METADATA_KEY: ENERGY_SAVING_TEMPLATE_URI,
                NEGOTIATION_CONTEXT_METADATA_KEY: {
                    "id": accept_context.id,
                    "round": accept_context.round,
                    "maxRounds": accept_context.max_rounds,
                    "performative": accept_context.performative.value,
                },
            },
            prompt_server=server,
            requested_extensions={TASK_T_EXTENSION_URI, NEGOTIATION_T_EXTENSION_URI},
        )
    )
    return round1_events, round2_events, propose_context


class _SlotlessRejectingPromptServer:
    """Prompt-server stub whose content validation fails without slot-named errors."""

    def validate_task_prompt_and_data_filling(self, *, prompt: str, schema: object, template_uri: object) -> object:
        raise ContentValidationError(
            "llm.invocation_failed",
            errors=[SlotValidationError(slot_name=None, code="llm.invocation_failed", message="mock llm failure")],
        )


# --- round 1: missing params -> Negotiation-T propose on INPUT_REQUIRED ---------------------


def test_round_1_missing_region_answers_input_required_carrying_the_propose(_offline_env: Path) -> None:
    mock_llm.reset_call_log()
    client = A2ATClient(env_path=_offline_env)
    prompt = _missing_region_prompt(client, _offline_env)

    events = asyncio.run(
        _run_flow(
            metadata={
                prompt.extension_uri: prompt.prompt_text,
                TEMPLATE_URI_METADATA_KEY: ENERGY_SAVING_TEMPLATE_URI,
            },
            prompt_server=A2ATServer(env_path=_offline_env),
        )
    )

    assert _event_states(events) == [TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_INPUT_REQUIRED]
    input_required = events[-1]
    assert isinstance(input_required, TaskStatusUpdateEvent)
    # The status text part stays a short human line; the propose travels in the metadata.
    assert input_required.status.message.parts[0].text == INPUT_REQUIRED_MESSAGE
    metadata = _message_metadata(input_required)
    assert NEGOTIATION_T_EXTENSION_URI in metadata
    assert metadata[TEMPLATE_URI_METADATA_KEY] == "Negotiation-T/information-negotiation/propose/v1"
    context = metadata[NEGOTIATION_CONTEXT_METADATA_KEY]
    assert uuid.UUID(str(context["id"]))  # a fresh negotiation session id
    assert context["round"] == 1
    assert context["maxRounds"] == NegotiationContext.DEFAULT_MAX_ROUNDS
    assert context["performative"] == "PROPOSE"
    # The propose text asks for the missing region slot with the schema description fragment.
    propose_text = str(metadata[NEGOTIATION_T_EXTENSION_URI])
    assert "region" in propose_text
    assert "Coverage region" in propose_text


def _kind_round1_metadata(client: A2ATClient, env_path: Path, kind: str) -> dict[str, object]:
    """Generate the round-1 Task-T prompt of one negotiation kind and build the flow metadata."""
    prompt = client.generate_task_prompt_from_text(
        build_prompt_input(env_path=env_path, negotiation_kind=kind), ENERGY_SAVING_TEMPLATE_URI
    )
    return {
        prompt.extension_uri: prompt.prompt_text,
        TEMPLATE_URI_METADATA_KEY: ENERGY_SAVING_TEMPLATE_URI,
    }


# --- round 1: per-type proposes (target / feasibility) over the from-data channel ------------


def test_round_1_target_failure_answers_input_required_carrying_the_target_propose(_offline_env: Path) -> None:
    mock_llm.reset_call_log()
    metadata = _kind_round1_metadata(
        A2ATClient(env_path=_offline_env), _offline_env, scenario_data.NEGOTIATION_KIND_TARGET
    )

    events = asyncio.run(
        _run_flow(metadata=metadata, prompt_server=A2ATServer(env_path=_offline_env), env_path=_offline_env)
    )

    assert _event_states(events) == [TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_INPUT_REQUIRED]
    input_required = events[-1]
    assert isinstance(input_required, TaskStatusUpdateEvent)
    assert input_required.status.message.parts[0].text == INPUT_REQUIRED_MESSAGE
    metadata = _message_metadata(input_required)
    assert NEGOTIATION_T_EXTENSION_URI in metadata
    assert metadata[TEMPLATE_URI_METADATA_KEY] == TARGET_NEGOTIATION_PROPOSE_URI
    context = metadata[NEGOTIATION_CONTEXT_METADATA_KEY]
    assert uuid.UUID(str(context["id"]))  # a fresh negotiation session id
    assert context["round"] == 1
    assert context["maxRounds"] == NegotiationContext.DEFAULT_MAX_ROUNDS
    assert context["performative"] == "PROPOSE"
    # The propose text carries the clarified-target confirm request of the scenario data.
    propose_text = str(metadata[NEGOTIATION_T_EXTENSION_URI])
    assert negotiation_target_confirm_request(env_path=_offline_env) in propose_text
    # from-data propose generation is deterministic: no negotiation extraction call is logged.
    assert mock_llm.llm_calls == [
        mock_llm.STAGE_SLOT_EXTRACTION_TARGET,
        mock_llm.STAGE_CONTENT_VALIDATION_TARGET,
    ]


def test_round_1_feasibility_failure_answers_input_required_carrying_the_feasibility_propose(
    _offline_env: Path,
) -> None:
    mock_llm.reset_call_log()
    metadata = _kind_round1_metadata(
        A2ATClient(env_path=_offline_env), _offline_env, scenario_data.NEGOTIATION_KIND_FEASIBILITY
    )

    events = asyncio.run(
        _run_flow(metadata=metadata, prompt_server=A2ATServer(env_path=_offline_env), env_path=_offline_env)
    )

    assert _event_states(events) == [TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_INPUT_REQUIRED]
    metadata = _message_metadata(events[-1])
    assert NEGOTIATION_T_EXTENSION_URI in metadata
    assert metadata[TEMPLATE_URI_METADATA_KEY] == FEASIBILITY_NEGOTIATION_PROPOSE_URI
    context = metadata[NEGOTIATION_CONTEXT_METADATA_KEY]
    assert context["round"] == 1
    assert context["performative"] == "PROPOSE"
    # The propose text renders the contents to evaluate of the scenario data.
    propose_text = str(metadata[NEGOTIATION_T_EXTENSION_URI])
    for name, _value in negotiation_feasibility_contents(env_path=_offline_env):
        assert name in propose_text
    assert mock_llm.llm_calls == [
        mock_llm.STAGE_SLOT_EXTRACTION_FEASIBILITY,
        mock_llm.STAGE_CONTENT_VALIDATION_FEASIBILITY,
    ]


# --- round 1: per-type proposes over the from-text channel -----------------------------------


def test_round_1_target_failure_from_text_channel_generates_via_extraction(_offline_env: Path) -> None:
    mock_llm.reset_call_log()
    metadata = _kind_round1_metadata(
        A2ATClient(env_path=_offline_env), _offline_env, scenario_data.NEGOTIATION_KIND_TARGET
    )

    events = asyncio.run(
        _run_flow(
            metadata=metadata,
            prompt_server=A2ATServer(env_path=_offline_env),
            channel=CHANNEL_TEXT,
            env_path=_offline_env,
        )
    )

    assert _event_states(events) == [TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_INPUT_REQUIRED]
    propose_metadata = _message_metadata(events[-1])
    # The templateUri still carries the target propose URI — the client's dispatch key is unchanged.
    assert propose_metadata[TEMPLATE_URI_METADATA_KEY] == TARGET_NEGOTIATION_PROPOSE_URI
    assert negotiation_target_confirm_request(env_path=_offline_env) in str(
        propose_metadata[NEGOTIATION_T_EXTENSION_URI]
    )
    # from-text propose generation runs exactly one extraction call after the round-1 validation.
    assert mock_llm.llm_calls == [
        mock_llm.STAGE_SLOT_EXTRACTION_TARGET,
        mock_llm.STAGE_CONTENT_VALIDATION_TARGET,
        mock_llm.STAGE_NEGOTIATION_TARGET_PROPOSE_EXTRACTION,
    ]


def test_round_1_feasibility_failure_from_text_channel_generates_via_extraction(_offline_env: Path) -> None:
    mock_llm.reset_call_log()
    metadata = _kind_round1_metadata(
        A2ATClient(env_path=_offline_env), _offline_env, scenario_data.NEGOTIATION_KIND_FEASIBILITY
    )

    events = asyncio.run(
        _run_flow(
            metadata=metadata,
            prompt_server=A2ATServer(env_path=_offline_env),
            channel=CHANNEL_TEXT,
            env_path=_offline_env,
        )
    )

    assert _event_states(events) == [TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_INPUT_REQUIRED]
    propose_metadata = _message_metadata(events[-1])
    assert propose_metadata[TEMPLATE_URI_METADATA_KEY] == FEASIBILITY_NEGOTIATION_PROPOSE_URI
    # The rendered text carries the description and the contents to evaluate of the scenario data.
    propose_text = str(propose_metadata[NEGOTIATION_T_EXTENSION_URI])
    assert negotiation_feasibility_description(env_path=_offline_env) in propose_text
    for name, value in negotiation_feasibility_contents(env_path=_offline_env):
        assert f"{name}: {value}" in propose_text
    assert mock_llm.llm_calls == [
        mock_llm.STAGE_SLOT_EXTRACTION_FEASIBILITY,
        mock_llm.STAGE_CONTENT_VALIDATION_FEASIBILITY,
        mock_llm.STAGE_NEGOTIATION_FEASIBILITY_PROPOSE_EXTRACTION,
    ]


def test_target_propose_messages_are_equivalent_across_channels(_offline_env: Path) -> None:
    """The from-data and from-text channels produce equivalent messages (the mock extraction
    returns the canonical content the scenario accessors mirror); only the fresh session id
    differs."""
    metadata = _kind_round1_metadata(
        A2ATClient(env_path=_offline_env), _offline_env, scenario_data.NEGOTIATION_KIND_TARGET
    )
    server = A2ATServer(env_path=_offline_env)

    mock_llm.reset_call_log()
    data_events = asyncio.run(_run_flow(metadata=metadata, prompt_server=server, env_path=_offline_env))
    mock_llm.reset_call_log()
    text_events = asyncio.run(
        _run_flow(metadata=metadata, prompt_server=server, channel=CHANNEL_TEXT, env_path=_offline_env)
    )

    data_metadata = _message_metadata(data_events[-1])
    text_metadata = _message_metadata(text_events[-1])
    assert data_metadata[NEGOTIATION_T_EXTENSION_URI] == text_metadata[NEGOTIATION_T_EXTENSION_URI]
    assert data_metadata[TEMPLATE_URI_METADATA_KEY] == text_metadata[TEMPLATE_URI_METADATA_KEY]
    data_context = dict(data_metadata[NEGOTIATION_CONTEXT_METADATA_KEY])
    text_context = dict(text_metadata[NEGOTIATION_CONTEXT_METADATA_KEY])
    assert data_context.pop("id") != text_context.pop("id")  # each channel opens its own session
    assert data_context == text_context


def test_feasibility_propose_messages_are_equivalent_across_channels(_offline_env: Path) -> None:
    """The from-data and from-text feasibility proposes produce equivalent messages (the mock
    extraction returns the canonical content the scenario accessors mirror); only the fresh
    session id differs."""
    metadata = _kind_round1_metadata(
        A2ATClient(env_path=_offline_env), _offline_env, scenario_data.NEGOTIATION_KIND_FEASIBILITY
    )
    server = A2ATServer(env_path=_offline_env)

    mock_llm.reset_call_log()
    data_events = asyncio.run(_run_flow(metadata=metadata, prompt_server=server, env_path=_offline_env))
    mock_llm.reset_call_log()
    text_events = asyncio.run(
        _run_flow(metadata=metadata, prompt_server=server, channel=CHANNEL_TEXT, env_path=_offline_env)
    )

    data_metadata = _message_metadata(data_events[-1])
    text_metadata = _message_metadata(text_events[-1])
    assert data_metadata[NEGOTIATION_T_EXTENSION_URI] == text_metadata[NEGOTIATION_T_EXTENSION_URI]
    assert data_metadata[TEMPLATE_URI_METADATA_KEY] == text_metadata[TEMPLATE_URI_METADATA_KEY]
    data_context = dict(data_metadata[NEGOTIATION_CONTEXT_METADATA_KEY])
    text_context = dict(text_metadata[NEGOTIATION_CONTEXT_METADATA_KEY])
    assert data_context.pop("id") != text_context.pop("id")  # each channel opens its own session
    assert data_context == text_context


# --- per-type propose text renderings of the from-text channel -------------------------------


def test_information_propose_text_renders_numbered_items_and_the_relationship() -> None:
    items = [_missing_item("region"), _missing_item("energySavingGoal")]

    text = _negotiation_propose_text(NEGOTIATION_TYPE_INFORMATION, items, env_path=None)

    assert text.startswith("1. region：Please provide region:")
    assert "；2. energySavingGoal：" in text
    assert text.endswith(NEGOTIATION_RELATIONSHIP)
    single = _negotiation_propose_text(NEGOTIATION_TYPE_INFORMATION, [_missing_item("region")], env_path=None)
    assert NEGOTIATION_RELATIONSHIP not in single
    # The value is omitted when blank; the full-width separators are kept.
    assert _numbered_items([("a", ""), ("b", None)]) == "1. a；2. b；"


def test_target_propose_text_joins_the_description_and_the_confirm_request(_offline_env: Path) -> None:
    text = _negotiation_propose_text(NEGOTIATION_TYPE_TARGET, [], env_path=_offline_env)

    assert text == (
        f"{negotiation_target_description(env_path=_offline_env)} "
        f"{negotiation_target_confirm_request(env_path=_offline_env)}"
    )


def test_feasibility_propose_text_renders_the_contents_to_evaluate(_offline_env: Path) -> None:
    text = _negotiation_propose_text(NEGOTIATION_TYPE_FEASIBILITY, [], env_path=_offline_env)

    assert text.startswith(
        f"{scenario_data.negotiation_feasibility_description(env_path=_offline_env)} Contents to evaluate: "
    )
    for name, value in negotiation_feasibility_contents(env_path=_offline_env):
        assert f"{name}：{value}" in text


# --- round 2: continuation -> validate -> progress -> artifact -> COMPLETED -----------------


def test_round_2_continuation_streams_the_uc1_result_and_completes(_offline_env: Path) -> None:
    mock_llm.reset_call_log()
    _, round2_events, _ = _run_two_round_flow(_offline_env)

    expected_steps = get_energy_saving_progress_steps()
    assert _event_states(round2_events) == [TaskState.TASK_STATE_WORKING] * len(expected_steps) + [
        TaskState.TASK_STATE_COMPLETED
    ]
    # No SUBMITTED event on the continuation round.
    assert not any(isinstance(event, Task) for event in round2_events)
    # One artifact event, arriving between the last WORKING step and COMPLETED.
    artifacts = [event for event in round2_events if isinstance(event, TaskArtifactUpdateEvent)]
    assert len(artifacts) == 1
    artifact_index = round2_events.index(artifacts[0])
    assert _event_state(round2_events[artifact_index - 1]) == TaskState.TASK_STATE_WORKING
    assert _event_state(round2_events[artifact_index + 1]) == TaskState.TASK_STATE_COMPLETED
    assert artifacts[0].artifact.name == "energy saving intent report"
    completed = round2_events[-1]
    assert isinstance(completed, TaskStatusUpdateEvent)
    assert completed.status.message.parts[0].text == COMPLETED_MESSAGE


def test_two_round_sequence_pins_the_mock_call_log(_offline_env: Path) -> None:
    mock_llm.reset_call_log()

    _run_two_round_flow(_offline_env)

    # Round 1: extraction of the missing input + its failing content validation; round 2: the
    # from-data filled-params extraction (no scenario input) + its passing content validation.
    assert mock_llm.llm_calls == [
        mock_llm.STAGE_SLOT_EXTRACTION_MISSING,
        mock_llm.STAGE_CONTENT_VALIDATION_MISSING,
        mock_llm.STAGE_SLOT_EXTRACTION,
        mock_llm.STAGE_CONTENT_VALIDATION,
    ]


# --- default flow: success stays long-running, slotless failure rejects ---------------------


def test_default_success_streams_the_uc1_result_and_stays_long_running(_offline_env: Path) -> None:
    """The default (non-negotiation) success path keeps the UC1 semantics: no terminal state."""
    mock_llm.reset_call_log()
    client = A2ATClient(env_path=_offline_env)
    prompt = client.generate_task_prompt_from_text(
        build_prompt_input(env_path=_offline_env, scenario=scenario_data.SCENARIO_DEFAULT),
        ENERGY_SAVING_TEMPLATE_URI,
    )

    events = asyncio.run(
        _run_flow(
            metadata={
                prompt.extension_uri: prompt.prompt_text,
                TEMPLATE_URI_METADATA_KEY: ENERGY_SAVING_TEMPLATE_URI,
            },
            prompt_server=A2ATServer(env_path=_offline_env),
        )
    )

    expected_steps = get_energy_saving_progress_steps()
    assert _event_states(events) == [TaskState.TASK_STATE_SUBMITTED] + [TaskState.TASK_STATE_WORKING] * len(
        expected_steps
    )
    # The stream ends on the artifact event; no terminal status update is emitted.
    assert isinstance(events[-1], TaskArtifactUpdateEvent)
    assert mock_llm.llm_calls == [mock_llm.STAGE_SLOT_EXTRACTION, mock_llm.STAGE_CONTENT_VALIDATION]


def test_default_failure_without_slot_errors_answers_rejected(_offline_env: Path) -> None:
    events = asyncio.run(
        _run_flow(metadata={TASK_T_EXTENSION_URI: "any prompt text"}, prompt_server=_SlotlessRejectingPromptServer())
    )

    assert _event_states(events) == [TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_REJECTED]
    rejected = events[-1]
    assert isinstance(rejected, TaskStatusUpdateEvent)
    assert "Prompt validation failed" in rejected.status.message.parts[0].text
    # No Negotiation-T propose metadata travels on the plain rejection.
    assert NEGOTIATION_T_EXTENSION_URI not in _message_metadata(rejected)


def test_default_failure_with_an_unrecognized_error_code_answers_rejected(_offline_env: Path) -> None:
    """A slot-named error whose code selects no negotiation type keeps the plain rejection (plan R1)."""

    class _UnrecognizedCodePromptServer:
        """Prompt-server stub whose content validation fails with a slot-named unmapped code."""

        def validate_task_prompt_and_data_filling(self, *, prompt: str, schema: object, template_uri: object) -> object:
            raise ContentValidationError(
                "content.format_error",
                errors=[SlotValidationError(slot_name="region", code="content.format_error", message="m")],
            )

    events = asyncio.run(
        _run_flow(metadata={TASK_T_EXTENSION_URI: "any prompt text"}, prompt_server=_UnrecognizedCodePromptServer())
    )

    assert _event_states(events) == [TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_REJECTED]
    assert NEGOTIATION_T_EXTENSION_URI not in _message_metadata(events[-1])


# --- task-execution seam: Protocol contract, demo service, injected fake, failure text --------


def test_uc1_demo_execution_service_satisfies_the_task_execution_protocol() -> None:
    """Contract test: the demo implementation structurally satisfies the server seam Protocol.

    ``TaskExecutionService`` is ``@runtime_checkable``, so the idiomatic conformance check is an
    ``isinstance`` probe (it verifies the member set at runtime; signatures stay the static
    checks' concern). The ``TaskExecutionResult`` shape the flow maps onto the report artifact is
    pinned here too.
    """
    assert isinstance(Uc1DemoExecutionService(), TaskExecutionService)
    assert [field.name for field in dataclasses.fields(TaskExecutionResult)] == [
        "report_name",
        "report_text",
        "report_metadata",
    ]
    result = TaskExecutionResult(report_name="n", report_text="t", report_metadata="m")
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.report_name = "other"


def test_uc1_demo_execution_service_delegates_to_constants_data() -> None:
    """The demo service delegates to the ``constants_data`` accessors — the data stays there (plan R2)."""
    emitted: list[str] = []

    async def _emit(text: str) -> None:
        emitted.append(text)

    service = Uc1DemoExecutionService(sleep_fn=_no_sleep)
    result = asyncio.run(service.execute({}, _emit))

    assert emitted == get_energy_saving_progress_steps()
    report = get_energy_saving_intent_report()
    assert result.report_name == report["name"]
    assert result.report_text == report["text"]
    assert result.report_metadata == report["metadata"]


def test_uc1_demo_execution_service_streams_steps_with_pacing_and_maps_the_report() -> None:
    """The scripted demo emits each step with one pacing sleep after it, then maps the report."""
    sleeps: list[float] = []
    emitted: list[str] = []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def _emit(text: str) -> None:
        emitted.append(text)

    service = Uc1DemoExecutionService(
        progress_steps=["step one", "step two"],
        intent_report={"name": "fake report", "metadata": "fake metadata"},
        sleep_fn=_sleep,
        artifact_send_interval_seconds=0.5,
    )
    result = asyncio.run(service.execute({"region": "R"}, _emit))

    assert emitted == ["step one", "step two"]
    assert sleeps == [0.5, 0.5]
    # The report dict maps onto the result fields; the text falls back to the name when absent.
    assert (result.report_name, result.report_text, result.report_metadata) == (
        "fake report",
        "fake report",
        "fake metadata",
    )


class _RecordingExecutionService:
    """Injected fake ``TaskExecutionService`` proving the seam replaces the demo behavior."""

    def __init__(self) -> None:
        self.seen_params: dict[str, object] | None = None

    async def execute(
        self, params: dict[str, object], emit_progress: Callable[[str], Awaitable[None]]
    ) -> TaskExecutionResult:
        self.seen_params = params
        for line in ("fake progress one", "fake progress two"):
            await emit_progress(line)
        return TaskExecutionResult(
            report_name="fake report",
            report_text="fake report body",
            report_metadata="fake report metadata",
        )


def test_injected_execution_service_replaces_the_demo_behavior_end_to_end(_offline_env: Path) -> None:
    """An injected execution service drives the flow: its lines become the WORKING updates and its
    result the report artifact — the demo UC1 stream is fully replaced (the seam works)."""
    mock_llm.reset_call_log()
    client = A2ATClient(env_path=_offline_env)
    prompt = client.generate_task_prompt_from_text(
        build_prompt_input(env_path=_offline_env, scenario=scenario_data.SCENARIO_DEFAULT),
        ENERGY_SAVING_TEMPLATE_URI,
    )
    fake = _RecordingExecutionService()

    events = asyncio.run(
        _run_flow(
            metadata={
                prompt.extension_uri: prompt.prompt_text,
                TEMPLATE_URI_METADATA_KEY: ENERGY_SAVING_TEMPLATE_URI,
            },
            prompt_server=A2ATServer(env_path=_offline_env),
            execution_service=fake,
        )
    )

    assert _event_states(events) == [TaskState.TASK_STATE_SUBMITTED] + [TaskState.TASK_STATE_WORKING] * 2
    working = [
        event
        for event in events
        if isinstance(event, TaskStatusUpdateEvent) and event.status.state == TaskState.TASK_STATE_WORKING
    ]
    assert [event.status.message.parts[0].text for event in working] == ["fake progress one", "fake progress two"]
    artifacts = [event for event in events if isinstance(event, TaskArtifactUpdateEvent)]
    assert len(artifacts) == 1
    assert artifacts[0].artifact.name == "fake report"
    assert artifacts[0].artifact.parts[0].text == "fake report body"
    assert artifacts[0].artifact.metadata[TASK_T_EXTENSION_URI] == "fake report metadata"
    # The seam receives the filled Task-T parameters extracted by the content validation.
    assert isinstance(fake.seen_params, dict)
    assert fake.seen_params.get("region") == ENERGY_SAVING_REGION


class _ValidatingPromptServer:
    """Prompt-server stub whose content validation succeeds with fixed filled parameters."""

    def validate_task_prompt_and_data_filling(self, *, prompt: str, schema: object, template_uri: object) -> object:
        return FilledParamData(data={"operationType": "create", "region": ENERGY_SAVING_REGION})


class _FailingExecutionService:
    """Injected execution service whose execution always fails."""

    async def execute(
        self, params: dict[str, object], emit_progress: Callable[[str], Awaitable[None]]
    ) -> TaskExecutionResult:
        raise RuntimeError("validation is fine, execution is not")


def test_execution_failure_emits_failed_with_the_generic_text() -> None:
    """The flow owns the exception -> FAILED emission; the text is generic (no 'Mock' wording, plan R2).

    The flow re-raises after the FAILED emission, so the events are drained after the raised run.
    """
    queue = EventQueueLegacy()
    request_context = _build_request_context(
        metadata={TASK_T_EXTENSION_URI: "any prompt text"},
        requested_extensions={TASK_T_EXTENSION_URI},
    )
    with pytest.raises(RuntimeError, match="validation is fine, execution is not"):
        asyncio.run(
            execute_server_flow(
                request_context=request_context,
                event_queue=queue,
                prompt_server=_ValidatingPromptServer(),
                execution_service=_FailingExecutionService(),
            )
        )

    events = asyncio.run(_drain_events(queue))
    assert _event_states(events) == [TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_FAILED]
    failed = events[-1]
    assert isinstance(failed, TaskStatusUpdateEvent)
    assert failed.status.message.parts[0].text == "Task execution failed: validation is fine, execution is not"


def test_negotiation_type_by_error_code_pins_the_sample_mapping() -> None:
    """The error-code -> negotiation-type table is the sample-level customization point (plan R6)."""
    assert _NEGOTIATION_TYPE_BY_ERROR_CODE == {
        "content.param_missing": "information",
        "content.value_not_allowed": "target",
        "content.semantic_conflict": "feasibility",
    }
    # Unrecognized codes stay out of the table (their tasks keep the plain rejection).
    assert "content.format_error" not in _NEGOTIATION_TYPE_BY_ERROR_CODE


# --- missing-item building and the relationship rule ----------------------------------------


def test_missing_items_are_built_from_slot_named_errors_only() -> None:
    errors = [
        SlotValidationError(slot_name="region", code="content.param_missing", message="m1"),
        SlotValidationError(slot_name=None, code="llm.invocation_failed", message="m2"),
    ]

    items = _missing_items_from_errors(errors)

    assert [(item.name, item.value) for item in items] == [
        (
            "region",
            "Please provide region: Coverage region (task object) of the energy-saving task; "
            "a concrete physical area name..",
        )
    ]
    assert _missing_items_from_errors(None) == []
    assert (
        _missing_items_from_errors([SlotValidationError(slot_name=None, code="llm.invocation_failed", message="m")])
        == []
    )


def test_propose_carries_the_relationship_only_for_multiple_items(_offline_env: Path) -> None:
    async def _emit(items: list[NegotiationItem]) -> list[object]:
        queue = EventQueueLegacy()
        request = _build_send_message_request(metadata={TASK_T_EXTENSION_URI: "prompt"})
        request_context = RequestContext(
            call_context=ServerCallContext(requested_extensions={TASK_T_EXTENSION_URI}),
            request=request,
        )
        await _emit_negotiation_propose(
            request_context=request_context,
            event_queue=queue,
            prompt_server=A2ATServer(env_path=_offline_env),
            missing_items=items,
            task_id=request_context.task_id or "",
            context_id=request_context.context_id or "",
            log_sink=None,
        )
        return await _drain_events(queue)

    single = asyncio.run(_emit([_missing_item("region")]))
    multiple = asyncio.run(_emit([_missing_item("region"), _missing_item("energySavingGoal")]))

    assert _event_states(single) == [TaskState.TASK_STATE_INPUT_REQUIRED]
    assert _event_states(multiple) == [TaskState.TASK_STATE_INPUT_REQUIRED]
    single_text = str(_message_metadata(single[-1])[NEGOTIATION_T_EXTENSION_URI])
    multiple_text = str(_message_metadata(multiple[-1])[NEGOTIATION_T_EXTENSION_URI])
    assert NEGOTIATION_RELATIONSHIP not in single_text
    assert NEGOTIATION_RELATIONSHIP in multiple_text


# --- channel resolution and executor plumbing ------------------------------------------------


def test_resolve_negotiation_channel_defaults_and_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # isolate the cwd; this test writes its own .env files below

    # No .env and no OS environment entry -> data.
    assert resolve_negotiation_channel() == CHANNEL_DATA

    # Unknown value -> data.
    env_path = tmp_path / ".env"
    env_path.write_text(f"{CHANNEL_ENV_KEY}=bogus\n", encoding="utf-8")
    assert resolve_negotiation_channel(env_path=env_path) == CHANNEL_DATA

    # Known value -> itself.
    env_path.write_text(f"{CHANNEL_ENV_KEY}=text\n", encoding="utf-8")
    assert resolve_negotiation_channel(env_path=env_path) == CHANNEL_TEXT

    # OS environment fallback when the .env does not carry the key.
    env_path.write_text("A2AT_LANGUAGE=en-US\n", encoding="utf-8")
    monkeypatch.setenv(CHANNEL_ENV_KEY, CHANNEL_TEXT)
    assert resolve_negotiation_channel(env_path=env_path) == CHANNEL_TEXT

    # The .env value wins over the OS environment.
    env_path.write_text(f"A2AT_LANGUAGE=en-US\n{CHANNEL_ENV_KEY}=data\n", encoding="utf-8")
    assert resolve_negotiation_channel(env_path=env_path) == CHANNEL_DATA


def test_executor_threads_the_channel_and_env_path_into_the_flow(
    _offline_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # server_main's import-time mock install reads the cwd .env
    from server.server_main import SampleAgentExecutor

    captured: dict[str, object] = {}

    async def _capture_flow(**kwargs: object) -> None:
        captured.update(kwargs)

    executor = SampleAgentExecutor(
        prompt_server=object(),
        log_sink=None,
        execute_flow=_capture_flow,
        channel=CHANNEL_TEXT,
        env_path=_offline_env,
    )
    request_context = _build_request_context(metadata={}, requested_extensions={TASK_T_EXTENSION_URI})
    asyncio.run(executor.execute(request_context, EventQueueLegacy()))

    assert captured["channel"] == CHANNEL_TEXT
    assert captured["env_path"] == _offline_env
    assert captured["prompt_server"] is getattr(executor, "_prompt_server")


def test_build_server_runtime_resolves_the_channel_from_the_env(
    _offline_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # server_main's import-time mock install reads the cwd .env
    from server.server_main import build_server_runtime

    runtime = build_server_runtime(agent_card=None, env_path=_offline_env)
    executor = runtime["executor"]

    # The offline .env carries no channel key -> the default data channel.
    assert getattr(executor, "_channel") == CHANNEL_DATA
    assert getattr(executor, "_env_path") == _offline_env

    text_env_path = tmp_path / "text-channel.env"
    text_env_path.write_text(_OFFLINE_ENV_TEXT.format(language="en-US") + f"{CHANNEL_ENV_KEY}=text\n", encoding="utf-8")
    text_runtime = build_server_runtime(agent_card=None, env_path=text_env_path)
    assert getattr(text_runtime["executor"], "_channel") == CHANNEL_TEXT
