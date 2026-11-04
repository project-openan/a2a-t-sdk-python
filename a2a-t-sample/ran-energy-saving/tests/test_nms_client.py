"""Contract tests of the raw vendor-NMS client and its client-flow transport."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import httpx
import pytest
from a2a.client.client import ClientCallContext
from a2a.types import Role, SendMessageRequest
from protocol.nms_client import (
    NmsClient,
    NmsConfig,
    NmsConfigError,
    NmsNegotiationRequiredError,
    NmsProtocolError,
    NmsSseTransport,
    _frame_to_event,
)

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


def _request() -> tuple[SendMessageRequest, ClientCallContext]:
    request = SendMessageRequest()
    request.message.message_id = "m1"
    request.message.role = Role.ROLE_USER
    request.message.parts.add().text = "create ran energy saving task"
    return request, ClientCallContext(timeout=5.0, service_parameters={"A2A-Extensions": TASK_T_URI})


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
