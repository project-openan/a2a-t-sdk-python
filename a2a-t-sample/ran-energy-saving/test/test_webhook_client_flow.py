"""Offline tests of the webhook transport flow (v3) of the ran-energy-saving demo.

The powerful pattern: a REAL embedded ``WebhookReceiver`` (ephemeral port, in-process HTTP only)
plus a fake a2a client whose ``send_message`` records every request (round-1 must carry
``return_immediately=True`` and the inline push config; round-2 must carry NO inline config) and
whose side effect POSTs the scripted StreamResponse-shaped bodies to the receiver URL with the
registered token header — simulating the server's push sender, so the ENTIRE webhook pipeline
(normalize → queue → flow) runs without the server stack. The fake
``create_task_push_notification_config`` records the round-2 endpoint re-registration
(R4) with the same url/token, and the call order send→register→send is pinned.

Covered: the default scenario (artifact end, no round 2, summary counts WEBHOOK-received events
only — the RPC reply is logged separately per R6), the 3-type negotiation matrix over the fromData
channel (per-type accept content + re-registration + COMPLETED end), the target fromText spot
check, the unhandled/malformed INPUT_REQUIRED degradations (identical to SSE), the terminal-state
end signals (round-1/round-2 REJECTED end promptly with ``end=<state>`` on the summary — no idle
hang, final fix), the idle-timeout backstop (R5), the
``normalize_webhook_payload``/``normalize_event`` parity, and the server
assembly (push store/sender wired unconditionally into ``build_rest_app``).
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from a2a.client.client import ClientCallContext
from a2a.server.tasks import BasePushNotificationSender, InMemoryPushNotificationConfigStore
from a2a.types import (
    Artifact,
    Message,
    Role,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
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
    InformationEndingContent,
    NegotiationEndingData,
    TargetEndingContent,
)
from google.protobuf.json_format import MessageToDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from client.client_flow import _parse_negotiation_context, normalize_webhook_payload, run_client_flow
from client.negotiation_response import (
    NegotiationDecision,
    NegotiationProposeView,
    NegotiationResponseProvider,
)
from client.scenario_data import (
    CHANNEL_DATA,
    CHANNEL_TEXT,
    ENERGY_SAVING_TEMPLATE_URI,
    NEGOTIATION_KIND_TARGET,
    negotiation_feasibility_summary,
    negotiation_target_confirmation,
)
from common.sse_event_consumer import normalize_event
from common.webhook_receiver import WebhookReceiver

#: An offline ``.env`` (language pinned) written per test; the accept builders' scenario accessors read it.
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

#: The registered push token (the ``A2AT_SAMPLE_WEBHOOK_TOKEN`` default).
_DEMO_TOKEN = "demo-webhook-token"

#: Filled Task-T parameters and accept answers handed to the flow (negotiation round 2).
_NEGOTIATION_PARAMS: dict[str, object] = {
    "operationType": "create",
    "region": "Songshanhu Administration Committee",
    "energySavingGoal": "Maximize energy saving",
    "guaranteedThroughputTarget": "{00:00~07:00,2Mbps}",
}
_NEGOTIATION_ANSWERS: dict[str, str] = {"region": "Songshanhu Administration Committee"}


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


class _WebhookFakeA2AClient:
    """Fake a2a client simulating the server side over the REAL receiver.

    ``send_message`` records the request (and its call context), POSTs the round's scripted
    StreamResponse-shaped bodies to the receiver URL with the registered token header (the push
    sender's behavior), and yields the single non-streaming RPC reply — the first Task of the
    round, per ``return_immediately`` semantics (R6: the reply also gets pushed, so it overlaps
    with the webhook events). ``create_task_push_notification_config`` records the round-2
    re-registration call. ``call_log`` pins the send→register→send order.
    """

    def __init__(
        self,
        *,
        httpx_client: httpx.AsyncClient,
        receiver_url: str,
        token: str,
        rounds: list[list[StreamResponse]],
    ) -> None:
        self._http = httpx_client
        self._receiver_url = receiver_url
        self._token = token
        self._rounds = rounds
        self.send_calls: list[tuple[SendMessageRequest, ClientCallContext | None]] = []
        self.register_calls: list[tuple[str, TaskPushNotificationConfig]] = []
        self.call_log: list[str] = []
        self.push_status_codes: list[int] = []

    async def send_message(
        self, request: SendMessageRequest, *, context: ClientCallContext | None = None
    ) -> AsyncIterator[StreamResponse]:
        self.send_calls.append((request, context))
        self.call_log.append(f"send:{len(self.send_calls)}")
        script = self._rounds[len(self.send_calls) - 1]
        reply: StreamResponse | None = None
        for event in script:
            # The script events are StreamResponse-shaped; MessageToDict(event) is exactly the
            # body the server's push sender POSTs (MessageToDict(to_stream_response(event))).
            body = MessageToDict(event)
            response = await self._http.post(
                self._receiver_url, json=body, headers={"X-A2A-Notification-Token": self._token}
            )
            self.push_status_codes.append(response.status_code)
            if event.HasField("task") and reply is None:
                reply = StreamResponse(task=event.task)
        if reply is None:
            # No Task event in this round's script: the aggregated reply carries the current state.
            state = TaskState.TASK_STATE_SUBMITTED
            for event in script:
                if event.HasField("status_update"):
                    state = event.status_update.status.state
                    break
            reply = StreamResponse(task=Task(id=_TASK_ID, context_id=_CONTEXT_ID, status=TaskStatus(state=state)))
        yield reply

    async def create_task_push_notification_config(
        self, request: TaskPushNotificationConfig, *, context: ClientCallContext | None = None
    ) -> TaskPushNotificationConfig:
        self.register_calls.append((request.task_id, request))
        self.call_log.append(f"register:{len(self.register_calls)}")
        return request  # the server echoes the config back (on_create_task_push_notification_config)


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


def _default_round1_script() -> list[StreamResponse]:
    """Default-path script pushed via webhook: SUBMITTED Task, one WORKING step, then the artifact."""
    return [
        _task_event(TaskState.TASK_STATE_SUBMITTED),
        _status_update(state=TaskState.TASK_STATE_WORKING, text="progress step 1"),
        _artifact_event(),
    ]


def _negotiation_round1_script(metadata: dict[str, object]) -> list[StreamResponse]:
    """Negotiation round-1 script pushed via webhook: SUBMITTED Task, then INPUT_REQUIRED propose."""
    return [
        _task_event(TaskState.TASK_STATE_SUBMITTED),
        _status_update(
            state=TaskState.TASK_STATE_INPUT_REQUIRED,
            text="Additional information is required to start the energy saving task",
            metadata=metadata,
        ),
    ]


def _negotiation_round2_script() -> list[StreamResponse]:
    """Negotiation round-2 script pushed via webhook: WORKING steps, artifact, then COMPLETED."""
    return [
        _status_update(state=TaskState.TASK_STATE_WORKING, text="progress step 1"),
        _status_update(state=TaskState.TASK_STATE_WORKING, text="progress step 2"),
        _artifact_event(),
        _status_update(state=TaskState.TASK_STATE_COMPLETED, text="Energy saving task completed"),
    ]


async def _run_webhook_scenario(
    *,
    rounds: list[list[StreamResponse]],
    env_path: Path,
    negotiation_params: dict[str, object] | None = None,
    negotiation_answers: dict[str, str] | None = None,
    negotiation_kind: str | None = None,
    negotiation_response_provider: NegotiationResponseProvider | None = None,
    channel: str = CHANNEL_DATA,
    idle_timeout: float = 2.0,
    token: str = _DEMO_TOKEN,
) -> tuple[list[dict[str, object]], _WebhookFakeA2AClient, _ScriptedPromptClient, list[str], WebhookReceiver]:
    """Run ``run_client_flow`` in webhook mode against the REAL receiver and the fake clients.

    The receiver feeds the queue exactly like ``client_main`` does (``on_event=queue.put_nowait``),
    so the flow consumes real push deliveries end to end; everything is stopped in ``finally``.
    """
    logs: list[str] = []
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    receiver = WebhookReceiver(token=token, on_event=queue.put_nowait, port=0, log_sink=logs.append)
    await receiver.start()
    prompt_client = _ScriptedPromptClient()
    a2a_client = _WebhookFakeA2AClient(
        httpx_client=httpx.AsyncClient(trust_env=False), receiver_url=receiver.url, token=token, rounds=rounds
    )
    try:
        events = await run_client_flow(
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
            transport="webhook",
            webhook_queue=queue,
            push_url=receiver.url,
            push_token=token,
            webhook_idle_timeout=idle_timeout,
            log_sink=logs.append,
        )
    finally:
        await receiver.stop()
        await a2a_client._http.aclose()
    return events, a2a_client, prompt_client, logs, receiver


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


# --- default scenario: artifact end, no round 2, webhook-only counting -------------------------


def test_default_scenario_webhook_ends_on_artifact_and_counts_webhook_events(_offline_env: Path) -> None:
    events, a2a_client, prompt_client, logs, receiver = asyncio.run(
        _run_webhook_scenario(rounds=[_default_round1_script()], env_path=_offline_env)
    )

    # Every scripted push got through the real receiver with 200.
    assert a2a_client.push_status_codes == [200, 200, 200]
    assert _event_states(events) == ["TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"]
    assert any(event["kind"] == "artifact" for event in events)
    assert len(events) == 3  # SUBMITTED + WORKING + artifact, all WEBHOOK-received
    assert len(a2a_client.send_calls) == 1  # no round 2
    assert a2a_client.register_calls == []
    assert prompt_client.accept_calls == []

    # Round-1 request carries return_immediately + the inline push config (R2/R4, round 1).
    request, context = a2a_client.send_calls[0]
    assert request.configuration.return_immediately is True
    assert request.configuration.HasField("task_push_notification_config")
    inline_config = request.configuration.task_push_notification_config
    assert inline_config.url == receiver.url
    assert inline_config.token == _DEMO_TOKEN
    assert set(_request_metadata(request)) == {TASK_T_EXTENSION_URI, TEMPLATE_URI_METADATA_KEY}
    assert context is not None
    assert context.service_parameters == {"A2A-Extensions": TASK_T_EXTENSION_URI}

    # Summary counts WEBHOOK-received events only (the RPC reply is logged separately, R6).
    summary_lines = [line for line in logs if line.startswith("[client] stream-completed:")]
    assert summary_lines == ["[client] stream-completed: events=3 artifacts=1 transport=webhook"]
    reply_lines = [line for line in logs if line.startswith("[client] webhook-rpc-reply:")]
    assert len(reply_lines) == 1

    # The webhook-mode stage logs mirror the SSE flow, with webhook-event instead of a2a-event.
    assert _stage_names(logs) == [
        "scenario-data",
        "input-raw",
        "sdk-call",
        "sdk-output-prompt",
        "sdk-output-meta",
        "a2a-send-message",
        "webhook-rpc-reply",
        "webhook-event",
        "webhook-event",
        "webhook-event",
        "stream-completed",
    ]


# --- negotiation matrix: 3 types x webhook (fromData) ------------------------------------------


@pytest.mark.parametrize(
    ("propose_uri", "accept_uri"),
    [
        (INFORMATION_NEGOTIATION_PROPOSE_URI, INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI),
        (TARGET_NEGOTIATION_PROPOSE_URI, TARGET_NEGOTIATION_ACCEPT_REJECT_URI),
        (FEASIBILITY_NEGOTIATION_PROPOSE_URI, FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI),
    ],
)
def test_negotiation_webhook_round_trip_re_registers_and_completes(
    _offline_env: Path, propose_uri: str, accept_uri: str
) -> None:
    """3-type matrix: INPUT_REQUIRED -> endpoint re-registration (R4) -> accept -> COMPLETED (R5)."""
    propose_metadata = _propose_metadata(template_uri=propose_uri)
    session_id = propose_metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["id"]

    events, a2a_client, prompt_client, logs, receiver = asyncio.run(
        _run_webhook_scenario(
            rounds=[_negotiation_round1_script(propose_metadata), _negotiation_round2_script()],
            env_path=_offline_env,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
        )
    )

    # Pushes all delivered; the flow ends on the COMPLETED webhook event.
    assert a2a_client.push_status_codes == [200] * 6
    assert _event_states(events) == [
        "TASK_STATE_SUBMITTED",
        "TASK_STATE_INPUT_REQUIRED",
        "TASK_STATE_WORKING",
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
    ]
    assert any(event["kind"] == "artifact" for event in events)

    # Order evidence: round-1 send -> endpoint re-registration -> round-2 send.
    assert a2a_client.call_log == ["send:1", "register:1", "send:2"]

    # Round-2 request: non-blocking, NO inline config (already registered via the endpoint),
    # round-1 identity, per-type accept templateUri, advanced ACCEPT session.
    round2_request, round2_context = a2a_client.send_calls[1]
    assert round2_request.configuration.return_immediately is True
    assert not round2_request.configuration.HasField("task_push_notification_config")
    assert round2_request.message.task_id == _TASK_ID
    assert round2_request.message.context_id == _CONTEXT_ID
    metadata = _request_metadata(round2_request)
    assert metadata[TEMPLATE_URI_METADATA_KEY] == accept_uri
    context = metadata[NEGOTIATION_CONTEXT_METADATA_KEY]
    assert context["id"] == session_id
    assert context["round"] == 2
    assert context["maxRounds"] == NegotiationContext.DEFAULT_MAX_ROUNDS
    assert context["performative"] == "ACCEPT"
    assert round2_context is not None
    header = str((round2_context.service_parameters or {}).get("A2A-Extensions", ""))
    assert {part.strip() for part in header.split(",") if part.strip()} == {
        TASK_T_EXTENSION_URI,
        NEGOTIATION_T_EXTENSION_URI,
    }

    # Re-registration (R4): same task id, SAME url/token as round 1's inline config.
    registered_task_id, registered_config = a2a_client.register_calls[0]
    assert registered_task_id == _TASK_ID
    assert registered_config.url == receiver.url
    assert registered_config.token == _DEMO_TOKEN
    reregister_logs = [line for line in logs if line.startswith("[client] webhook-push-reregister:")]
    assert len(reregister_logs) == 1

    # The accept was generated from typed data (fromData channel) for the proposed type.
    assert prompt_client.from_data_calls == [(_NEGOTIATION_PARAMS, ENERGY_SAVING_TEMPLATE_URI)]
    assert len(prompt_client.accept_calls) == 1
    assert prompt_client.accept_from_text_calls == []
    assert prompt_client.accept_calls[0].content.conclusion == NegotiationConclusion.ACCEPT
    assert prompt_client.accept_calls[0].context.round == 2

    # Summary: 2 webhook events (round 1) + 4 (round 2), 1 artifact, terminal COMPLETED end.
    summary_lines = [line for line in logs if line.startswith("[client] stream-completed:")]
    assert summary_lines == [
        "[client] stream-completed: events=6 artifacts=1 transport=webhook end=TASK_STATE_COMPLETED"
    ]


def test_information_webhook_accept_carries_the_answer_items(_offline_env: Path) -> None:
    """The information accept keeps the typed items (the supplied answers) under webhook too."""
    _, _a2a_client, prompt_client, _logs, _receiver = asyncio.run(
        _run_webhook_scenario(
            rounds=[_negotiation_round1_script(_propose_metadata()), _negotiation_round2_script()],
            env_path=_offline_env,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
        )
    )
    accept_data = prompt_client.accept_calls[0]
    assert isinstance(accept_data.content, InformationEndingContent)
    assert [(item.name, item.value) for item in accept_data.content.items] == list(_NEGOTIATION_ANSWERS.items())


def test_target_webhook_accept_carries_the_confirmed_intent(_offline_env: Path) -> None:
    _, _a2a_client, prompt_client, logs, _receiver = asyncio.run(
        _run_webhook_scenario(
            rounds=[
                _negotiation_round1_script(_propose_metadata(template_uri=TARGET_NEGOTIATION_PROPOSE_URI)),
                _negotiation_round2_script(),
            ],
            env_path=_offline_env,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
        )
    )
    accept_data = prompt_client.accept_calls[0]
    assert isinstance(accept_data.content, TargetEndingContent)
    assert accept_data.content.confirmed_intent == negotiation_target_confirmation(env_path=_offline_env)
    accept_logs = [line for line in logs if line.startswith("[client] negotiation-accept:")]
    assert "Negotiation-T/target-negotiation/accept-reject/v1" in accept_logs[0]


def test_feasibility_webhook_accept_carries_the_feasibility_summary(_offline_env: Path) -> None:
    _, _a2a_client, prompt_client, _logs, _receiver = asyncio.run(
        _run_webhook_scenario(
            rounds=[
                _negotiation_round1_script(_propose_metadata(template_uri=FEASIBILITY_NEGOTIATION_PROPOSE_URI)),
                _negotiation_round2_script(),
            ],
            env_path=_offline_env,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
        )
    )
    accept_data = prompt_client.accept_calls[0]
    assert isinstance(accept_data.content, FeasibilityEndingContent)
    assert accept_data.content.feasibility_summary == negotiation_feasibility_summary(env_path=_offline_env)


# --- target fromText x webhook spot check (D8) ---------------------------------------------------


def test_target_from_text_channel_webhook_spot_check(_offline_env: Path) -> None:
    """fromText accept generation works under webhook: rendered text -> extraction API call."""
    _, a2a_client, prompt_client, _logs, _receiver = asyncio.run(
        _run_webhook_scenario(
            rounds=[
                _negotiation_round1_script(_propose_metadata(template_uri=TARGET_NEGOTIATION_PROPOSE_URI)),
                _negotiation_round2_script(),
            ],
            env_path=_offline_env,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
            negotiation_kind="target",
            channel=CHANNEL_TEXT,
        )
    )

    assert prompt_client.accept_calls == []
    assert len(prompt_client.accept_from_text_calls) == 1
    text, context, template_uri = prompt_client.accept_from_text_calls[0]
    assert text == f"Accept. {negotiation_target_confirmation(env_path=_offline_env)}"
    assert context.round == 2
    assert context.performative == NegotiationPerformative.ACCEPT
    assert template_uri == TARGET_NEGOTIATION_ACCEPT_REJECT_URI

    # Round-2 wire shape unchanged across channels: send -> register -> send, COMPLETED end.
    assert a2a_client.call_log == ["send:1", "register:1", "send:2"]
    round2_request = a2a_client.send_calls[1][0]
    metadata = _request_metadata(round2_request)
    assert metadata[TEMPLATE_URI_METADATA_KEY] == TARGET_NEGOTIATION_ACCEPT_REJECT_URI
    assert metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["performative"] == "ACCEPT"


# --- unhandled degradations behave identically under webhook -------------------------------------


def test_input_required_without_answers_is_unhandled_and_ends_after_round1_webhook(_offline_env: Path) -> None:
    _, a2a_client, prompt_client, logs, _receiver = asyncio.run(
        _run_webhook_scenario(
            rounds=[_negotiation_round1_script(_propose_metadata())],
            env_path=_offline_env,
        )
    )

    assert len(a2a_client.send_calls) == 1
    assert a2a_client.register_calls == []
    assert prompt_client.accept_calls == []
    unhandled_logs = [line for line in logs if line.startswith("[client] negotiation-unhandled:")]
    assert len(unhandled_logs) == 1
    assert "no negotiation answers supplied" in unhandled_logs[0]
    summary_lines = [line for line in logs if line.startswith("[client] stream-completed:")]
    assert summary_lines == ["[client] stream-completed: events=2 artifacts=0 transport=webhook"]


def test_malformed_context_is_unhandled_and_ends_after_round1_webhook(_offline_env: Path) -> None:
    _, a2a_client, prompt_client, logs, _receiver = asyncio.run(
        _run_webhook_scenario(
            rounds=[_negotiation_round1_script(_malformed_propose_metadata())],
            env_path=_offline_env,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
        )
    )

    assert len(a2a_client.send_calls) == 1
    assert prompt_client.accept_calls == []
    unhandled_logs = [line for line in logs if line.startswith("[client] negotiation-unhandled:")]
    assert len(unhandled_logs) == 1
    assert "malformed/missing session context" in unhandled_logs[0]


# --- negotiation response seam: injected provider over webhook (plan R3/R4/R5) -------------------


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


def test_injected_provider_webhook_replaces_the_scripted_accept_end_to_end(_offline_env: Path) -> None:
    """A custom accept decision drives the webhook round-2 accept: re-register, send, COMPLETED."""
    propose_metadata = _propose_metadata(template_uri=TARGET_NEGOTIATION_PROPOSE_URI)
    session_id = propose_metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["id"]
    provider = _RecordingResponseProvider(
        NegotiationDecision(conclusion="accept", confirmed_intent="Custom confirmed target")
    )

    events, a2a_client, prompt_client, logs, _receiver = asyncio.run(
        _run_webhook_scenario(
            rounds=[_negotiation_round1_script(propose_metadata), _negotiation_round2_script()],
            env_path=_offline_env,
            negotiation_params=dict(_NEGOTIATION_PARAMS),  # Task-T refill; NO scripted answers
            negotiation_response_provider=provider,
        )
    )

    # The provider saw the propose view verbatim (kind dispatched on the propose's templateUri).
    assert len(provider.views) == 1
    view = provider.views[0]
    assert (view.kind, view.template_uri, view.prompt_text) == (
        NEGOTIATION_KIND_TARGET,
        TARGET_NEGOTIATION_PROPOSE_URI,
        "Please provide region: Coverage region of the energy-saving task.",
    )
    assert provider.sessions[0].id == session_id

    # Order evidence: round-1 send -> endpoint re-registration -> round-2 send.
    assert a2a_client.call_log == ["send:1", "register:1", "send:2"]
    # The typed accept carries the decision's confirmed intent (not the scenario text).
    assert len(prompt_client.accept_calls) == 1
    accept_data = prompt_client.accept_calls[0]
    assert isinstance(accept_data.content, TargetEndingContent)
    assert accept_data.content.confirmed_intent == "Custom confirmed target"
    assert _event_states(events)[-1] == "TASK_STATE_COMPLETED"
    summary_lines = [line for line in logs if line.startswith("[client] stream-completed:")]
    assert summary_lines == [
        "[client] stream-completed: events=6 artifacts=1 transport=webhook end=TASK_STATE_COMPLETED"
    ]


def test_reject_decision_webhook_logs_and_ends_after_round1(_offline_env: Path) -> None:
    """A reject decision over webhook: dedicated log, no re-registration, no round-2 send (R5)."""
    provider = _RecordingResponseProvider(NegotiationDecision(conclusion="reject", reason="policy"))

    events, a2a_client, prompt_client, logs, _receiver = asyncio.run(
        _run_webhook_scenario(
            rounds=[_negotiation_round1_script(_propose_metadata())],
            env_path=_offline_env,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_response_provider=provider,
        )
    )

    assert a2a_client.call_log == ["send:1"]
    assert a2a_client.register_calls == []
    assert prompt_client.accept_calls == []
    decision_logs = [line for line in logs if line.startswith("[client] negotiation-decision:")]
    assert decision_logs == [
        "[client] negotiation-decision: reject (not wired in this demo; see the integration guide)"
    ]
    assert _event_states(events) == ["TASK_STATE_SUBMITTED", "TASK_STATE_INPUT_REQUIRED"]
    summary_lines = [line for line in logs if line.startswith("[client] stream-completed:")]
    assert summary_lines == ["[client] stream-completed: events=2 artifacts=0 transport=webhook"]


# --- idle timeout backstop (R5) -------------------------------------------------------------------


def test_idle_timeout_ends_the_flow_gracefully_when_no_push_arrives(_offline_env: Path) -> None:
    """Push loss simulation: the server pushes nothing, the idle backstop ends the flow normally."""
    events, a2a_client, _prompt_client, logs, _receiver = asyncio.run(
        _run_webhook_scenario(rounds=[[]], env_path=_offline_env, idle_timeout=0.2)
    )

    assert a2a_client.push_status_codes == []  # nothing was pushed at all
    assert events == []
    assert len(a2a_client.send_calls) == 1
    timeout_logs = [line for line in logs if line.startswith("[client] webhook-idle-timeout:")]
    assert timeout_logs == ["[client] webhook-idle-timeout: no event for 0.2s"]
    summary_lines = [line for line in logs if line.startswith("[client] stream-completed:")]
    assert summary_lines == ["[client] stream-completed: events=0 artifacts=0 transport=webhook idle-timeout=0.2s"]


# --- terminal-state end signals: REJECTED ends the flow, not the idle backstop (final fix) -------


def test_round1_rejected_ends_the_flow_promptly_with_no_round2_send(_offline_env: Path) -> None:
    """Round-1 REJECTED (unmapped-code validation failure) is a terminal end — no idle hang, no round 2."""
    events, a2a_client, prompt_client, logs, _receiver = asyncio.run(
        _run_webhook_scenario(
            rounds=[
                [
                    _task_event(TaskState.TASK_STATE_SUBMITTED),
                    _status_update(state=TaskState.TASK_STATE_REJECTED, text="Prompt validation failed: unknown code"),
                ]
            ],
            env_path=_offline_env,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
        )
    )

    # No idle-timeout diagnostic: the terminal state ended the flow instead of the backstop.
    timeout_logs = [line for line in logs if line.startswith("[client] webhook-idle-timeout:")]
    assert timeout_logs == []
    assert _event_states(events) == ["TASK_STATE_SUBMITTED", "TASK_STATE_REJECTED"]
    # The answers were supplied, but a terminal REJECTED preempts the negotiation round trip.
    assert len(a2a_client.send_calls) == 1
    assert a2a_client.register_calls == []
    assert prompt_client.accept_calls == []
    summary_lines = [line for line in logs if line.startswith("[client] stream-completed:")]
    assert summary_lines == [
        "[client] stream-completed: events=2 artifacts=0 transport=webhook end=TASK_STATE_REJECTED"
    ]


def test_round2_rejected_after_accept_ends_the_flow_with_the_state_noted(_offline_env: Path) -> None:
    """Round-2 REJECTED (accept validation failure) ends the flow right away — the state is noted."""
    events, a2a_client, _prompt_client, logs, _receiver = asyncio.run(
        _run_webhook_scenario(
            rounds=[
                _negotiation_round1_script(_propose_metadata()),
                # The server pushes REJECTED instead of the normal progress/artifact/COMPLETED.
                [_status_update(state=TaskState.TASK_STATE_REJECTED, text="Prompt validation failed: region")],
            ],
            env_path=_offline_env,
            negotiation_params=dict(_NEGOTIATION_PARAMS),
            negotiation_answers=dict(_NEGOTIATION_ANSWERS),
        )
    )

    # The accept WAS sent (a genuine round-2 failure), then REJECTED ended the flow — no hang.
    assert a2a_client.call_log == ["send:1", "register:1", "send:2"]
    timeout_logs = [line for line in logs if line.startswith("[client] webhook-idle-timeout:")]
    assert timeout_logs == []
    assert _event_states(events) == ["TASK_STATE_SUBMITTED", "TASK_STATE_INPUT_REQUIRED", "TASK_STATE_REJECTED"]
    summary_lines = [line for line in logs if line.startswith("[client] stream-completed:")]
    assert summary_lines == [
        "[client] stream-completed: events=3 artifacts=0 transport=webhook end=TASK_STATE_REJECTED"
    ]


# --- normalize_webhook_payload: parity with normalize_event ---------------------------------------


def test_normalize_webhook_payload_matches_normalize_event_for_every_event_kind() -> None:
    """The webhook dict-side adapter produces the exact same shape as the SSE proto-side one."""
    propose_metadata = _propose_metadata()
    events = [
        _task_event(TaskState.TASK_STATE_SUBMITTED),
        _status_update(state=TaskState.TASK_STATE_INPUT_REQUIRED, text="need more", metadata=propose_metadata),
        _status_update(state=TaskState.TASK_STATE_WORKING, text="working"),
        _artifact_event(),
    ]
    for event in events:
        # The script events are already StreamResponse-shaped, so MessageToDict(event) is exactly
        # the webhook POST body (the server's MessageToDict(to_stream_response(event))).
        payload = MessageToDict(event)
        assert normalize_webhook_payload(payload) == normalize_event(event), f"parity broken for {next(iter(payload))}"


def test_normalize_webhook_payload_artifact_data_and_text_parts() -> None:
    report = StreamResponse(
        artifact_update=TaskArtifactUpdateEvent(
            task_id=_TASK_ID,
            context_id=_CONTEXT_ID,
            artifact=Artifact(artifact_id="a-1", name="report", metadata={TASK_T_EXTENSION_URI: "meta text"}),
        )
    )
    report.artifact_update.artifact.parts.add(text="report body")
    event = normalize_webhook_payload(MessageToDict(report))
    assert event["kind"] == "artifact"
    assert event["artifact_id"] == "a-1"
    assert event["name"] == "report"
    assert event["text"] == "report body"
    assert event["metadata"] == {TASK_T_EXTENSION_URI: "meta text"}


def test_normalize_webhook_payload_message_body() -> None:
    message = Message(role=Role.ROLE_AGENT)
    message.parts.add(text="plain agent message")
    event = normalize_webhook_payload(MessageToDict(StreamResponse(message=message)))
    assert event == {"kind": "message", "text": "plain agent message"}


def test_normalize_webhook_payload_rejects_unrecognized_body() -> None:
    with pytest.raises(ValueError, match="no recognized StreamResponse field set"):
        normalize_webhook_payload({"unknown": {"x": 1}})


def test_normalize_webhook_payload_keeps_metadata_so_int_coercion_absorbs_the_float_round() -> None:
    """``negotiationContext.round`` arrives as the JSON 1.0 float; the session parser absorbs it."""
    metadata = _propose_metadata()
    metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["round"] = 1.0  # the Struct/JSON float round-trip
    status = _status_update(state=TaskState.TASK_STATE_INPUT_REQUIRED, text="propose", metadata=metadata)
    payload = MessageToDict(status)
    event = normalize_webhook_payload(payload)
    session = _parse_negotiation_context(event["metadata"])
    assert session is not None
    assert session.round == 1  # int(1.0) — the float round-trip is absorbed as on the SSE path
    assert session.performative == NegotiationPerformative.PROPOSE


# --- server assembly: push machinery wired unconditionally (R1) -----------------------------------


def test_build_rest_app_request_handler_carries_the_push_store_and_sender(
    _offline_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1: no transport switch — the push store/sender are always in the DefaultRequestHandler."""
    # server_main installs the mock LLM at import time (reads ./.env), so import it with the cwd
    # pointed at the offline .env the autouse fixture wrote.
    monkeypatch.chdir(_offline_env.parent)
    from server.server_main import build_rest_app

    app = build_rest_app(agent_card=object(), executor=object())
    handler = app.state.request_handler
    assert isinstance(handler._push_config_store, InMemoryPushNotificationConfigStore)
    assert isinstance(handler._push_sender, BasePushNotificationSender)
    # The sender reads configs from the very store wired into the handler.
    assert handler._push_sender._config_store is handler._push_config_store

    # The sender's httpx client is managed by the app lifespan (closed on shutdown).
    async def scenario() -> None:
        push_client = handler._push_sender._client
        assert not push_client.is_closed
        async with app.router.lifespan_context(app):
            assert not push_client.is_closed
        assert push_client.is_closed

    asyncio.run(scenario())
