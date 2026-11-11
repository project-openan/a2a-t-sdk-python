"""Contract tests of the raw vendor-NMS client and its client-flow transport."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import httpx
import pytest
from a2a.client.client import ClientCallContext
from a2a.types import Role, SendMessageRequest
from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    TASK_T_EXTENSION_URI,
)
from protocol.nms_client import (
    NmsClient,
    NmsConfig,
    NmsConfigError,
    NmsNegotiationRequiredError,
    NmsProtocolError,
    NmsSseTransport,
    NmsWebhookTransport,
    _frame_to_event,
)
from shared.config import SampleConfig
from shared.run_constants import PROTOCOL_A2A_T, PROTOCOL_PURE
from shared.trace import trace_scope
from shared.trace_propagation import propagated_trace

TOKEN_URL = "https://nms.example:27417/rest/plat/smapp/v1/sessions"
A2A_BASE = "https://nms.example:31127/a2a/json"
TASK_T_URI = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1"


def _config(**overrides: object) -> NmsConfig:
    values: dict[str, object] = {
        "a2a_base": A2A_BASE,
        "token_url": TOKEN_URL,
        "token_user": "northbound",
        "token_value": "secret",
        "verify_tls": False,
        "idle_timeout": 0.5,
        "request_timeout": 5.0,
        "poll_interval": 0.0,
    }
    values.update(overrides)
    return NmsConfig(**values)  # ty: ignore[invalid-argument-type]


def _handler(*, frames: list[str], token_expires: int = 1800) -> httpx.MockTransport:
    """Return a mock NMS endpoint answering the token, stream and task routes."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and str(request.url).endswith("/sessions"):
            return httpx.Response(200, json={"accessSession": "token-1", "expires": token_expires})
        if request.method == "POST" and str(request.url).endswith("/message:stream"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="".join(f"{line}\n\n" for line in frames).encode(),
            )
        if request.method == "GET" and "/tasks/" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "task": {
                        "id": "task-1",
                        "status": {"state": "TASK_STATE_COMPLETED"},
                    }
                },
            )
        if request.method == "POST" and str(request.url).endswith(":cancel"):
            return httpx.Response(503, text="cancel unsupported")
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def _client(transport: httpx.MockTransport, **overrides: object) -> NmsClient:
    return NmsClient(
        _config(**overrides),
        http_client=httpx.AsyncClient(transport=transport),
    )


def test_config_requires_all_four_values() -> None:
    from shared.config import SampleConfig

    config = SampleConfig(
        model_mode="offline",
        language="en-US",
        host="127.0.0.1",
        port=8000,
        registry_url="http://127.0.0.1:5001",
        webhook_port=9000,
        webhook_token="token",
        webhook_idle_timeout=30,
        adk_model_provider="litellm",
        adk_model_name="model",
        adk_model_api_base="http://127.0.0.1:1/v1",
        adk_model_api_key="none",
        discovery="nms",
    )

    with pytest.raises(NmsConfigError, match="A2AT_SAMPLE_NMS_A2A_BASE"):
        NmsConfig.from_config(config)


async def test_token_is_cached_and_refreshed_after_the_deadline() -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"accessSession": f"token-{calls}", "expires": 1800})

    client = _client(httpx.MockTransport(handle))

    assert await client.token() == "token-1"
    assert await client.token() == "token-1"
    client._token_deadline = 0.0
    assert await client.token() == "token-2"
    await client.aclose()


async def test_stream_message_skips_heartbeats_and_yields_json_frames() -> None:
    seen_headers: dict[str, str] = {}
    body: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal body
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        seen_headers.update(request.headers)
        body = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b": heartbeat\n\n"
                b'data: {"task": {"id": "task-1", "status": {"state": "TASK_STATE_SUBMITTED"}}}\n\n'
                b": heartbeat\n\n"
                b'data: {"statusUpdate": {"taskId": "task-1", "status": {"state": "TASK_STATE_WORKING"}}}\n\n'
            ),
        )

    client = _client(httpx.MockTransport(handle))
    frames = [
        frame async for frame in client.stream_message({"message": {"messageId": "m1"}}, extension_header=TASK_T_URI)
    ]

    assert [frame.get("task", {}).get("id") for frame in frames] == ["task-1", None]
    assert seen_headers["authorization"] == "Bearer token-1"
    assert seen_headers["x-auth-token"] == "token-1"
    assert seen_headers["a2a-extensions"] == TASK_T_URI
    assert seen_headers["x-a2a-extensions"] == TASK_T_URI
    assert body["message"]["messageId"] == "m1"
    await client.aclose()


async def test_stream_message_ends_an_idle_stream_instead_of_hanging() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b": heartbeat\n\n",
        )

    client = _client(httpx.MockTransport(handle), idle_timeout=0.05)
    frames = [frame async for frame in client.stream_message({"message": {}})]

    assert frames == []
    await client.aclose()


async def test_get_task_unwraps_the_task_document_and_cancel_503_is_not_an_error() -> None:
    client = _client(_handler(frames=[]))

    task = await client.get_task("task-1")
    assert task["id"] == "task-1"
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert await client.cancel_task("task-1") is False
    await client.aclose()


async def test_a_non_200_stream_raises_a_protocol_error() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        return httpx.Response(422, json={"error": {"code": 422}})

    client = _client(httpx.MockTransport(handle))
    with pytest.raises(NmsProtocolError, match="422"):
        [frame async for frame in client.stream_message({"message": {}})]
    await client.aclose()


def test_a_status_update_with_the_vendor_wrapper_field_is_normalized() -> None:
    """The real NMS puts a top-level contextId next to statusUpdate on Modify pushes."""
    event = _frame_to_event(
        {
            "statusUpdate": {
                "taskId": "task-1",
                "contextId": "ctx-1",
                "status": {
                    "state": "TASK_STATE_SUBMITTED",
                    "message": {
                        "role": "ROLE_AGENT",
                        "parts": [{"text": "Task modification succeeded"}],
                    },
                },
            },
            "contextId": "ctx-1",
        }
    )

    assert event["kind"] == "status"
    assert event["state"] == "TASK_STATE_SUBMITTED"
    assert event["text"] == "Task modification succeeded"
    # The inspector shows the whole vendor frame, including the wrapper field ParseDict drops.
    assert event["wireBody"]["contextId"] == "ctx-1"
    assert event["wireBody"]["statusUpdate"]["taskId"] == "task-1"


def test_status_update_frame_is_normalized() -> None:
    event = _frame_to_event(
        {
            "statusUpdate": {
                "taskId": "task-1",
                "contextId": "ctx-1",
                "status": {"state": "TASK_STATE_WORKING"},
            }
        }
    )

    assert event["kind"] == "status"
    assert event["state"] == "TASK_STATE_WORKING"
    assert event["task_id"] == "task-1"


class _FakeStreamClient:
    """Minimal ``NmsClient`` surface used by the transport tests."""

    def __init__(
        self,
        frames: list[dict[str, object]],
        *,
        terminal: Mapping[str, object] | None = None,
    ) -> None:
        self.frames = frames
        self.terminal = terminal
        self.config = _config()
        self.payload: dict[str, object] | None = None
        self.extension_header = ""
        self.errors: list[tuple[str, str, object | None]] = []

    def record_error(self, kind: str, message: str, frame: object | None = None) -> None:
        self.errors.append((kind, message, frame))

    async def stream_message(
        self, payload: Mapping[str, object], *, extension_header: str = ""
    ) -> AsyncIterator[dict[str, object]]:
        self.payload = dict(payload)
        self.extension_header = extension_header
        for frame in self.frames:
            yield frame

    async def wait_for_terminal(self, task_id: str, *, timeout: float | None = None) -> dict[str, object]:
        assert self.terminal is not None
        return dict(self.terminal)

    async def cancel_task(self, task_id: str) -> bool:
        return False


class _FakeWebhookClient:
    """Minimal ``NmsClient`` surface used by the webhook transport tests."""

    def __init__(self, reply: Mapping[str, object]) -> None:
        self.reply = dict(reply)
        self.config = _config()
        self.payload: dict[str, object] | None = None
        self.extension_header = ""

    async def send_message(self, payload: Mapping[str, object], *, extension_header: str = "") -> dict[str, object]:
        self.payload = dict(payload)
        self.extension_header = extension_header
        return dict(self.reply)

    async def cancel_task(self, task_id: str) -> bool:
        return False


def _propose_message(negotiation_id: str, text: str) -> dict[str, object]:
    return {
        "message": {
            "messageId": f"m-{negotiation_id}",
            "contextId": "ctx-1",
            "role": "ROLE_AGENT",
            "parts": [{"text": text}],
            "metadata": {
                NEGOTIATION_T_EXTENSION_URI: text,
                NEGOTIATION_CONTEXT_METADATA_KEY: {
                    "id": negotiation_id,
                    "round": 1,
                    "maxRounds": 3,
                    "performative": "PROPOSE",
                },
            },
        }
    }


class _RecordingWebhookClient(_FakeWebhookClient):
    """A fake that also captures what the transport writes to the wire log."""

    def __init__(self, reply: Mapping[str, object]) -> None:
        super().__init__(reply)
        self.inbound: list[tuple[dict[str, object], str, str]] = []

    def record_inbound(
        self,
        frame: Mapping[str, object],
        *,
        source: str,
        dropped: str = "",
    ) -> None:
        self.inbound.append((dict(frame), source, dropped))


def _status_update(
    state: str,
    message_id: str,
    text: str = "",
    metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build one vendor push body (``statusUpdate`` carrying its status message)."""
    message: dict[str, object] = {
        "messageId": message_id,
        "role": "ROLE_AGENT",
        "parts": [{"text": text}],
    }
    if metadata:
        message["metadata"] = dict(metadata)
    return {
        "statusUpdate": {
            "taskId": "task-1",
            "contextId": "ctx-1",
            "status": {"state": state, "message": message},
        }
    }


def _task_reply(
    state: str,
    message_id: str,
    text: str = "",
    metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build one synchronous ``message:send`` reply carrying the same status shape."""
    push = _status_update(state, message_id, text, metadata)["statusUpdate"]
    assert isinstance(push, dict)
    return {"task": {"id": "task-1", "contextId": "ctx-1", "status": push["status"]}}


async def test_webhook_transport_does_not_replay_a_push_of_a_status_it_already_returned() -> None:
    """The NMS reports each status on both channels; the pushed twin must not re-open a round."""
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _RecordingWebhookClient(
        _task_reply("TASK_STATE_INPUT_REQUIRED", "m-question", "Not feasible, please confirm")
    )
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="push-token",
        protocol=PROTOCOL_PURE,
        idle_timeout=0.05,
    )

    first = await transport.send(*_request())

    assert [event.get("state") for event in first] == ["TASK_STATE_INPUT_REQUIRED"]

    # The webhook twin arrived while the first phase returned at INPUT_REQUIRED, so it is still
    # queued when the next send waits for progress — the incident this test pins.
    await queue.put(_status_update("TASK_STATE_INPUT_REQUIRED", "m-question", "Not feasible, please confirm"))
    client.reply = _task_reply("TASK_STATE_WORKING", "m-success", "Task modification succeeded")

    second = await transport.send(*_request())

    assert [event.get("state") for event in second] == ["TASK_STATE_WORKING"]
    # The duplicate is still recorded verbatim on the wire: evidence is never dropped with it.
    assert [(source, dropped) for _, source, dropped in client.inbound] == [("webhook", "duplicate")]
    assert client.inbound[0][0]["statusUpdate"]["status"]["message"]["messageId"] == "m-question"


async def test_webhook_transport_still_delivers_a_new_status_after_the_duplicate() -> None:
    """Deduplication keys on the peer message id, not on the state or the text."""
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _RecordingWebhookClient(_task_reply("TASK_STATE_WORKING", "m-working"))
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="push-token",
        protocol=PROTOCOL_PURE,
        idle_timeout=0.5,
    )
    await queue.put(_status_update("TASK_STATE_WORKING", "m-working"))
    await queue.put(_status_update("TASK_STATE_INPUT_REQUIRED", "m-question-2", "Please confirm"))

    events = await transport.send(*_request())

    assert [event.get("state") for event in events] == ["TASK_STATE_WORKING", "TASK_STATE_INPUT_REQUIRED"]
    assert events[-1]["message_id"] == "m-question-2"


async def test_webhook_transport_drops_a_replayed_negotiation_status() -> None:
    """A2A-T rides the same server and the same dual-channel delivery: the twin must not re-ask."""
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    negotiation = {
        NEGOTIATION_CONTEXT_METADATA_KEY: {
            "id": "neg-1",
            "round": 1,
            "maxRounds": 3,
            "performative": "PROPOSE",
        }
    }
    client = _RecordingWebhookClient(
        _task_reply("TASK_STATE_INPUT_REQUIRED", "m-propose", "Please lower the rate", negotiation)
    )
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="push-token",
        protocol=PROTOCOL_A2A_T,
        idle_timeout=0.05,
    )
    initial, context = _request()
    initial.message.metadata[TASK_T_EXTENSION_URI] = "task prompt"
    first = await transport.send(initial, context)

    assert [event.get("state") for event in first] == ["TASK_STATE_INPUT_REQUIRED"]

    # The accept phase would otherwise read the replayed PROPOSE as a new negotiation round.
    await queue.put(_status_update("TASK_STATE_INPUT_REQUIRED", "m-propose", "Please lower the rate", negotiation))
    client.reply = _task_reply("TASK_STATE_WORKING", "m-accepted", "Task modification succeeded")
    accept, context = _request()
    accept.message.metadata[NEGOTIATION_CONTEXT_METADATA_KEY] = negotiation[NEGOTIATION_CONTEXT_METADATA_KEY]
    events = await transport.send(accept, context)

    assert [event.get("state") for event in events] == ["TASK_STATE_WORKING"]
    assert [(source, dropped) for _, source, dropped in client.inbound] == [("webhook", "duplicate")]


async def test_webhook_transport_registers_the_push_url_and_returns_a_negotiation_reply() -> None:
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _FakeWebhookClient(_propose_message("neg-1", "initial proposal"))
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="push-token",
        idle_timeout=0.05,
    )
    request, context = _request()
    # The send that creates the Task carries the Task-T extension, not a negotiation reply.
    request.message.metadata[TASK_T_EXTENSION_URI] = "task prompt"

    events = await transport.send(request, context)

    assert events[0]["kind"] == "message"
    assert client.payload is not None
    configuration = client.payload["configuration"]
    assert isinstance(configuration, dict)
    assert configuration["taskPushNotificationConfig"]["url"] == "http://host:9000/webhook"
    assert configuration["taskPushNotificationConfig"]["token"] == "push-token"
    assert client.extension_header == TASK_T_URI


async def test_webhook_transport_does_not_reregister_push_on_a_task_continuation() -> None:
    """A Modify send continues the existing Task, whose webhook is already registered."""
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _FakeWebhookClient(_propose_message("neg-1", "modify proposal"))
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="push-token",
        idle_timeout=0.05,
    )
    request, context = _request()
    request.message.task_id = "task-1"
    request.message.metadata[NEGOTIATION_T_EXTENSION_URI] = "modify task prompt"

    await transport.send(request, context)

    assert client.payload is not None
    # The continuation registers nothing: no push config and no returnImmediately, so the whole
    # ``configuration`` object is absent from the wire.
    assert "configuration" not in client.payload


async def test_webhook_transport_asks_for_an_immediate_reply_on_a_task_creation() -> None:
    """Only the send that creates a Task needs the non-blocking reply."""
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _FakeWebhookClient(_propose_message("neg-1", "initial proposal"))
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="push-token",
        idle_timeout=0.05,
    )
    request, context = _request()

    await transport.send(request, context)

    assert client.payload is not None
    configuration = client.payload["configuration"]
    assert isinstance(configuration, dict)
    assert configuration["returnImmediately"] is True


async def test_webhook_transport_leaves_return_immediately_out_of_a_task_continuation() -> None:
    """The vendor rejects returnImmediately on a Modify; the Task already exists."""
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _FakeWebhookClient(_propose_message("neg-1", "modify proposal"))
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="push-token",
        idle_timeout=0.05,
    )
    request, context = _request()
    request.message.task_id = "task-1"

    await transport.send(request, context)

    assert client.payload is not None
    configuration = client.payload.get("configuration") or {}
    assert isinstance(configuration, dict)
    assert "returnImmediately" not in configuration


async def test_webhook_transport_leaves_return_immediately_out_of_a_negotiation_reply() -> None:
    """A pre-task negotiation reply names no Task, so there is nothing to return immediately."""
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _FakeWebhookClient(_propose_message("neg-1", "counter proposal"))
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="push-token",
        idle_timeout=0.05,
    )
    request, context = _request()
    request.message.metadata[NEGOTIATION_CONTEXT_METADATA_KEY] = {
        "id": "neg-1",
        "round": 2,
        "maxRounds": 3,
        "performative": "PROPOSE",
    }

    await transport.send(request, context)

    assert client.payload is not None
    configuration = client.payload.get("configuration") or {}
    assert isinstance(configuration, dict)
    assert "returnImmediately" not in configuration


async def test_webhook_transport_binds_the_reply_identity_for_the_router() -> None:
    """The vendor ignores the push token; task/context identity is the routing key."""
    reply = {
        "task": {
            "id": "task-1",
            "contextId": "ctx-1",
            "status": {"state": "TASK_STATE_WORKING"},
        }
    }
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    queue.put_nowait(
        {
            "statusUpdate": {
                "taskId": "task-1",
                "contextId": "ctx-1",
                "status": {"state": "TASK_STATE_CANCELED"},
            }
        }
    )
    bound: list[tuple[str | None, str | None]] = []
    transport = NmsWebhookTransport(
        _FakeWebhookClient(reply),  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="push-token",
        idle_timeout=0.5,
        bind=lambda task_id, context_id: bound.append((task_id, context_id)),
    )

    await transport.send(*_request())

    assert bound == [("task-1", "ctx-1")]


async def test_webhook_transport_stamps_and_binds_its_context_before_sending() -> None:
    """The vendor ignores the push token; a client context makes early pushes routable."""
    reply = {
        "task": {
            "id": "task-1",
            "contextId": "ctx-run",
            "status": {"state": "TASK_STATE_CANCELED"},
        }
    }
    client = _FakeWebhookClient(reply)
    bound: list[tuple[str | None, str | None]] = []
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=asyncio.Queue(),
        push_url="http://host:9000/webhook",
        push_token="push-token",
        idle_timeout=0.5,
        context_id="ctx-run",
        bind=lambda task_id, context_id: bound.append((task_id, context_id)),
    )

    await transport.send(*_request())

    assert client.payload is not None
    message = client.payload["message"]
    assert isinstance(message, dict)
    assert message["contextId"] == "ctx-run"
    assert bound[0] == (None, "ctx-run")


async def test_webhook_transport_binds_a_modify_target_task_before_sending() -> None:
    """A Modify send already names its Task, so the new run owns the pushes from the send on.

    Binding only after the reply leaves a window where the previous run is retired and this run
    owns just the context; a push that names the Task in that window used to be lost.
    """
    reply = {
        "task": {
            "id": "task-1",
            "contextId": "ctx-1",
            "status": {"state": "TASK_STATE_CANCELED"},
        }
    }
    bound: list[tuple[str | None, str | None]] = []
    transport = NmsWebhookTransport(
        _FakeWebhookClient(reply),  # ty: ignore[invalid-argument-type]
        queue=asyncio.Queue(),
        push_url="http://host:9000/webhook",
        push_token="push-token",
        idle_timeout=0.5,
        context_id="ctx-run",
        bind=lambda task_id, context_id: bound.append((task_id, context_id)),
    )
    request, context = _request()
    request.message.task_id = "task-1"
    request.message.context_id = "ctx-1"

    await transport.send(request, context)

    assert bound[0] == ("task-1", "ctx-1")


async def test_webhook_transport_hands_a_long_task_to_the_tracker_after_the_silence() -> None:
    """A tracked webhook run returns quickly instead of occupying the callback for the budget."""
    reply = {
        "task": {
            "id": "task-1",
            "contextId": "ctx-1",
            "status": {"state": "TASK_STATE_SUBMITTED"},
        }
    }
    client = _FakeWebhookClient(reply)
    client.config = _config(
        defer_to_tracker=True,
        poll_after_silence=0.05,
        idle_timeout=10.0,
    )
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=asyncio.Queue(),
        push_url="http://host:9000/webhook",
        push_token="push-token",
        idle_timeout=10.0,
    )

    started = time.monotonic()
    events = await asyncio.wait_for(transport.send(*_request()), timeout=2.0)

    assert events[-1]["state"] == "TASK_STATE_SUBMITTED"
    assert time.monotonic() - started < 1.5


async def test_webhook_transport_keeps_waiting_after_a_negotiation_response_reply() -> None:
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _FakeWebhookClient(_propose_message("neg-1", "accepted proposal"))
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="push-token",
        idle_timeout=1.0,
    )
    request, context = _request()
    request.message.metadata.update(
        {
            NEGOTIATION_CONTEXT_METADATA_KEY: {
                "id": "neg-1",
                "round": 1,
                "maxRounds": 3,
                "performative": "ACCEPT",
            }
        }
    )
    await queue.put(_propose_message("neg-2", "next proposal"))

    events = await transport.send(request, context)

    assert [event.get("metadata", {}).get(NEGOTIATION_CONTEXT_METADATA_KEY, {}).get("id") for event in events] == [
        "neg-1",
        "neg-2",
    ]


async def test_webhook_idle_backstop_starts_at_the_last_event() -> None:
    """One budget per phase: a push part-way through is consumed but does not restart the clock.

    A vendor that keeps pushing must not keep the phase (and the caller) alive forever.
    """
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _FakeWebhookClient({"task": {"id": "task-1", "status": {"state": "TASK_STATE_SUBMITTED"}}})
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="push-token",
        idle_timeout=0.6,
    )

    async def push_after_the_reply() -> None:
        await asyncio.sleep(0.3)
        await queue.put({"statusUpdate": {"taskId": "task-1", "status": {"state": "TASK_STATE_WORKING"}}})

    pusher = asyncio.create_task(push_after_the_reply())
    started = time.monotonic()
    events = await asyncio.wait_for(transport.send(*_request()), timeout=2.0)
    elapsed = time.monotonic() - started
    await pusher

    assert [event.get("state") for event in events] == ["TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"]
    # 0.6 s (the phase budget); restarting the clock on the 0.3 s push ends at 0.9 s.
    assert elapsed < 0.75


async def test_webhook_transport_returns_task_events_until_terminal() -> None:
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _FakeWebhookClient({"task": {"id": "task-1", "status": {"state": "TASK_STATE_SUBMITTED"}}})
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="push-token",
        idle_timeout=1.0,
    )
    request, context = _request()
    await queue.put({"statusUpdate": {"taskId": "task-1", "status": {"state": "TASK_STATE_WORKING"}}})
    await queue.put({"statusUpdate": {"taskId": "task-1", "status": {"state": "TASK_STATE_COMPLETED"}}})

    events = await transport.send(request, context)

    assert events[-1]["kind"] == "status"
    assert events[-1]["state"] == "TASK_STATE_COMPLETED"


def _request() -> tuple[SendMessageRequest, ClientCallContext]:
    request = SendMessageRequest()
    request.message.message_id = "m1"
    request.message.role = Role.ROLE_USER
    request.message.parts.add().text = "create ran energy saving task"
    return request, ClientCallContext(timeout=5.0, service_parameters={"A2A-Extensions": TASK_T_URI})


async def test_send_message_posts_to_message_send_and_returns_the_json_reply() -> None:
    captured: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"task": {"id": "task-1", "status": {"state": "TASK_STATE_SUBMITTED"}}},
        )

    client = _client(httpx.MockTransport(handle))

    reply = await client.send_message({"message": {"messageId": "m1"}})

    assert reply["task"]["id"] == "task-1"
    assert str(captured["url"]).endswith("/message:send")
    assert captured["body"] == {"message": {"messageId": "m1"}}
    await client.aclose()


async def test_a_stream_of_heartbeats_does_not_postpone_the_idle_window() -> None:
    """The NMS heartbeats every 15 s forever; counting those as progress hangs the run."""

    class _HeartbeatStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            while True:
                await asyncio.sleep(0.02)
                yield b": heartbeat\n\n"

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_HeartbeatStream(),
        )

    client = _client(httpx.MockTransport(handle), idle_timeout=0.1)

    frames = [frame async for frame in client.stream_message({"message": {}})]

    assert frames == []
    await client.aclose()


async def test_a_tracked_stream_hands_over_after_the_short_silence() -> None:
    """A run manager owns background tracking, so it need not wait the full HTTP budget."""

    class _HeartbeatStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'data: {"task": {"id": "task-1", "status": {"state": "TASK_STATE_SUBMITTED"}}}\n\n'
            while True:
                await asyncio.sleep(0.02)
                yield b": heartbeat\n\n"

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_HeartbeatStream(),
        )

    client = _client(
        httpx.MockTransport(handle),
        idle_timeout=10.0,
        poll_after_silence=0.05,
        defer_to_tracker=True,
    )
    started = time.monotonic()
    frames = [frame async for frame in client.stream_message({"message": {}})]

    assert len(frames) == 1
    assert time.monotonic() - started < 2.0
    await client.aclose()


def _negotiation_response_request() -> SendMessageRequest:
    request = SendMessageRequest()
    request.message.message_id = "m-accept"
    request.message.role = Role.ROLE_USER
    request.message.parts.add().text = "accept"
    request.message.metadata.update(
        {
            NEGOTIATION_CONTEXT_METADATA_KEY: {
                "id": "neg-1",
                "round": 1,
                "maxRounds": 3,
                "performative": "ACCEPT",
            }
        }
    )
    return request


async def test_transport_keeps_reading_a_negotiation_response_for_the_next_propose() -> None:
    def propose(negotiation_id: str, text: str) -> dict[str, object]:
        return {
            "message": {
                "messageId": f"m-{negotiation_id}",
                "contextId": "ctx-1",
                "role": "ROLE_AGENT",
                "parts": [{"text": text}],
                "metadata": {
                    NEGOTIATION_T_EXTENSION_URI: text,
                    NEGOTIATION_CONTEXT_METADATA_KEY: {
                        "id": negotiation_id,
                        "round": 1,
                        "maxRounds": 3,
                        "performative": "PROPOSE",
                    },
                },
            }
        }

    client = _FakeStreamClient(
        [
            propose("neg-1", "first proposal"),
            propose("neg-2", "second proposal after the accept"),
        ]
    )
    transport = NmsSseTransport(client)  # ty: ignore[invalid-argument-type]
    request, context = _request()
    request.message.metadata.update(
        {
            NEGOTIATION_CONTEXT_METADATA_KEY: {
                "id": "neg-1",
                "round": 1,
                "maxRounds": 3,
                "performative": "ACCEPT",
            }
        }
    )

    events = await transport.send(request, context)

    assert [event.get("text") for event in events] == [
        "first proposal",
        "second proposal after the accept",
    ]
    assert client.extension_header == TASK_T_URI


async def test_transport_still_returns_the_first_propose_of_an_initial_request() -> None:
    class _ProposeClient(_FakeStreamClient):
        pass

    client = _ProposeClient(
        [
            {
                "message": {
                    "messageId": "m-1",
                    "contextId": "ctx-1",
                    "role": "ROLE_AGENT",
                    "parts": [{"text": "initial proposal"}],
                    "metadata": {
                        NEGOTIATION_T_EXTENSION_URI: "initial proposal",
                        NEGOTIATION_CONTEXT_METADATA_KEY: {
                            "id": "neg-1",
                            "round": 1,
                            "maxRounds": 3,
                            "performative": "PROPOSE",
                        },
                    },
                }
            },
            {
                "message": {
                    "messageId": "m-2",
                    "contextId": "ctx-1",
                    "role": "ROLE_AGENT",
                    "parts": [{"text": "never read"}],
                    "metadata": {
                        NEGOTIATION_T_EXTENSION_URI: "never read",
                        NEGOTIATION_CONTEXT_METADATA_KEY: {
                            "id": "neg-2",
                            "round": 2,
                            "maxRounds": 3,
                            "performative": "PROPOSE",
                        },
                    },
                }
            },
        ]
    )
    transport = NmsSseTransport(client)  # ty: ignore[invalid-argument-type]
    request, context = _request()

    events = await transport.send(request, context)

    assert [event.get("text") for event in events] == ["initial proposal"]


async def test_transport_reports_a_message_without_a_task_as_negotiation_required() -> None:
    client = _FakeStreamClient(
        [
            {
                "message": {
                    "messageId": "m2",
                    "contextId": "ctx-1",
                    "role": "ROLE_AGENT",
                    "parts": [{"text": "## Information Negotiation\nEnergy saving region is missing."}],
                }
            }
        ]
    )
    transport = NmsSseTransport(client)  # ty: ignore[invalid-argument-type]
    request, context = _request()

    with pytest.raises(NmsNegotiationRequiredError) as excinfo:
        await transport.send(request, context)

    assert excinfo.value.context_id == "ctx-1"
    assert "Energy saving region is missing" in excinfo.value.text
    assert client.extension_header == TASK_T_URI


async def test_pure_sse_transport_returns_a_plain_agent_message() -> None:
    """A pure request gets a text answer, not a structured negotiation; do not fail the phase."""
    client = _FakeStreamClient(
        [
            {
                "message": {
                    "messageId": "m2",
                    "contextId": "ctx-1",
                    "role": "ROLE_AGENT",
                    "parts": [{"text": "## Information Negotiation\nEnergy saving region is missing."}],
                }
            }
        ]
    )
    transport = NmsSseTransport(client, protocol=PROTOCOL_PURE)  # ty: ignore[invalid-argument-type]
    request, context = _request()

    events = await transport.send(request, context)

    assert [event["kind"] for event in events] == ["message"]
    assert "Energy saving region is missing" in events[0]["text"]


async def test_transport_returns_a_structured_negotiation_message() -> None:
    client = _FakeStreamClient(
        [
            {
                "message": {
                    "messageId": "m2",
                    "contextId": "ctx-1",
                    "role": "ROLE_AGENT",
                    "parts": [{"text": "See metadata for details."}],
                    "metadata": {
                        NEGOTIATION_T_EXTENSION_URI: ("## Information Negotiation\nEnergy saving region is missing."),
                        NEGOTIATION_CONTEXT_METADATA_KEY: {
                            "id": "n1",
                            "round": 1,
                            "maxRounds": 5,
                            "performative": "PROPOSE",
                        },
                    },
                }
            }
        ]
    )
    transport = NmsSseTransport(client)  # ty: ignore[invalid-argument-type]
    request, context = _request()

    events = await transport.send(request, context)

    assert events[0]["kind"] == "message"
    metadata = events[0]["metadata"]
    assert isinstance(metadata, dict)
    assert metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["performative"] == "PROPOSE"
    assert client.errors == []


async def test_transport_polls_to_terminal_and_emits_a_status_event() -> None:
    client = _FakeStreamClient(
        [
            {
                "task": {
                    "id": "task-1",
                    "status": {"state": "TASK_STATE_SUBMITTED"},
                }
            }
        ],
        terminal={"id": "task-1", "status": {"state": "TASK_STATE_COMPLETED"}},
    )
    transport = NmsSseTransport(client)  # ty: ignore[invalid-argument-type]
    request, context = _request()

    events = await transport.send(request, context)

    assert events[0]["kind"] == "status"
    assert events[0]["state"] == "TASK_STATE_SUBMITTED"
    assert events[-1]["state"] == "TASK_STATE_COMPLETED"


def test_nms_agent_card_advertises_the_a2a_surface_and_the_extensions() -> None:
    from a2a_t.core.metadata import NEGOTIATION_T_EXTENSION_URI
    from protocol.discovery import agent_card_url, nms_agent_card

    card = nms_agent_card("https://nms.example:31127/a2a/json/")

    assert agent_card_url(card) == "https://nms.example:31127/a2a/json"
    assert card.capabilities.streaming is True
    assert {extension.uri for extension in card.capabilities.extensions} == {
        TASK_T_URI,
        NEGOTIATION_T_EXTENSION_URI,
    }


async def test_nms_discovery_returns_the_static_card_without_the_registry() -> None:
    from protocol.discovery import discover_agent

    discovered = await discover_agent(
        discovery="nms",
        organization="Huawei",
        name="RAN NMS Agent",
        nms_a2a_base="https://nms.example:31127/a2a/json",
    )

    assert discovered.url == "https://nms.example:31127/a2a/json"
    assert discovered.agent_card.name == "RAN Energy Saving Agent"


async def test_error_envelope_raises_and_is_written_to_wire_error(tmp_path: Path) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(b'data: {"error":{"code":422,"status":"INVALID_ARGUMENT","message":"no active negotiation"}}\n\n'),
        )

    client = _client(httpx.MockTransport(handle))
    wire_path = tmp_path / "run" / "wire.jsonl"
    client.set_wire_path(wire_path)

    with pytest.raises(NmsProtocolError, match="no active negotiation"):
        [frame async for frame in client.stream_message({"message": {}})]

    error_text = (tmp_path / "run" / "wire-error.jsonl").read_text(encoding="utf-8")
    assert "no active negotiation" in error_text
    assert "NmsProtocolError" in error_text
    await client.aclose()


async def test_a_successful_send_keeps_its_status_headers_and_body(tmp_path: Path) -> None:
    """A successful response may echo a trace id or request id in its headers."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
                "x-request-id": "req-1",
                "set-cookie": "session=secret",
            },
            json={"task": {"id": "task-1", "status": {"state": "TASK_STATE_SUBMITTED"}}},
        )

    client = _client(httpx.MockTransport(handle))
    wire_path = tmp_path / "run" / "wire.jsonl"
    client.set_wire_path(wire_path)

    await client.send_message({"message": {"messageId": "m1"}})

    records = [json.loads(line) for line in wire_path.read_text(encoding="utf-8").splitlines()]
    inbound = [record for record in records if record["dir"] == "in"][-1]
    assert inbound["status"] == 200
    assert inbound["elapsedMs"] >= 0
    assert inbound["headers"]["traceparent"] == "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    assert inbound["headers"]["x-request-id"] == "req-1"
    # Credentials never reach the wire log.
    assert inbound["headers"]["set-cookie"] == "***"
    assert inbound["body"]["task"]["id"] == "task-1"
    await client.aclose()


async def test_a_stream_open_keeps_the_success_headers(tmp_path: Path) -> None:
    """The stream body arrives as line records; the opening response carries the headers."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "x-trace-id": "trace-1"},
            content=b'data: {"task": {"id": "task-1"}}\n\n',
        )

    client = _client(httpx.MockTransport(handle))
    wire_path = tmp_path / "run" / "wire.jsonl"
    client.set_wire_path(wire_path)

    frames = [frame async for frame in client.stream_message({"message": {"messageId": "m1"}})]

    assert frames == [{"task": {"id": "task-1"}}]
    records = [json.loads(line) for line in wire_path.read_text(encoding="utf-8").splitlines()]
    opened = [record for record in records if record["dir"] == "in" and record.get("stream")]
    assert len(opened) == 1
    assert opened[0]["status"] == 200
    assert opened[0]["headers"]["x-trace-id"] == "trace-1"
    await client.aclose()


async def test_a_poll_success_header_block_is_recorded_once(tmp_path: Path) -> None:
    """A long task polls forever; the same header block must not repeat on every poll."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "x-request-id": "req-poll"},
            json={"task": {"id": "task-1", "status": {"state": "TASK_STATE_WORKING"}}},
        )

    client = _client(httpx.MockTransport(handle))
    wire_path = tmp_path / "run" / "wire.jsonl"
    client.set_wire_path(wire_path)

    await client.get_task("task-1")
    await client.get_task("task-1")

    records = [json.loads(line) for line in wire_path.read_text(encoding="utf-8").splitlines()]
    polls = [record for record in records if record["dir"] == "in" and record.get("headers")]
    assert len(polls) == 1
    assert polls[0]["headers"]["x-request-id"] == "req-poll"
    await client.aclose()


async def test_a_rate_limited_send_keeps_its_status_headers_and_retry_after(tmp_path: Path) -> None:
    """The vendor's 429 body is empty, so the status and headers are the only evidence."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        return httpx.Response(
            429,
            headers={
                "retry-after": "17",
                "x-hiro-apigw-err-desc": "Request is rejected by api quotaControl.",
                "x-request-id": "req-1",
                "x-internal-detail": "must not be logged",
            },
        )

    client = _client(httpx.MockTransport(handle))
    wire_path = tmp_path / "run" / "wire.jsonl"
    client.set_wire_path(wire_path)

    with pytest.raises(NmsProtocolError) as raised:
        await client.send_message({"message": {"messageId": "m1"}})

    assert raised.value.http_status == 429
    assert raised.value.retry_after == 17.0
    assert "<empty body>" in str(raised.value)

    records = [json.loads(line) for line in wire_path.read_text(encoding="utf-8").splitlines()]
    assert [record["dir"] for record in records] == ["out", "in"]
    failed = records[1]
    assert failed["status"] == 429
    assert failed["body"] is None
    assert failed["elapsedMs"] >= 0
    assert failed["headers"]["retry-after"] == "17"
    assert failed["headers"]["x-hiro-apigw-err-desc"] == "Request is rejected by api quotaControl."
    assert "x-internal-detail" not in failed["headers"]

    error_text = (tmp_path / "run" / "wire-error.jsonl").read_text(encoding="utf-8")
    assert '"status": 429' in error_text
    assert '"retry-after": "17"' in error_text
    await client.aclose()


async def test_a_rate_limited_task_poll_carries_its_http_status(tmp_path: Path) -> None:
    """The watcher needs the status (and Retry-After) to back off instead of re-polling."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        return httpx.Response(429, headers={"retry-after": "23"}, json={"retCode": "429"})

    client = _client(httpx.MockTransport(handle))
    wire_path = tmp_path / "run" / "wire.jsonl"
    client.set_wire_path(wire_path)

    with pytest.raises(NmsProtocolError) as raised:
        await client.get_task("task-1")

    assert raised.value.http_status == 429
    assert raised.value.retry_after == 23.0
    poll_record = json.loads(wire_path.read_text(encoding="utf-8"))
    assert poll_record["status"] == 429
    assert poll_record["body"] == '{"retCode":"429"}'
    await client.aclose()


async def test_a_failure_without_retry_after_carries_no_delay_hint() -> None:
    """Only a rate limit carries a retry hint; a plain 500 must not slow the poll schedule."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        return httpx.Response(500, text="internal")

    client = _client(httpx.MockTransport(handle))

    with pytest.raises(NmsProtocolError) as raised:
        await client.send_message({"message": {"messageId": "m1"}})

    assert raised.value.http_status == 500
    assert raised.value.retry_after is None
    await client.aclose()


async def test_wire_log_switch_disables_both_files(tmp_path: Path) -> None:
    client = _client(_handler(frames=[]), wire_log=False)
    wire_path = tmp_path / "run" / "wire.jsonl"
    client.set_wire_path(wire_path)

    client._write_wire("out", method="POST", body={"message": {"messageId": "m1"}})
    client.record_error("ParseError", "boom", {"frame": "x"})

    assert not wire_path.exists()
    assert not (tmp_path / "run" / "wire-error.jsonl").exists()
    await client.aclose()


async def test_wire_log_redacts_the_credentials(tmp_path: Path) -> None:
    client = _client(_handler(frames=[]))
    wire_path = tmp_path / "run" / "wire.jsonl"
    client.set_wire_path(wire_path)

    client._write_wire(
        "out",
        method="POST",
        headers={"Authorization": "Bearer token-1", "X-Auth-Token": "token-1"},
        body={"message": {"messageId": "m1"}},
    )

    text = wire_path.read_text(encoding="utf-8")
    assert "Bearer token-1" not in text
    assert "token-1" not in text
    assert "***" in text
    assert "m1" in text
    await client.aclose()


async def test_webhook_pure_returns_on_a_synchronous_input_required_reply() -> None:
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _FakeWebhookClient({"task": {"id": "task-1", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}})
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9001/webhook",
        push_token="push-token",
        protocol=PROTOCOL_PURE,
        idle_timeout=30.0,
    )

    events = await asyncio.wait_for(transport.send(*_request()), timeout=1.0)

    assert len(events) == 1
    assert events[0]["state"] == "TASK_STATE_INPUT_REQUIRED"


async def test_webhook_pure_returns_on_a_pushed_input_required_event() -> None:
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _FakeWebhookClient({"task": {"id": "task-1", "status": {"state": "TASK_STATE_SUBMITTED"}}})
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9001/webhook",
        push_token="push-token",
        protocol=PROTOCOL_PURE,
        idle_timeout=30.0,
    )
    await queue.put({"statusUpdate": {"taskId": "task-1", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}})

    events = await asyncio.wait_for(transport.send(*_request()), timeout=1.0)

    assert events[-1]["state"] == "TASK_STATE_INPUT_REQUIRED"


async def test_sse_pure_returns_on_input_required_without_polling() -> None:
    client = _FakeStreamClient(
        [
            {
                "statusUpdate": {
                    "taskId": "task-1",
                    "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
                }
            }
        ],
        terminal=None,
    )
    transport = NmsSseTransport(client, protocol=PROTOCOL_PURE)  # ty: ignore[invalid-argument-type]

    events = await asyncio.wait_for(transport.send(*_request()), timeout=1.0)

    assert events[-1]["state"] == "TASK_STATE_INPUT_REQUIRED"


async def test_cancel_posts_the_body_the_nms_requires() -> None:
    """An empty or ``taskId`` body is answered with 500 INTERNAL; only ``{"id": …}`` cancels."""
    seen: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        if request.method == "POST" and str(request.url).endswith(":cancel"):
            seen.update(json.loads(request.content))
            seen["url"] = str(request.url)
            return httpx.Response(200, json={"id": "task-1", "status": {"state": "TASK_STATE_CANCELED"}})
        return httpx.Response(404)

    client = _client(httpx.MockTransport(handle))

    assert await client.cancel_task("task-1") is True
    assert seen == {"id": "task-1", "url": f"{A2A_BASE}/tasks/task-1:cancel"}
    await client.aclose()


async def test_webhook_transport_registers_the_push_config_the_vendor_accepts() -> None:
    """The NMS answers 500 INTERNAL unless the inline config carries an id and authentication."""
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _FakeWebhookClient(_propose_message("neg-1", "initial proposal"))
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="https://141.5.146.234:9000/webhook",
        push_token="push-token",
        push_auth_credentials="north:secret",
        idle_timeout=0.05,
    )

    await transport.send(*_request())

    assert client.payload is not None
    config = client.payload["configuration"]["taskPushNotificationConfig"]  # ty: ignore[index]
    assert config["id"] and len(config["id"]) <= 36
    assert config["authentication"] == {"scheme": "Basic", "credentials": "north:secret"}
    # Basic credentials are the authentication channel; the SDK token would be noise.
    assert "token" not in config


async def test_traceparent_carries_the_run_id_as_the_w3c_trace_id() -> None:
    """The vendor can join its logs to one run: the run id is a valid 32-hex trace-id."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        seen.append(request.headers["traceparent"])
        return httpx.Response(200, json={"task": {"id": "task-1"}})

    client = _client(httpx.MockTransport(handle))
    run_id = "0123456789abcdef0123456789abcdef"
    with trace_scope(run_id=run_id):
        await client.send_message({"message": {"messageId": "m1"}})

    version, trace_id, span_id, flags = seen[0].split("-")
    assert (version, trace_id, flags) == ("00", run_id, "01")
    assert len(span_id) == 16 and all(char in "0123456789abcdef" for char in span_id)
    await client.aclose()


async def test_traceparent_prefers_the_stable_downstream_trace_id() -> None:
    """A blocked request and its retry carry the same vendor-facing trace id."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        seen.append(request.headers["traceparent"])
        return httpx.Response(200, json={"task": {"id": "task-1"}})

    client = _client(httpx.MockTransport(handle))
    run_id = "0123456789abcdef0123456789abcdef"
    stable_trace_id = "fedcba9876543210fedcba9876543210"
    with trace_scope(run_id=run_id), propagated_trace(run_id=run_id, downstream_trace_id=stable_trace_id):
        await client.send_message({"message": {"messageId": "m1"}})

    version, trace_id, span_id, flags = seen[0].split("-")
    assert (version, trace_id, flags) == ("00", stable_trace_id, "01")
    assert len(span_id) == 16 and all(char in "0123456789abcdef" for char in span_id)
    await client.aclose()


async def test_a_request_outside_a_traced_run_carries_no_traceparent() -> None:
    seen: dict[str, str] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"accessSession": "token-1", "expires": 1800})
        seen.update(request.headers)
        return httpx.Response(200, json={"task": {"id": "task-1"}})

    client = _client(httpx.MockTransport(handle))
    with trace_scope(run_id=""):
        await client.send_message({"message": {"messageId": "m1"}})

    assert "traceparent" not in seen
    await client.aclose()


async def test_wire_path_is_task_local_on_a_shared_client(tmp_path: Path) -> None:
    """Concurrent OSS requests share one NmsClient but write different wire files."""
    client = NmsClient(_config())

    async def select(name: str) -> str:
        client.set_wire_path(tmp_path / name / "wire.jsonl")
        await asyncio.sleep(0)
        path = client._current_wire_path()
        return "" if path is None else path.parent.name

    try:
        assert await asyncio.gather(select("run-a"), select("run-b")) == ["run-a", "run-b"]
    finally:
        await client.aclose()


async def test_an_inbound_push_is_recorded_in_the_wire_log(tmp_path: Path) -> None:
    """A webhook push never passes through message:send, so the wire must record it explicitly."""
    client = _client(_handler(frames=[]))
    wire_path = tmp_path / "run" / "wire.jsonl"
    client.set_wire_path(wire_path)

    client.record_inbound(
        {
            "statusUpdate": {
                "taskId": "task-1",
                "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
            }
        },
        source="webhook",
    )

    records = [json.loads(line) for line in wire_path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["dir"] == "in"
    assert records[-1]["source"] == "webhook"
    assert records[-1]["body"]["statusUpdate"]["taskId"] == "task-1"
    await client.aclose()


class _ConflictThenReplyClient:
    """Fail the first sends with TASK_STREAM_CONFLICT, then answer with a completed Task."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0
        self.config = _config()

    async def send_message(self, payload: Mapping[str, object], *, extension_header: str = "") -> dict[str, object]:
        self.calls += 1
        if self.calls <= self.failures:
            raise NmsProtocolError(
                'NMS A2A request returned HTTP 409: {"error":{"code":409,"status":"ABORTED",'
                '"message":"Task already has an active stream, close it before retrying",'
                '"details":[{"reason":"TASK_STREAM_CONFLICT"}]}}',
                http_status=409,
            )
        return {
            "task": {
                "id": "task-1",
                "contextId": "ctx-1",
                "status": {"state": "TASK_STATE_COMPLETED"},
            }
        }

    async def cancel_task(self, task_id: str) -> bool:
        return False


async def test_webhook_transport_retries_a_transient_stream_conflict() -> None:
    """TASK_STREAM_CONFLICT asks the client to retry; the webhook path owns no stream to close."""
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _ConflictThenReplyClient(failures=2)
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="push-token",
        idle_timeout=0.01,
        retry_delays=(0.0, 0.0),
    )
    request, context = _request()

    events = await transport.send(request, context)

    assert client.calls == 3
    assert events[0]["state"] == "TASK_STATE_COMPLETED"


async def test_webhook_transport_stops_after_the_retry_budget() -> None:
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _ConflictThenReplyClient(failures=3)
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="push-token",
        idle_timeout=0.01,
        retry_delays=(0.0,),
    )
    request, context = _request()

    with pytest.raises(NmsProtocolError):
        await transport.send(request, context)

    assert client.calls == 2


async def test_the_webhook_phase_has_no_deadline_while_the_caller_is_attached() -> None:
    """A live page keeps the task in the foreground: the settle window must not apply."""
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _FakeWebhookClient(
        {"task": {"id": "task-1", "contextId": "ctx-1", "status": {"state": "TASK_STATE_SUBMITTED"}}}
    )
    attached = asyncio.Event()
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="",
        protocol=PROTOCOL_PURE,
        idle_timeout=0.01,
        attached=attached,
    )
    request, context = _request()

    task = asyncio.create_task(transport.send(request, context))
    await asyncio.sleep(0.06)  # far past the settle window
    assert not task.done()

    await queue.put(
        {"statusUpdate": {"taskId": "task-1", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}}
    )
    events = await asyncio.wait_for(task, timeout=1.0)

    assert events[-1]["state"] == "TASK_STATE_INPUT_REQUIRED"


async def test_a_detached_caller_returns_the_phase_immediately() -> None:
    """A closed stream is the one signal that hands the task to the background pump."""
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _FakeWebhookClient(
        {"task": {"id": "task-1", "contextId": "ctx-1", "status": {"state": "TASK_STATE_SUBMITTED"}}}
    )
    attached = asyncio.Event()
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="",
        protocol=PROTOCOL_PURE,
        idle_timeout=30.0,
        attached=attached,
    )
    request, context = _request()

    task = asyncio.create_task(transport.send(request, context))
    await asyncio.sleep(0.01)
    attached.set()
    events = await asyncio.wait_for(task, timeout=1.0)

    assert len(events) == 1  # only the synchronous reply


async def test_the_transport_reports_the_vendor_identity_as_soon_as_it_is_known() -> None:
    """The outer↔downstream binding must be durable before a long wait."""
    bound: list[tuple[str, str]] = []

    async def on_task_bound(task_id: str, context_id: str) -> None:
        bound.append((task_id, context_id))

    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _FakeWebhookClient(
        {
            "task": {
                "id": "task-9",
                "contextId": "ctx-9",
                "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
            }
        }
    )
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="",
        protocol=PROTOCOL_PURE,
        on_task_bound=on_task_bound,
    )
    request, context = _request()

    await transport.send(request, context)

    assert bound == [("task-9", "ctx-9")]


async def test_webhook_transport_leaves_the_push_configuration_out_of_a_negotiation_reply() -> None:
    """A Negotiation-T reply creates no Task, so it must carry no push configuration."""
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _FakeWebhookClient(_propose_message("neg-1", "accept"))
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="push-token",
        idle_timeout=0.05,
    )
    request, context = _request()
    request.message.metadata[NEGOTIATION_CONTEXT_METADATA_KEY] = {
        "id": "neg-1",
        "round": 1,
        "maxRounds": 5,
        "performative": "ACCEPT",
    }

    await transport.send(request, context)

    assert client.payload is not None
    configuration = client.payload.get("configuration") or {}
    assert isinstance(configuration, dict)
    assert "taskPushNotificationConfig" not in configuration


async def test_webhook_transport_keeps_the_push_configuration_out_of_a_task_continuation() -> None:
    """A Modify continues an existing Task whose registration is still in force."""
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    client = _FakeWebhookClient(_propose_message("neg-1", "modify"))
    transport = NmsWebhookTransport(
        client,  # ty: ignore[invalid-argument-type]
        queue=queue,
        push_url="http://host:9000/webhook",
        push_token="push-token",
        idle_timeout=0.05,
    )
    request, context = _request()
    request.message.task_id = "task-1"

    await transport.send(request, context)

    assert client.payload is not None
    configuration = client.payload.get("configuration") or {}
    assert isinstance(configuration, dict)
    assert "taskPushNotificationConfig" not in configuration


def test_a_timeout_error_is_self_describing() -> None:
    """``str(ReadTimeout())`` is empty; the field UI must still show why the hop failed."""
    from protocol.nms_client import _http_error_detail

    assert _http_error_detail(httpx.ReadTimeout("")) == "ReadTimeout"
    assert (
        _http_error_detail(httpx.ConnectError("connection refused"))
        == "ConnectError: connection refused"
    )


def test_the_send_timeout_is_mapped_from_the_sample_config() -> None:
    """``message:send`` gets its own budget; GET/poll keeps the shorter request timeout."""
    config = SampleConfig(
        model_mode="offline",
        language="en-US",
        host="127.0.0.1",
        port=8000,
        registry_url="http://127.0.0.1:5001",
        webhook_port=9000,
        webhook_token="token",
        webhook_idle_timeout=30,
        adk_model_provider="litellm",
        adk_model_name="model",
        adk_model_api_base="http://127.0.0.1:1/v1",
        adk_model_api_key="none",
        discovery="nms",
        nms_a2a_base="https://nms.example/a2a/json",
        nms_token_url="https://nms.example/sessions",
        nms_token_user="user",
        nms_token_value="secret",
        nms_request_timeout=120.0,
        nms_send_timeout=300.0,
    )

    nms = NmsConfig.from_config(config)

    assert nms.request_timeout == 120.0
    assert nms.send_timeout == 300.0
