"""Offline tests of the client-side negotiation round-trip of the ran-energy-saving demo.

Drives ``run_client_flow`` against a scripted fake a2a client (real protobuf StreamResponse events)
and a fake prompt client: round 1 sends the Task-T prompt and receives the Negotiation-T propose on
INPUT_REQUIRED, round 2 re-sends the filled Task-T prompt together with the Negotiation-T accept and
consumes the UC1 result; the default path stays single-round and its stage logs are pinned. The
accept generation covers all three negotiation types dispatched on the propose's ``templateUri``
(the explicit kind flag wins when supplied) over both channels: from-data (typed content, zero LLM
calls) and from-text (a natural-language rendering plus one extraction LLM call).
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from a2a.client.client import ClientCallContext
from a2a.types import (
    Artifact,
    Message,
    Role,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
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
    FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI,
    FEASIBILITY_NEGOTIATION_PROPOSE_URI,
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
    TARGET_NEGOTIATION_ACCEPT_REJECT_URI,
    TARGET_NEGOTIATION_PROPOSE_URI,
)
from a2a_t.negotiation.content.enums import NegotiationConclusion
from a2a_t.negotiation.content.models import (
    FeasibilityEndingContent,
    NegotiationEndingData,
    TargetEndingContent,
)
from google.protobuf.json_format import MessageToDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from client import scenario_data
from client.client_flow import run_client_flow
from client.negotiation_response import (
    NegotiationDecision,
    NegotiationProposeView,
    NegotiationResponseProvider,
    ScriptedNegotiationResponseProvider,
)
from client.scenario_data import (
    CHANNEL_DATA,
    CHANNEL_TEXT,
    ENERGY_SAVING_TEMPLATE_URI,
    negotiation_feasibility_summary,
    negotiation_target_confirmation,
)
from common.sse_event_consumer import normalize_event

#: An offline ``.env`` (language pinned) written per test; the scenario accessors read it.
_OFFLINE_ENV_TEXT = "A2AT_LANGUAGE={language}\nA2AT_LLM_PROVIDER=openai\nA2AT_LLM_MODEL=mock-llm\nA2AT_LLM_API_KEY=\n"


@pytest.fixture(autouse=True)
def _offline_env(tmp_path: Path) -> Path:
    """Write the offline ``.env`` (en-US) the accept builders' scenario accessors read."""
    env_path = tmp_path / ".env"
    env_path.write_text(_OFFLINE_ENV_TEXT.format(language="en-US"), encoding="utf-8")
    return env_path


#: The round-1 task identity the scripted server stream answers with.
_TASK_ID = "task-round-1"
_CONTEXT_ID = "context-round-1"

#: Filled Task-T parameters and accept answers handed to the flow (negotiation round 2).
_NEGOTIATION_PARAMS: dict[str, object] = {
    "operationType": "create",
    "region": "Songshanhu Administration Committee",
    "energySavingGoal": "Maximize energy saving",
    "guaranteedThroughputTarget": "{00:00~07:00,2Mbps}",
}
_NEGOTIATION_ANSWERS: dict[str, str] = {"region": "Songshanhu Administration Committee"}


class _ScriptedA2AClient:
    """Fake a2a-sdk client: records every (request, context) and replays scripted streams."""

    def __init__(self, rounds: list[list[StreamResponse]]) -> None:
        self._rounds = rounds
        self.calls: list[tuple[SendMessageRequest, ClientCallContext | None]] = []

    async def send_message(
        self, request: SendMessageRequest, *, context: ClientCallContext | None = None
    ) -> AsyncIterator[StreamResponse]:
        self.calls.append((request, context))
        for event in self._rounds[len(self.calls) - 1]:
            yield event


class _ScriptedPromptClient:
    """Fake A2ATClient: deterministic MetadataContent results, recording every generation call."""

    def __init__(self) -> None:
        self.from_text_calls: list[tuple[str, str]] = []
        self.from_data_calls: list[tuple[dict[str, object], str]] = []
        self.accept_calls: list[NegotiationEndingData] = []
        self.accept_from_text_calls: list[tuple[str, NegotiationContext, str]] = []

    def generate_task_prompt_from_text(self, text: str, template_uri: str) -> MetadataContent:
        self.from_text_calls.append((text, str(template_uri)))
        return MetadataContent(
            template_uri=str(template_uri),
            prompt_text=f"rendered prompt for: {text}",
            extension_uri=TASK_T_EXTENSION_URI,
        )

    def generate_task_prompt_from_data_with_schema(
        self, data: dict[str, object], schema: object, template_uri: str
    ) -> MetadataContent:
        self.from_data_calls.append((dict(data), str(template_uri)))
        return MetadataContent(
            template_uri=str(template_uri),
            prompt_text="filled task prompt",
            extension_uri=TASK_T_EXTENSION_URI,
        )

    def _accept_result(self, template_uri: str, context: NegotiationContext) -> MetadataContent:
        return MetadataContent(
            template_uri=str(template_uri),
            prompt_text="accept prompt",
            extension_uri=NEGOTIATION_T_EXTENSION_URI,
            negotiation_context=NegotiationContext(
                id=context.id,
                round=context.round,
                max_rounds=context.max_rounds,
                performative=NegotiationPerformative.ACCEPT,
            ),
        )

    def generate_negotiation_accept_prompt_from_data(
        self, data: NegotiationEndingData, template_uri: str
    ) -> MetadataContent:
        self.accept_calls.append(data)
        return self._accept_result(str(template_uri), data.context)

    def generate_negotiation_accept_prompt_from_text(
        self, text: str, context: NegotiationContext, template_uri: str
    ) -> MetadataContent:
        self.accept_from_text_calls.append((text, context, str(template_uri)))
        return self._accept_result(str(template_uri), context)


def _propose_metadata(*, template_uri: str = INFORMATION_NEGOTIATION_PROPOSE_URI) -> dict[str, object]:
    """Build the round-1 propose metadata the server emits (``build_metadata_content`` shape)."""
    context = NegotiationContext(
        id=str(uuid.uuid4()),
        round=1,
        max_rounds=NegotiationContext.DEFAULT_MAX_ROUNDS,
        performative=NegotiationPerformative.PROPOSE,
    )
    propose = MetadataContent(
        template_uri=template_uri,
        prompt_text="Please provide region: Coverage region of the energy-saving task.",
        extension_uri=NEGOTIATION_T_EXTENSION_URI,
        negotiation_context=context,
    )
    return propose.build_metadata_content()


def _malformed_propose_metadata() -> dict[str, object]:
    """Round-1 metadata whose ``negotiationContext`` fails the NegotiationContext validation."""
    return {
        NEGOTIATION_T_EXTENSION_URI: "Please provide region: Coverage region of the energy-saving task.",
        NEGOTIATION_CONTEXT_METADATA_KEY: {"id": "", "round": 1, "maxRounds": 5, "performative": "PROPOSE"},
    }


def _agent_message(*, text: str, metadata: dict[str, object] | None = None) -> Message:
    """Build one agent status message with a text part and an optional metadata map."""
    message = Message(context_id=_CONTEXT_ID, task_id=_TASK_ID, role=Role.ROLE_AGENT)
    message.parts.add(text=text)
    for key, value in (metadata or {}).items():
        message.metadata[key] = value
    return message


def _status_update(*, state: TaskState, text: str = "", metadata: dict[str, object] | None = None) -> StreamResponse:
    """Build one streamed status update event (the wire shape the a2a-sdk client yields)."""
    return StreamResponse(
        status_update=TaskStatusUpdateEvent(
            task_id=_TASK_ID,
            context_id=_CONTEXT_ID,
            status=TaskStatus(state=state, message=_agent_message(text=text, metadata=metadata)),
        )
    )


def _task_event(state: TaskState) -> StreamResponse:
    """Build one streamed Task event carrying the task's current status."""
    return StreamResponse(task=Task(id=_TASK_ID, context_id=_CONTEXT_ID, status=TaskStatus(state=state)))


def _artifact_event() -> StreamResponse:
    """Build one streamed intent-report artifact event."""
    return StreamResponse(
        artifact_update=TaskArtifactUpdateEvent(
            task_id=_TASK_ID,
            context_id=_CONTEXT_ID,
            artifact=Artifact(artifact_id=str(uuid.uuid4()), name="energy saving intent report"),
            last_chunk=True,
        )
    )


def _round1_events(*, metadata: dict[str, object] | None = None) -> list[StreamResponse]:
    """Round-1 script: the SUBMITTED Task event, then INPUT_REQUIRED carrying the propose."""
    return [
        _task_event(TaskState.TASK_STATE_SUBMITTED),
        _status_update(
            state=TaskState.TASK_STATE_INPUT_REQUIRED,
            text="Additional information is required to start the energy saving task",
            metadata=metadata,
        ),
    ]


def _round2_events() -> list[StreamResponse]:
    """Round-2 script: WORKING steps, the intent report artifact, then COMPLETED."""
    return [
        _status_update(state=TaskState.TASK_STATE_WORKING, text="progress step 1"),
        _status_update(state=TaskState.TASK_STATE_WORKING, text="progress step 2"),
        _artifact_event(),
        _status_update(state=TaskState.TASK_STATE_COMPLETED, text="Energy saving task completed"),
    ]


def _default_events() -> list[StreamResponse]:
    """Default-path script: SUBMITTED, one WORKING step, then the stream ends on the artifact."""
    return [
        _task_event(TaskState.TASK_STATE_SUBMITTED),
        _status_update(state=TaskState.TASK_STATE_WORKING, text="progress step 1"),
        _artifact_event(),
    ]


async def _run_flow(
    a2a_client: _ScriptedA2AClient,
    prompt_client: _ScriptedPromptClient,
    *,
    negotiation_params: dict[str, object] | None = None,
    negotiation_answers: dict[str, str] | None = None,
    negotiation_kind: str | None = None,
    negotiation_response_provider: NegotiationResponseProvider | None = None,
    channel: str = CHANNEL_DATA,
    env_path: Path | None = None,
    log_sink: object | None = None,
) -> list[dict[str, object]]:
    """Run ``run_client_flow`` once against the scripted clients."""
    return await run_client_flow(
        prompt_client=prompt_client,
        a2a_client=a2a_client,
        initial_input={"scenario": "create ran energy saving task"},
        input_text="Create a RAN energy saving task to maximize energy saving.",
        negotiation_params=negotiation_params,
        negotiation_answers=negotiation_answers,
        negotiation_kind=negotiation_kind,
        negotiation_response_provider=negotiation_response_provider,
        channel=channel,
        env_path=env_path,
        log_sink=log_sink,
    )


def _event_states(events: list[dict[str, object]]) -> list[str]:
    """Return the ordered states of the status events of one normalized stream."""
    return [str(event["state"]) for event in events if event.get("kind") == "status"]


def _request_metadata(request: SendMessageRequest) -> dict[str, object]:
    """Return one request message's metadata as a plain dict."""
    return MessageToDict(request.message.metadata)


def _stage_names(logs: list[str]) -> list[str]:
    """Extract the ordered stage names from the ``[client] <stage>: <detail>`` log lines."""
    prefix = "[client] "
    return [line[len(prefix) :].split(":", 1)[0] for line in logs if line.startswith(prefix)]


# --- negotiation round trip: propose -> filled prompt + accept -> COMPLETED ------------------


def test_negotiation_round_trip_answers_the_propose_and_completes() -> None:
    propose_metadata = _propose_metadata()
    session_id = propose_metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["id"]
    prompt_client = _ScriptedPromptClient()
    a2a_client = _ScriptedA2AClient([_round1_events(metadata=propose_metadata), _round2_events()])

    events = asyncio.run(
        _run_flow(
            a2a_client,
            prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
        )
    )

    # Exactly two streaming sends: the round-1 task prompt, then the negotiation continuation.
    assert len(a2a_client.calls) == 2
    round1_request, round1_context = a2a_client.calls[0]
    assert round1_context is not None
    assert round1_context.service_parameters == {"A2A-Extensions": TASK_T_EXTENSION_URI}
    assert set(_request_metadata(round1_request)) == {TASK_T_EXTENSION_URI, TEMPLATE_URI_METADATA_KEY}

    round2_request, round2_context = a2a_client.calls[1]
    assert round2_request.message.task_id == _TASK_ID
    assert round2_request.message.context_id == _CONTEXT_ID
    assert round2_request.message.role == Role.ROLE_USER
    assert round2_request.message.parts[0].text == "create ran energy saving task"
    metadata = _request_metadata(round2_request)
    assert set(metadata) == {
        TASK_T_EXTENSION_URI,
        NEGOTIATION_T_EXTENSION_URI,
        TEMPLATE_URI_METADATA_KEY,
        NEGOTIATION_CONTEXT_METADATA_KEY,
    }
    assert metadata[TEMPLATE_URI_METADATA_KEY] == INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI
    context = metadata[NEGOTIATION_CONTEXT_METADATA_KEY]
    assert context["id"] == session_id
    assert context["round"] == 2
    assert context["maxRounds"] == NegotiationContext.DEFAULT_MAX_ROUNDS
    assert context["performative"] == "ACCEPT"

    # The A2A-Extensions header of round 2 carries both extension URIs.
    assert round2_context is not None
    header = str((round2_context.service_parameters or {}).get("A2A-Extensions", ""))
    assert {part.strip() for part in header.split(",") if part.strip()} == {
        TASK_T_EXTENSION_URI,
        NEGOTIATION_T_EXTENSION_URI,
    }

    # The filled Task-T prompt was regenerated from the supplied parameters and schema.
    assert prompt_client.from_data_calls == [(_NEGOTIATION_PARAMS, ENERGY_SAVING_TEMPLATE_URI)]
    # The accept carries conclusion ACCEPT, the advanced session context and the region answer.
    assert len(prompt_client.accept_calls) == 1
    accept_data = prompt_client.accept_calls[0]
    assert accept_data.content.conclusion == NegotiationConclusion.ACCEPT
    assert accept_data.context.id == session_id
    assert accept_data.context.round == 2
    assert accept_data.context.performative == NegotiationPerformative.ACCEPT
    assert [(item.name, item.value) for item in accept_data.content.items] == list(_NEGOTIATION_ANSWERS.items())

    # The consumed stream spans both rounds and closes with the COMPLETED state.
    assert _event_states(events) == [
        "TASK_STATE_SUBMITTED",
        "TASK_STATE_INPUT_REQUIRED",
        "TASK_STATE_WORKING",
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
    ]
    assert any(event["kind"] == "artifact" for event in events)


def test_round2_reuses_the_task_and_context_ids_of_round_1() -> None:
    prompt_client = _ScriptedPromptClient()
    a2a_client = _ScriptedA2AClient([_round1_events(metadata=_propose_metadata()), _round2_events()])

    events = asyncio.run(
        _run_flow(
            a2a_client,
            prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
        )
    )

    input_required = [event for event in events if event.get("state") == "TASK_STATE_INPUT_REQUIRED"][0]
    assert input_required["context_id"] == _CONTEXT_ID
    assert input_required["metadata"][NEGOTIATION_T_EXTENSION_URI]
    # The round-2 request carries exactly the round-1 identity (task id from any round-1 event,
    # context id from the INPUT_REQUIRED status event).
    round2_request = a2a_client.calls[1][0]
    assert round2_request.message.task_id == input_required["task_id"]
    assert round2_request.message.context_id == input_required["context_id"]


# --- INPUT_REQUIRED without negotiation answers / default path -------------------------------


def test_input_required_without_answers_is_logged_unhandled_and_stays_on_round_1() -> None:
    prompt_client = _ScriptedPromptClient()
    a2a_client = _ScriptedA2AClient([_round1_events(metadata=_propose_metadata())])

    logs: list[str] = []
    events = asyncio.run(_run_flow(a2a_client, prompt_client, log_sink=logs.append))

    assert len(a2a_client.calls) == 1
    assert prompt_client.accept_calls == []
    assert prompt_client.from_data_calls == []
    assert _event_states(events) == ["TASK_STATE_SUBMITTED", "TASK_STATE_INPUT_REQUIRED"]
    assert any("negotiation-unhandled" in line for line in logs)


def test_malformed_negotiation_context_is_logged_unhandled_and_stays_on_round_1() -> None:
    """A propose whose context fails validation degrades to the unhandled log, never a crash."""
    prompt_client = _ScriptedPromptClient()
    a2a_client = _ScriptedA2AClient([_round1_events(metadata=_malformed_propose_metadata())])

    logs: list[str] = []
    events = asyncio.run(
        _run_flow(
            a2a_client,
            prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
            log_sink=logs.append,
        )
    )

    assert len(a2a_client.calls) == 1
    assert prompt_client.accept_calls == []
    assert prompt_client.from_data_calls == []
    assert _event_states(events) == ["TASK_STATE_SUBMITTED", "TASK_STATE_INPUT_REQUIRED"]
    unhandled_logs = [line for line in logs if line.startswith("[client] negotiation-unhandled:")]
    assert len(unhandled_logs) == 1
    assert "malformed/missing session context" in unhandled_logs[0]


def test_default_path_without_input_required_sends_exactly_once_and_keeps_the_stage_logs() -> None:
    prompt_client = _ScriptedPromptClient()
    a2a_client = _ScriptedA2AClient([_default_events()])

    logs: list[str] = []
    events = asyncio.run(_run_flow(a2a_client, prompt_client, log_sink=logs.append))

    assert len(a2a_client.calls) == 1
    assert prompt_client.accept_calls == []
    assert _event_states(events) == ["TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"]
    assert any(event["kind"] == "artifact" for event in events)
    # The default path's stage logs are unchanged (byte-identical flow: no negotiation stages).
    assert _stage_names(logs) == [
        "scenario-data",
        "input-raw",
        "sdk-call",
        "sdk-output-prompt",
        "sdk-output-meta",
        "a2a-send-message",
        "a2a-event",
        "response-inbound",
        "a2a-event",
        "a2a-event",
        "stream-completed",
    ]


def test_negotiation_round_trip_logs_the_accept_and_round2_send() -> None:
    prompt_client = _ScriptedPromptClient()
    a2a_client = _ScriptedA2AClient([_round1_events(metadata=_propose_metadata()), _round2_events()])

    logs: list[str] = []
    asyncio.run(
        _run_flow(
            a2a_client,
            prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
            log_sink=logs.append,
        )
    )

    accept_logs = [line for line in logs if line.startswith("[client] negotiation-accept:")]
    assert len(accept_logs) == 1
    assert "Negotiation-T/information-negotiation/accept-reject/v1" in accept_logs[0]
    assert "('region', 'Songshanhu Administration Committee')" in accept_logs[0]
    round2_send_logs = [line for line in logs if line.startswith("[client] a2a-send-message: round=2")]
    assert len(round2_send_logs) == 1
    assert "header[A2A-Extensions]=" in round2_send_logs[0]


# --- per-type accepts: target / feasibility over the from-data channel -----------------------


def test_target_propose_answers_with_the_typed_target_accept(_offline_env: Path) -> None:
    propose_metadata = _propose_metadata(template_uri=TARGET_NEGOTIATION_PROPOSE_URI)
    session_id = propose_metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["id"]
    prompt_client = _ScriptedPromptClient()
    a2a_client = _ScriptedA2AClient([_round1_events(metadata=propose_metadata), _round2_events()])

    logs: list[str] = []
    events = asyncio.run(
        _run_flow(
            a2a_client,
            prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
            negotiation_kind=scenario_data.NEGOTIATION_KIND_TARGET,
            env_path=_offline_env,
            log_sink=logs.append,
        )
    )

    # The round-2 accept templateUri addresses the target accept-reject template.
    round2_request, _round2_context = a2a_client.calls[1]
    metadata = _request_metadata(round2_request)
    assert metadata[TEMPLATE_URI_METADATA_KEY] == TARGET_NEGOTIATION_ACCEPT_REJECT_URI
    context = metadata[NEGOTIATION_CONTEXT_METADATA_KEY]
    assert context["id"] == session_id
    assert context["round"] == 2
    assert context["maxRounds"] == NegotiationContext.DEFAULT_MAX_ROUNDS
    assert context["performative"] == "ACCEPT"

    # The typed accept carries the confirmed intent of the scenario data (canned extraction text).
    assert len(prompt_client.accept_calls) == 1
    accept_data = prompt_client.accept_calls[0]
    assert isinstance(accept_data.content, TargetEndingContent)
    assert accept_data.content.conclusion == NegotiationConclusion.ACCEPT
    assert accept_data.content.confirmed_intent == negotiation_target_confirmation(env_path=_offline_env)
    assert accept_data.content.failure_reason is None
    assert accept_data.context.id == session_id
    assert accept_data.context.round == 2
    assert accept_data.context.performative == NegotiationPerformative.ACCEPT
    # The from-text accept API was not used on the data channel; the flow closes with COMPLETED.
    assert prompt_client.accept_from_text_calls == []
    assert _event_states(events)[-1] == "TASK_STATE_COMPLETED"
    accept_logs = [line for line in logs if line.startswith("[client] negotiation-accept:")]
    assert len(accept_logs) == 1
    assert "Negotiation-T/target-negotiation/accept-reject/v1" in accept_logs[0]
    assert f"confirmed_intent={negotiation_target_confirmation(env_path=_offline_env)}" in accept_logs[0]


def test_feasibility_propose_answers_with_the_typed_feasibility_accept(_offline_env: Path) -> None:
    propose_metadata = _propose_metadata(template_uri=FEASIBILITY_NEGOTIATION_PROPOSE_URI)
    prompt_client = _ScriptedPromptClient()
    a2a_client = _ScriptedA2AClient([_round1_events(metadata=propose_metadata), _round2_events()])

    events = asyncio.run(
        _run_flow(
            a2a_client,
            prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
            negotiation_kind=scenario_data.NEGOTIATION_KIND_FEASIBILITY,
            env_path=_offline_env,
        )
    )

    round2_request, _round2_context = a2a_client.calls[1]
    metadata = _request_metadata(round2_request)
    assert metadata[TEMPLATE_URI_METADATA_KEY] == FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI
    assert metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["round"] == 2
    assert metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["performative"] == "ACCEPT"

    # The typed accept carries the feasibility summary of the scenario data.
    assert len(prompt_client.accept_calls) == 1
    accept_data = prompt_client.accept_calls[0]
    assert isinstance(accept_data.content, FeasibilityEndingContent)
    assert accept_data.content.conclusion == NegotiationConclusion.ACCEPT
    assert accept_data.content.feasibility_summary == negotiation_feasibility_summary(env_path=_offline_env)
    assert prompt_client.accept_from_text_calls == []
    assert _event_states(events)[-1] == "TASK_STATE_COMPLETED"


@pytest.mark.parametrize(
    ("propose_uri", "accept_uri"),
    [
        (INFORMATION_NEGOTIATION_PROPOSE_URI, INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI),
        (TARGET_NEGOTIATION_PROPOSE_URI, TARGET_NEGOTIATION_ACCEPT_REJECT_URI),
        (FEASIBILITY_NEGOTIATION_PROPOSE_URI, FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI),
    ],
)
def test_propose_template_uri_dispatches_the_accept_kind_without_a_flag(
    _offline_env: Path, propose_uri: str, accept_uri: str
) -> None:
    """With no kind flag the accept type dispatches on the propose's templateUri (the wire)."""
    prompt_client = _ScriptedPromptClient()
    a2a_client = _ScriptedA2AClient(
        [_round1_events(metadata=_propose_metadata(template_uri=propose_uri)), _round2_events()]
    )

    asyncio.run(
        _run_flow(
            a2a_client,
            prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
            env_path=_offline_env,
        )
    )

    # The client still builds the round-2 accept, addressed to the matching accept-reject template.
    assert len(a2a_client.calls) == 2
    metadata = _request_metadata(a2a_client.calls[1][0])
    assert metadata[TEMPLATE_URI_METADATA_KEY] == accept_uri
    assert metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["round"] == 2
    assert metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["performative"] == "ACCEPT"
    assert len(prompt_client.accept_calls) == 1
    assert prompt_client.accept_from_text_calls == []


# --- from-text channel: the accept goes through the extraction API ---------------------------


def test_from_text_channel_generates_the_target_accept_via_extraction(_offline_env: Path) -> None:
    prompt_client = _ScriptedPromptClient()
    a2a_client = _ScriptedA2AClient(
        [_round1_events(metadata=_propose_metadata(template_uri=TARGET_NEGOTIATION_PROPOSE_URI)), _round2_events()]
    )

    asyncio.run(
        _run_flow(
            a2a_client,
            prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
            negotiation_kind=scenario_data.NEGOTIATION_KIND_TARGET,
            channel=CHANNEL_TEXT,
            env_path=_offline_env,
        )
    )

    # The from-text accept API replaced the from-data call; the rendered text carries the content.
    assert prompt_client.accept_calls == []
    assert len(prompt_client.accept_from_text_calls) == 1
    text, context, template_uri = prompt_client.accept_from_text_calls[0]
    assert text == f"Accept. {negotiation_target_confirmation(env_path=_offline_env)}"
    assert context.round == 2
    assert context.performative == NegotiationPerformative.ACCEPT
    assert template_uri == TARGET_NEGOTIATION_ACCEPT_REJECT_URI
    # The round-2 request shape is unchanged: same metadata keys, advanced ACCEPT session.
    metadata = _request_metadata(a2a_client.calls[1][0])
    assert set(metadata) == {
        TASK_T_EXTENSION_URI,
        NEGOTIATION_T_EXTENSION_URI,
        TEMPLATE_URI_METADATA_KEY,
        NEGOTIATION_CONTEXT_METADATA_KEY,
    }
    assert metadata[TEMPLATE_URI_METADATA_KEY] == TARGET_NEGOTIATION_ACCEPT_REJECT_URI
    assert metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["round"] == 2
    assert metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["performative"] == "ACCEPT"


def test_from_text_channel_renders_the_feasibility_summary_and_numbered_answers(_offline_env: Path) -> None:
    feasibility_prompt_client = _ScriptedPromptClient()
    feasibility_a2a_client = _ScriptedA2AClient(
        [
            _round1_events(metadata=_propose_metadata(template_uri=FEASIBILITY_NEGOTIATION_PROPOSE_URI)),
            _round2_events(),
        ]
    )
    asyncio.run(
        _run_flow(
            feasibility_a2a_client,
            feasibility_prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
            negotiation_kind=scenario_data.NEGOTIATION_KIND_FEASIBILITY,
            channel=CHANNEL_TEXT,
            env_path=_offline_env,
        )
    )
    text, _context, template_uri = feasibility_prompt_client.accept_from_text_calls[0]
    assert text == f"Accept. {negotiation_feasibility_summary(env_path=_offline_env)}"
    assert template_uri == FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI

    # The information accept keeps the numbered-items rendering of the answers.
    information_prompt_client = _ScriptedPromptClient()
    information_a2a_client = _ScriptedA2AClient([_round1_events(metadata=_propose_metadata()), _round2_events()])
    asyncio.run(
        _run_flow(
            information_a2a_client,
            information_prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
            channel=CHANNEL_TEXT,
            env_path=_offline_env,
        )
    )
    text, _context, template_uri = information_prompt_client.accept_from_text_calls[0]
    assert text == "Accept. 1. region：Songshanhu Administration Committee；"
    assert template_uri == INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI


def test_target_accept_round2_requests_are_equivalent_across_channels(_offline_env: Path) -> None:
    """The from-data and from-text target accepts produce the same round-2 wire metadata."""
    propose_metadata = _propose_metadata(template_uri=TARGET_NEGOTIATION_PROPOSE_URI)
    round2_metadata: list[dict[str, object]] = []
    for channel in (CHANNEL_DATA, CHANNEL_TEXT):
        prompt_client = _ScriptedPromptClient()
        a2a_client = _ScriptedA2AClient([_round1_events(metadata=propose_metadata), _round2_events()])
        asyncio.run(
            _run_flow(
                a2a_client,
                prompt_client,
                negotiation_params=dict(_NEGOTIATION_PARAMS),
                negotiation_answers=dict(_NEGOTIATION_ANSWERS),
                negotiation_kind=scenario_data.NEGOTIATION_KIND_TARGET,
                channel=channel,
                env_path=_offline_env,
            )
        )
        round2_metadata.append(_request_metadata(a2a_client.calls[1][0]))

    data_metadata, text_metadata = round2_metadata
    assert data_metadata[TEMPLATE_URI_METADATA_KEY] == text_metadata[TEMPLATE_URI_METADATA_KEY]
    assert data_metadata[NEGOTIATION_CONTEXT_METADATA_KEY] == text_metadata[NEGOTIATION_CONTEXT_METADATA_KEY]


# --- unhandled degradations stay type-agnostic -----------------------------------------------


def test_unknown_propose_template_uri_without_a_kind_flag_stays_unhandled(_offline_env: Path) -> None:
    """A parseable session but an unrecognized propose templateUri keeps the single-round flow."""
    metadata: dict[str, object] = {
        NEGOTIATION_T_EXTENSION_URI: "propose text",
        NEGOTIATION_CONTEXT_METADATA_KEY: {
            "id": str(uuid.uuid4()),
            "round": 1,
            "maxRounds": NegotiationContext.DEFAULT_MAX_ROUNDS,
            "performative": "PROPOSE",
        },
    }
    prompt_client = _ScriptedPromptClient()
    a2a_client = _ScriptedA2AClient([_round1_events(metadata=metadata)])

    logs: list[str] = []
    asyncio.run(
        _run_flow(
            a2a_client,
            prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
            env_path=_offline_env,
            log_sink=logs.append,
        )
    )

    assert len(a2a_client.calls) == 1
    assert prompt_client.accept_calls == []
    assert prompt_client.accept_from_text_calls == []
    unhandled_logs = [line for line in logs if line.startswith("[client] negotiation-unhandled:")]
    assert len(unhandled_logs) == 1
    assert "unrecognized propose templateUri" in unhandled_logs[0]


def test_malformed_context_degrades_to_unhandled_with_a_kind_flag_too(_offline_env: Path) -> None:
    """The malformed-context degradation keeps working for every negotiation type."""
    prompt_client = _ScriptedPromptClient()
    a2a_client = _ScriptedA2AClient([_round1_events(metadata=_malformed_propose_metadata())])

    logs: list[str] = []
    asyncio.run(
        _run_flow(
            a2a_client,
            prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
            negotiation_kind=scenario_data.NEGOTIATION_KIND_TARGET,
            env_path=_offline_env,
            log_sink=logs.append,
        )
    )

    assert len(a2a_client.calls) == 1
    assert prompt_client.accept_calls == []
    assert prompt_client.accept_from_text_calls == []
    unhandled_logs = [line for line in logs if line.startswith("[client] negotiation-unhandled:")]
    assert len(unhandled_logs) == 1
    assert "malformed/missing session context" in unhandled_logs[0]


# --- sse_event_consumer: metadata surfacing and context_id -----------------------------------


def test_status_event_surfaces_the_message_metadata_and_context_id() -> None:
    event = normalize_event(
        _status_update(state=TaskState.TASK_STATE_INPUT_REQUIRED, text="need more", metadata={"key": "value"})
    )

    assert event["kind"] == "status"
    assert event["task_id"] == _TASK_ID
    assert event["context_id"] == _CONTEXT_ID
    assert event["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert event["text"] == "need more"
    assert event["metadata"] == {"key": "value"}


def test_status_event_without_metadata_keeps_the_old_shape() -> None:
    event = normalize_event(_status_update(state=TaskState.TASK_STATE_WORKING, text="working"))

    assert event == {
        "kind": "status",
        "task_id": _TASK_ID,
        "context_id": _CONTEXT_ID,
        "state": "TASK_STATE_WORKING",
        "text": "working",
    }
    assert "metadata" not in event


def test_task_event_gains_the_context_id() -> None:
    event = normalize_event(_task_event(TaskState.TASK_STATE_SUBMITTED))

    assert event == {
        "kind": "status",
        "task_id": _TASK_ID,
        "context_id": _CONTEXT_ID,
        "state": "TASK_STATE_SUBMITTED",
    }


# --- negotiation response seam: Protocol contract, shim equivalence, injected providers --------


def test_scripted_provider_satisfies_the_negotiation_response_protocol(_offline_env: Path) -> None:
    """Contract test: the demo provider structurally satisfies the client seam Protocol.

    ``NegotiationResponseProvider`` is ``@runtime_checkable``, so the idiomatic conformance check is
    an ``isinstance`` probe (it verifies the member set at runtime; signatures stay the static
    checks' concern). The seam's data shapes are pinned here too.
    """
    provider = ScriptedNegotiationResponseProvider(
        negotiation_params=dict(_NEGOTIATION_PARAMS),
        negotiation_answers=dict(_NEGOTIATION_ANSWERS),
        negotiation_kind=None,
        env_path=_offline_env,
    )
    assert isinstance(provider, NegotiationResponseProvider)
    assert [field.name for field in dataclasses.fields(NegotiationProposeView)] == [
        "kind",
        "template_uri",
        "prompt_text",
    ]
    assert [field.name for field in dataclasses.fields(NegotiationDecision)] == [
        "conclusion",
        "filled_params",
        "confirmed_intent",
        "feasibility_summary",
        "reason",
    ]
    decision = NegotiationDecision(conclusion="accept")
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.conclusion = "reject"


def test_scripted_provider_mirrors_the_legacy_selection(_offline_env: Path) -> None:
    """The demo provider's decisions mirror the scripted selection for every kind (plan R3)."""
    provider = ScriptedNegotiationResponseProvider(
        negotiation_params=dict(_NEGOTIATION_PARAMS),
        negotiation_answers=dict(_NEGOTIATION_ANSWERS),
        negotiation_kind=None,
        env_path=_offline_env,
    )
    session = NegotiationContext(id="s", round=1, max_rounds=5, performative=NegotiationPerformative.PROPOSE)

    information = provider.respond(
        NegotiationProposeView(
            kind=scenario_data.NEGOTIATION_KIND_INFORMATION,
            template_uri=INFORMATION_NEGOTIATION_PROPOSE_URI,
            prompt_text="propose text",
        ),
        session,
    )
    assert information == NegotiationDecision(conclusion="accept", filled_params=dict(_NEGOTIATION_ANSWERS))
    target = provider.respond(
        NegotiationProposeView(
            kind=scenario_data.NEGOTIATION_KIND_TARGET,
            template_uri=TARGET_NEGOTIATION_PROPOSE_URI,
            prompt_text="propose text",
        ),
        session,
    )
    assert target == NegotiationDecision(
        conclusion="accept", confirmed_intent=negotiation_target_confirmation(env_path=_offline_env)
    )
    feasibility = provider.respond(
        NegotiationProposeView(
            kind=scenario_data.NEGOTIATION_KIND_FEASIBILITY,
            template_uri=FEASIBILITY_NEGOTIATION_PROPOSE_URI,
            prompt_text="propose text",
        ),
        session,
    )
    assert feasibility == NegotiationDecision(
        conclusion="accept", feasibility_summary=negotiation_feasibility_summary(env_path=_offline_env)
    )


def test_explicit_scripted_provider_matches_the_legacy_shim_outputs(_offline_env: Path) -> None:
    """provider=None + legacy params (the internal shim) and provider=Scripted(...) are equivalent.

    The shim run passes the legacy arguments; the seam run supplies the same scripted provider
    explicitly and NO ``negotiation_answers`` — the accept content comes from the provider's
    decision, and both runs produce identical round-2 requests, accept content and stage logs.
    """
    propose_metadata = _propose_metadata()
    legacy_prompt_client = _ScriptedPromptClient()
    legacy_logs: list[str] = []
    legacy_events = asyncio.run(
        _run_flow(
            _ScriptedA2AClient([_round1_events(metadata=propose_metadata), _round2_events()]),
            legacy_prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
            env_path=_offline_env,
            log_sink=legacy_logs.append,
        )
    )
    scripted_provider = ScriptedNegotiationResponseProvider(
        negotiation_params=dict(_NEGOTIATION_PARAMS),
        negotiation_answers=dict(_NEGOTIATION_ANSWERS),
        negotiation_kind=None,
        env_path=_offline_env,
    )
    seam_prompt_client = _ScriptedPromptClient()
    seam_logs: list[str] = []
    seam_events = asyncio.run(
        _run_flow(
            _ScriptedA2AClient([_round1_events(metadata=propose_metadata), _round2_events()]),
            seam_prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_response_provider=scripted_provider,
            env_path=_offline_env,
            log_sink=seam_logs.append,
        )
    )

    assert _event_states(seam_events) == _event_states(legacy_events)
    assert _stage_names(seam_logs) == _stage_names(legacy_logs)
    # The accept content is identical (items from the legacy answers == the provider's decision).
    assert len(legacy_prompt_client.accept_calls) == len(seam_prompt_client.accept_calls) == 1
    legacy_accept = legacy_prompt_client.accept_calls[0]
    seam_accept = seam_prompt_client.accept_calls[0]
    assert seam_accept.context.id == legacy_accept.context.id
    assert seam_accept.context.round == legacy_accept.context.round == 2
    assert seam_accept.context.performative == NegotiationPerformative.ACCEPT
    assert (
        [(item.name, item.value) for item in seam_accept.content.items]
        == [(item.name, item.value) for item in legacy_accept.content.items]
        == list(_NEGOTIATION_ANSWERS.items())
    )


class _RecordingResponseProvider:
    """Injected fake ``NegotiationResponseProvider`` proving the seam replaces the scripted behavior."""

    def __init__(self, decision: NegotiationDecision) -> None:
        self._decision = decision
        self.views: list[NegotiationProposeView] = []
        self.sessions: list[NegotiationContext] = []

    def respond(self, propose: NegotiationProposeView, session: NegotiationContext) -> NegotiationDecision:
        self.views.append(propose)
        self.sessions.append(session)
        return self._decision


def test_injected_provider_replaces_the_scripted_accept_end_to_end(_offline_env: Path) -> None:
    """A custom accept decision drives the round-2 accept: its filled_params become the items."""
    propose_metadata = _propose_metadata()
    session_id = propose_metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["id"]
    prompt_client = _ScriptedPromptClient()
    a2a_client = _ScriptedA2AClient([_round1_events(metadata=propose_metadata), _round2_events()])
    provider = _RecordingResponseProvider(
        NegotiationDecision(conclusion="accept", filled_params={"region": "Custom filled region"})
    )

    events = asyncio.run(
        _run_flow(
            a2a_client,
            prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),  # Task-T refill; NO scripted answers
            negotiation_response_provider=provider,
            env_path=_offline_env,
        )
    )

    # The provider saw the propose view verbatim: resolved kind, templateUri, rendered text, session.
    assert len(provider.views) == 1
    view = provider.views[0]
    assert (view.kind, view.template_uri, view.prompt_text) == (
        scenario_data.NEGOTIATION_KIND_INFORMATION,
        INFORMATION_NEGOTIATION_PROPOSE_URI,
        "Please provide region: Coverage region of the energy-saving task.",
    )
    assert provider.sessions[0].id == session_id
    assert provider.sessions[0].performative == NegotiationPerformative.PROPOSE

    # The accept was built from the decision (the seam replaced the scripted answer content).
    assert len(a2a_client.calls) == 2
    assert len(prompt_client.accept_calls) == 1
    accept_data = prompt_client.accept_calls[0]
    assert [(item.name, item.value) for item in accept_data.content.items] == [("region", "Custom filled region")]
    # The Task-T prompt refill remains the flow's wire mechanic fed by the caller.
    assert prompt_client.from_data_calls == [(_NEGOTIATION_PARAMS, ENERGY_SAVING_TEMPLATE_URI)]
    assert _event_states(events)[-1] == "TASK_STATE_COMPLETED"


def test_reject_decision_logs_negotiation_decision_and_stays_on_round_1(_offline_env: Path) -> None:
    """A reject decision logs the dedicated line and ends the flow like unhandled (plan R5)."""
    prompt_client = _ScriptedPromptClient()
    a2a_client = _ScriptedA2AClient([_round1_events(metadata=_propose_metadata())])
    provider = _RecordingResponseProvider(NegotiationDecision(conclusion="reject", reason="not now"))

    logs: list[str] = []
    events = asyncio.run(
        _run_flow(
            a2a_client,
            prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_response_provider=provider,
            env_path=_offline_env,
            log_sink=logs.append,
        )
    )

    assert len(a2a_client.calls) == 1  # no round-2 send
    assert prompt_client.accept_calls == []
    assert prompt_client.from_data_calls == []
    decision_logs = [line for line in logs if line.startswith("[client] negotiation-decision:")]
    assert decision_logs == [
        "[client] negotiation-decision: reject (not wired in this demo; see the integration guide)"
    ]
    assert _event_states(events) == ["TASK_STATE_SUBMITTED", "TASK_STATE_INPUT_REQUIRED"]
    summary_lines = [line for line in logs if line.startswith("[client] stream-completed:")]
    assert summary_lines == ["[client] stream-completed: events=2 artifacts=0"]


def test_abort_decision_logs_negotiation_decision_and_stays_on_round_1(_offline_env: Path) -> None:
    """An abort decision behaves like reject: dedicated log, no round-2 send (plan R5)."""
    prompt_client = _ScriptedPromptClient()
    a2a_client = _ScriptedA2AClient([_round1_events(metadata=_propose_metadata())])
    provider = _RecordingResponseProvider(NegotiationDecision(conclusion="abort", reason="budget spent"))

    logs: list[str] = []
    events = asyncio.run(
        _run_flow(
            a2a_client,
            prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_response_provider=provider,
            env_path=_offline_env,
            log_sink=logs.append,
        )
    )

    assert len(a2a_client.calls) == 1
    assert prompt_client.accept_calls == []
    decision_logs = [line for line in logs if line.startswith("[client] negotiation-decision:")]
    assert decision_logs == ["[client] negotiation-decision: abort (not wired in this demo; see the integration guide)"]
    assert _event_states(events) == ["TASK_STATE_SUBMITTED", "TASK_STATE_INPUT_REQUIRED"]
    summary_lines = [line for line in logs if line.startswith("[client] stream-completed:")]
    assert summary_lines == ["[client] stream-completed: events=2 artifacts=0"]


def test_target_accept_missing_confirmed_intent_is_logged_and_stays_unhandled(_offline_env: Path) -> None:
    """An accept whose per-conclusion field is missing logs the problem and ends as unhandled."""
    prompt_client = _ScriptedPromptClient()
    a2a_client = _ScriptedA2AClient(
        [_round1_events(metadata=_propose_metadata(template_uri=TARGET_NEGOTIATION_PROPOSE_URI))]
    )
    provider = _RecordingResponseProvider(NegotiationDecision(conclusion="accept"))

    logs: list[str] = []
    events = asyncio.run(
        _run_flow(
            a2a_client,
            prompt_client,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_response_provider=provider,
            env_path=_offline_env,
            log_sink=logs.append,
        )
    )

    assert len(a2a_client.calls) == 1
    assert prompt_client.accept_calls == []
    assert prompt_client.accept_from_text_calls == []
    decision_logs = [line for line in logs if line.startswith("[client] negotiation-decision:")]
    assert len(decision_logs) == 1
    assert "missing confirmed_intent" in decision_logs[0]
    assert _event_states(events) == ["TASK_STATE_SUBMITTED", "TASK_STATE_INPUT_REQUIRED"]
