"""Contract tests of the local NMS simulator."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from a2a.client.client import ClientCallContext
from a2a.types import Role, SendMessageRequest
from a2a_t.core.metadata import (
    NEGOTIATION_T_EXTENSION_URI,
    TASK_T_EXTENSION_URI,
)
from demo import ran_data
from demo.nms_sim.app import build_sim_app
from demo.nms_sim.config import FIDELITY_FAST, FIDELITY_STRICT, SimConfig
from demo.nms_sim.sse import task_stream
from demo.nms_sim.tasks import TASK_SUBMITTED, SimTask, TaskStore

TOKEN_URL = "/rest/plat/smapp/v1/sessions"
STREAM_URL = "/a2a/json/message:stream"
USER = "snbitestDefaultNorth"
VALUE = "Changeme_123"


@pytest.fixture(autouse=True)
def _reset_plans() -> None:
    """Keep plan ids deterministic across tests."""
    ran_data.reset()


def _config(fidelity: str = FIDELITY_FAST) -> SimConfig:
    return SimConfig(fidelity=fidelity)


async def _client(config: SimConfig | None = None) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_sim_app(config or _config()))
    async with httpx.AsyncClient(transport=transport, base_url="http://sim") as client:
        yield client


async def _token(client: httpx.AsyncClient) -> str:
    response = await client.put(
        TOKEN_URL,
        json={"grantType": "password", "userName": USER, "value": VALUE},
    )
    assert response.status_code == 200
    return response.json()["accessSession"]


def _prompt(
    *,
    region: str = "A2ATestRegion",
    window: str = "00:00~06:00",
    rate: str = "30 Mbps",
    percent: str | None = None,
) -> str:
    context = f"Energy saving time: {window}; guaranteed throughput: {rate}."
    if percent is not None:
        context += f" Energy consumption goal: {percent}."
    return "\n".join(
        [
            "## Operation Type",
            "",
            "Create",
            "",
            "## Task Object",
            "",
            f"The area is {region}.",
            "",
            "## Task Context",
            "",
            context,
        ]
    )


def _body(prompt: str | None, *, text: str = "create ran energy saving task") -> dict[str, object]:
    message: dict[str, object] = {
        "messageId": "m1",
        "role": "ROLE_USER",
        "parts": [{"text": text}],
    }
    if prompt is not None:
        message["metadata"] = {TASK_T_EXTENSION_URI: prompt}
    return {"message": message}


def _headers(
    token: str,
    *,
    extension: str | None = f"{TASK_T_EXTENSION_URI},{NEGOTIATION_T_EXTENSION_URI}",
) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if extension is not None:
        headers["A2A-Extensions"] = extension
    return headers


def _message_text(frame: dict[str, object]) -> str:
    """Return the full text of one negotiation message, preferring its metadata body."""
    message = frame["message"]
    assert isinstance(message, dict)
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        text = metadata.get(NEGOTIATION_T_EXTENSION_URI)
        if isinstance(text, str):
            return text
    return str(message["parts"][0]["text"])


async def _frames(
    client: httpx.AsyncClient,
    body: dict[str, object],
    headers: dict[str, str],
) -> list[dict[str, object]]:
    async with client.stream("POST", STREAM_URL, json=body, headers=headers) as response:
        assert response.status_code == 200, await response.aread()
        return [json.loads(line[5:]) async for line in response.aiter_lines() if line.startswith("data:")]


async def test_token_endpoint_issues_and_rejects_sessions() -> None:
    async for client in _client():
        assert await _token(client)

        rejected = await client.put(
            TOKEN_URL,
            json={"grantType": "password", "userName": USER, "value": "wrong"},
        )
        assert rejected.status_code == 401


async def test_a2a_t_stream_creates_a_task_and_reaches_completed() -> None:
    async for client in _client():
        token = await _token(client)
        frames = await _frames(client, _body(_prompt()), _headers(token))

        assert "task" in frames[0]
        assert frames[0]["task"]["status"]["state"] == TASK_SUBMITTED
        assert any(frame.get("artifactUpdate") for frame in frames)
        assert any(
            frame.get("statusUpdate", {}).get("status", {}).get("state") == "TASK_STATE_COMPLETED" for frame in frames
        )


async def test_extension_header_without_metadata_is_422() -> None:
    async for client in _client():
        token = await _token(client)
        response = await client.post(
            STREAM_URL,
            json=_body(None),
            headers=_headers(token),
        )

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == 422
        assert "-T extension key" in error["message"]


async def test_empty_extension_header_and_metadata_is_plain_a2a() -> None:
    async for client in _client():
        token = await _token(client)
        frames = await _frames(
            client,
            _body(
                None,
                text=("Implement energy-saving in the A2ATestRegion region, activation 00:00-06:00 at 30 Mbps."),
            ),
            _headers(token, extension=None),
        )

        assert "task" in frames[0]


async def test_missing_region_returns_an_information_negotiation_message() -> None:
    async for client in _client():
        token = await _token(client)
        frames = await _frames(
            client,
            _body("## Task Context\n\nEnergy saving time: 00:00~06:00; 30 Mbps."),
            _headers(token),
        )

        assert "message" in frames[0]
        text = _message_text(frames[0])
        assert "Information Negotiation" in text
        assert "Energy saving region" in text


async def test_unknown_region_is_rejected_with_the_available_test_region() -> None:
    async for client in _client():
        token = await _token(client)
        frames = await _frames(
            client,
            _body(_prompt(region="Songshanhu Administration Committee")),
            _headers(token),
        )

        text = _message_text(frames[0])
        assert "not served" in text
        assert "A2ATestRegion" in text


async def test_negotiation_metadata_requires_declaring_the_negotiation_extension() -> None:
    async for client in _client():
        token = await _token(client)

        unstructured = await _frames(
            client,
            _body(_prompt(region="Songshanhu Administration Committee")),
            _headers(token, extension=TASK_T_EXTENSION_URI),
        )
        message = unstructured[0]["message"]
        assert message["metadata"] == {}
        assert "not served" in message["parts"][0]["text"]

        structured = await _frames(
            client,
            _body(_prompt(region="Songshanhu Administration Committee")),
            _headers(token),
        )
        metadata = structured[0]["message"]["metadata"]
        assert NEGOTIATION_T_EXTENSION_URI in metadata
        assert metadata["negotiationContext"]["performative"] == "PROPOSE"


async def test_unavailable_window_is_rejected() -> None:
    async for client in _client():
        token = await _token(client)
        frames = await _frames(
            client,
            _body(_prompt(window="19:00~22:00")),
            _headers(token),
        )

        text = _message_text(frames[0])
        assert "not available" in text
        assert "00:00~06:00" in text


async def test_missing_time_segmented_throughput_uses_the_vendor_wording() -> None:
    async for client in _client():
        token = await _token(client)
        frames = await _frames(
            client,
            _body(_prompt(rate="")),
            _headers(token),
        )

        text = _message_text(frames[0])
        assert "missing explicit time-segmented rate goals" in text


async def test_rate_above_the_cell_maximum_is_rejected() -> None:
    async for client in _client():
        token = await _token(client)
        frames = await _frames(
            client,
            _body(_prompt(percent="50%")),
            _headers(token),
        )

        text = _message_text(frames[0])
        assert "35%" in text


async def test_task_endpoints_and_the_known_cancel_503() -> None:
    async for client in _client():
        token = await _token(client)
        frames = await _frames(client, _body(_prompt()), _headers(token))
        task_id = frames[0]["task"]["id"]

        task = await client.get(f"/a2a/json/tasks/{task_id}", headers={"X-Auth-Token": token})
        assert task.status_code == 200
        assert task.json()["task"]["id"] == task_id
        assert task.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"

        listing = await client.get("/a2a/json/tasks", headers={"X-Auth-Token": token})
        assert listing.status_code == 200
        assert listing.json()["tasks"][0]["id"] == task_id

        cancel = await client.post(f"/a2a/json/tasks/{task_id}:cancel", headers={"X-Auth-Token": token})
        assert cancel.status_code == 503


async def test_requests_without_a_token_are_401() -> None:
    async for client in _client():
        response = await client.post(STREAM_URL, json=_body(_prompt()))
        assert response.status_code == 401


def test_task_store_keeps_only_the_latest_tasks() -> None:
    store = TaskStore(limit=2)
    first = store.create(context_id="c1")
    second = store.create(context_id="c2")
    third = store.create(context_id="c3")

    assert store.get(first.task_id) is None
    assert [task["id"] for task in store.list()] == [third.task_id, second.task_id]


async def test_raw_nms_client_completes_against_the_simulator() -> None:
    from protocol.nms_client import NmsClient, NmsConfig, NmsSseTransport

    config = SimConfig(fidelity=FIDELITY_FAST)
    http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_sim_app(config)),
        base_url="http://sim",
    )
    client = NmsClient(
        NmsConfig(
            a2a_base="http://sim/a2a/json",
            token_url="http://sim/rest/plat/smapp/v1/sessions",
            token_user=USER,
            token_value=VALUE,
            idle_timeout=1.0,
            request_timeout=5.0,
            poll_interval=0.0,
        ),
        http_client=http_client,
    )
    request = SendMessageRequest()
    request.message.message_id = "m1"
    request.message.role = Role.ROLE_USER
    request.message.parts.add().text = "create ran energy saving task"
    request.message.metadata[TASK_T_EXTENSION_URI] = _prompt()
    transport = NmsSseTransport(client)
    context = ClientCallContext(
        timeout=5.0,
        service_parameters={"A2A-Extensions": f"{TASK_T_EXTENSION_URI},{NEGOTIATION_T_EXTENSION_URI}"},
    )

    events = await transport.send(request, context)

    assert events[0]["kind"] == "status"
    assert events[0]["state"] == TASK_SUBMITTED
    assert events[-1]["state"] == "TASK_STATE_COMPLETED"
    await client.aclose()
    await http_client.aclose()


async def test_strict_task_stream_emits_heartbeats_before_completion() -> None:
    config = SimConfig(
        fidelity=FIDELITY_STRICT,
        heartbeat_seconds=0.01,
        working_delay=0.02,
        completion_delay=0.0,
    )
    task = SimTask(task_id="t1", context_id="c1")
    chunks: list[str] = []
    async for chunk in task_stream(
        task,
        report={},
        artifact={"artifactId": "a1", "name": "report", "parts": [{"text": "ok"}]},
        config=config,
    ):
        chunks.append(chunk)
        if '"TASK_STATE_COMPLETED"' in chunk:
            break

    assert any(chunk.startswith(": heartbeat") for chunk in chunks)
    assert any('"TASK_STATE_COMPLETED"' in chunk for chunk in chunks)


async def test_pure_missing_target_asks_then_resumes_to_completed() -> None:
    async for client in _client():
        token = await _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        body = {
            "message": {
                "messageId": "m1",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "text": (
                            "Create a RAN energy saving task in the A2ATestRegion area. "
                            "Energy saving time is 00:00~06:00."
                        )
                    }
                ],
            }
        }
        frames = await _frames(client, body, headers)

        assert "task" in frames[0]
        assert any(
            frame.get("statusUpdate", {}).get("status", {}).get("state")
            == "TASK_STATE_INPUT_REQUIRED"
            for frame in frames
        )
        task_id = str(frames[0]["task"]["id"])

        follow_up = {
            "message": {
                "messageId": "m2",
                "taskId": task_id,
                "role": "ROLE_USER",
                "parts": [{"text": "{00:00~06:00,30Mbps}"}],
            }
        }
        resumed = await _frames(client, follow_up, headers)

        assert any(
            frame.get("statusUpdate", {}).get("status", {}).get("state")
            == "TASK_STATE_COMPLETED"
            for frame in resumed
        )
        assert any(frame.get("artifactUpdate") for frame in resumed)
