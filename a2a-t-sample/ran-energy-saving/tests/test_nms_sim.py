"""Contract tests of the local NMS simulator."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from a2a.client.client import ClientCallContext
from a2a.types import Role, SendMessageRequest
from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    TASK_T_EXTENSION_URI,
    TEMPLATE_URI_METADATA_KEY,
)
from demo import ran_data
from demo.nms_sim.app import build_sim_app
from demo.nms_sim.config import (
    CARRIER_MESSAGE,
    CARRIER_TASK,
    FIDELITY_FAST,
    FIDELITY_STRICT,
    TASK_SHAPE_TOPLEVEL,
    SimConfig,
)
from demo.nms_sim.sse import task_stream
from demo.nms_sim.tasks import TASK_SUBMITTED, SimTask, TaskStore
from starlette.applications import Starlette

TOKEN_URL = "/rest/plat/smapp/v1/sessions"
STREAM_URL = "/a2a/json/message:stream"
USER = "snbitestDefaultNorth"
VALUE = "Changeme_123"


@pytest.fixture(autouse=True)
def _reset_plans() -> None:
    """Keep plan ids deterministic across tests."""
    ran_data.reset()


def _config(fidelity: str = FIDELITY_FAST) -> SimConfig:
    """Default to the standalone-message carrier so the legacy SSE tests keep their shape."""
    return SimConfig(fidelity=fidelity, negotiation_carrier=CARRIER_MESSAGE)


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
            _body(_prompt(region="Region A")),
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
            _body(_prompt(region="Region A")),
            _headers(token, extension=TASK_T_EXTENSION_URI),
        )
        message = unstructured[0]["message"]
        assert message["metadata"] == {}
        assert "not served" in message["parts"][0]["text"]

        structured = await _frames(
            client,
            _body(_prompt(region="Region A")),
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


async def test_task_endpoints_and_an_idempotent_cancel_of_a_terminal_task() -> None:
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
        assert cancel.status_code == 200
        assert cancel.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


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

        # A plain-A2A question is a standalone Message (no Task), matching the real NMS: a Task
        # status would be answered by the OSS Agent itself instead of relayed to the caller.
        assert "message" in frames[0]
        assert "TASK_STATE_INPUT_REQUIRED" not in json.dumps(frames[0])
        context_id = str(frames[0]["message"]["contextId"])
        assert "Time-segmented guaranteed throughput target" in json.dumps(frames[0])

        follow_up = {
            "message": {
                "messageId": "m2",
                "contextId": context_id,
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


def test_sim_config_reads_the_hold_working_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Modify picker's local rehearsal switch is opt-in."""
    from demo.nms_sim.config import FIDELITY_ENV_KEY, HOLD_WORKING_ENV_KEY

    monkeypatch.setenv(FIDELITY_ENV_KEY, FIDELITY_FAST)
    monkeypatch.delenv(HOLD_WORKING_ENV_KEY, raising=False)
    assert SimConfig.from_env().hold_working is False

    monkeypatch.setenv(HOLD_WORKING_ENV_KEY, "1")
    assert SimConfig.from_env().hold_working is True


def test_sim_config_can_reproduce_the_unsupported_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    from demo.nms_sim.config import CANCEL_UNSUPPORTED_ENV_KEY, FIDELITY_ENV_KEY

    monkeypatch.setenv(FIDELITY_ENV_KEY, FIDELITY_FAST)
    monkeypatch.delenv(CANCEL_UNSUPPORTED_ENV_KEY, raising=False)
    assert SimConfig.from_env().cancel_supported is True

    monkeypatch.setenv(CANCEL_UNSUPPORTED_ENV_KEY, "1")
    assert SimConfig.from_env().cancel_supported is False


async def test_cancel_marks_the_task_canceled_and_removes_it_from_the_active_list() -> None:
    config = SimConfig(fidelity=FIDELITY_FAST, hold_working=True)
    async for client in _client(config):
        token = await _token(client)
        frames = await _frames(client, _body(_prompt()), _headers(token))
        task_id = frames[0]["task"]["id"]
        assert frames[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"

        cancel = await client.post(f"/a2a/json/tasks/{task_id}:cancel", headers={"X-Auth-Token": token})
        task = await client.get(f"/a2a/json/tasks/{task_id}", headers={"X-Auth-Token": token})

        assert cancel.status_code == 200
        assert task.json()["task"]["status"]["state"] == "TASK_STATE_CANCELED"


async def test_cancel_unsupported_still_answers_503() -> None:
    config = SimConfig(fidelity=FIDELITY_FAST, cancel_supported=False)
    async for client in _client(config):
        token = await _token(client)
        frames = await _frames(client, _body(_prompt()), _headers(token))
        task_id = frames[0]["task"]["id"]

        cancel = await client.post(f"/a2a/json/tasks/{task_id}:cancel", headers={"X-Auth-Token": token})

        assert cancel.status_code == 503


async def test_cancel_unknown_task_is_404() -> None:
    async for client in _client():
        token = await _token(client)
        cancel = await client.post("/a2a/json/tasks/missing:cancel", headers={"X-Auth-Token": token})
        assert cancel.status_code == 404


async def test_task_stream_stops_when_the_task_is_canceled() -> None:
    from demo.nms_sim.tasks import TASK_CANCELED

    task = SimTask(task_id="task-cancel", context_id="ctx-cancel")
    config = SimConfig(
        fidelity=FIDELITY_STRICT,
        heartbeat_seconds=0.0,
        working_delay=0.02,
        completion_delay=0.02,
    )
    stream = task_stream(
        task,
        report={"summary": "plan"},
        artifact={"artifactId": "a1"},
        config=config,
    )

    first = await anext(stream)
    task.state = TASK_CANCELED
    rest: list[str] = []
    try:
        async with asyncio.timeout(1.0):
            async for frame in stream:
                rest.append(frame)
    except TimeoutError:
        pytest.fail("the canceled Task stream kept emitting frames")

    assert "task" in json.loads(first.removeprefix("data: ").strip())
    states = [
        json.loads(frame.removeprefix("data: ").strip())["statusUpdate"]["status"]["state"]
        for frame in rest
        if frame.startswith("data: ")
    ]
    assert states[-1] == TASK_CANCELED
    assert "TASK_STATE_COMPLETED" not in states
    assert not any("artifactUpdate" in frame for frame in rest)


# ---------------------------------------------------------------------------
# message:send + webhook push
# ---------------------------------------------------------------------------

SEND_URL = "/a2a/json/message:send"


def _push_config(**overrides: object) -> dict[str, object]:
    """Return one vendor-shaped task push notification config."""
    config: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "url": "http://callback.test/webhook",
        "token": "push-token",
        "authentication": {"scheme": "Basic", "credentials": "user:pass"},
    }
    config.update(overrides)
    return config


def _send_body(
    prompt: str | None,
    *,
    task_id: str | None = None,
    push_config: dict[str, object] | None = None,
    extra_metadata: dict[str, object] | None = None,
    text: str = "create ran energy saving task",
) -> dict[str, object]:
    """Build one ``message:send`` body with an optional Task continuation and push config."""
    metadata: dict[str, object] = dict(extra_metadata or {})
    if prompt is not None:
        metadata[TASK_T_EXTENSION_URI] = prompt
    message: dict[str, object] = {
        "messageId": str(uuid.uuid4()),
        "role": "ROLE_USER",
        "parts": [{"text": text}],
        "metadata": metadata,
    }
    if task_id:
        message["taskId"] = task_id
    body: dict[str, object] = {"message": message}
    if push_config is not None:
        body["configuration"] = {"taskPushNotificationConfig": push_config}
    return body


def _push_app(config: SimConfig, pushes: list[dict[str, object]]) -> Starlette:
    """Build a simulator app whose pushes land in ``pushes`` instead of the network."""

    def handler(request: httpx.Request) -> httpx.Response:
        pushes.append(
            {
                "url": str(request.url),
                "authorization": request.headers.get("authorization", ""),
                "token": request.headers.get("x-a2a-notification-token", ""),
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(200, json={"received": "ok"})

    return build_sim_app(config, push_transport=httpx.MockTransport(handler))


async def _send(
    client: httpx.AsyncClient,
    body: dict[str, object],
    headers: dict[str, str],
) -> httpx.Response:
    return await client.post(SEND_URL, json=body, headers=headers)


async def _wait_for_pushes(app: Starlette) -> None:
    """Wait for the simulator's background pushes to finish."""
    await app.state.sim.pushes.wait_idle()


async def test_message_send_answers_with_the_task_and_pushes_later_states() -> None:
    pushes: list[dict[str, object]] = []
    config = SimConfig(fidelity=FIDELITY_FAST, negotiation_carrier=CARRIER_MESSAGE)
    app = _push_app(config, pushes)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sim") as client:
        token = await _token(client)
        response = await _send(client, _send_body(_prompt(), push_config=_push_config()), _headers(token))

        assert response.status_code == 200
        task = response.json()["task"]
        assert task["status"]["state"] == TASK_SUBMITTED
        task_id = task["id"]

        await _wait_for_pushes(app)

        kinds = [next(iter(push["body"])) for push in pushes]
        assert kinds == ["statusUpdate", "artifactUpdate", "statusUpdate"]
        assert pushes[0]["body"]["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"
        assert pushes[-1]["body"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
        metadata = pushes[0]["body"]["statusUpdate"]["metadata"]
        assert isinstance(metadata["timestamp"], int)
        for push in pushes:
            assert push["authorization"] == "Basic user:pass"
            assert push["token"] == "push-token"
            assert push["url"] == "http://callback.test/webhook"

        stored = await client.get(f"/a2a/json/tasks/{task_id}", headers={"X-Auth-Token": token})
        document = stored.json()["task"]
        assert document["status"]["state"] == "TASK_STATE_COMPLETED"
        assert isinstance(document["timestamp"], int)


async def test_a_second_registration_for_the_same_task_is_500() -> None:
    pushes: list[dict[str, object]] = []
    config = SimConfig(fidelity=FIDELITY_FAST, negotiation_carrier=CARRIER_MESSAGE)
    app = _push_app(config, pushes)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sim") as client:
        token = await _token(client)
        first = await _send(client, _send_body(_prompt(), push_config=_push_config()), _headers(token))
        task_id = first.json()["task"]["id"]
        await _wait_for_pushes(app)

        second = await _send(
            client,
            _send_body(_prompt(), task_id=task_id, push_config=_push_config()),
            _headers(token),
        )

        assert second.status_code == 500
        assert "config already exists" in second.json()["error"]["message"]


async def test_task_carried_negotiation_is_an_input_required_task() -> None:
    config = SimConfig(fidelity=FIDELITY_FAST, negotiation_carrier=CARRIER_TASK)
    async for client in _client(config):
        token = await _token(client)
        response = await _send(
            client,
            _send_body("## Task Context\n\nEnergy saving time: 00:00~06:00; 30 Mbps."),
            _headers(token),
        )

        assert response.status_code == 200
        task = response.json()["task"]
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        metadata = task["status"]["message"]["metadata"]
        assert "Information Negotiation" in metadata[NEGOTIATION_T_EXTENSION_URI]
        assert metadata[TEMPLATE_URI_METADATA_KEY] == "Negotiation-T/information-negotiation/propose/v1"
        context = metadata[NEGOTIATION_CONTEXT_METADATA_KEY]
        assert context["round"] == 1
        assert context["performative"] == "PROPOSE"


async def test_standalone_message_negotiation_returns_a_message() -> None:
    config = SimConfig(fidelity=FIDELITY_FAST, negotiation_carrier=CARRIER_MESSAGE)
    async for client in _client(config):
        token = await _token(client)
        response = await _send(client, _send_body(_prompt(region="")), _headers(token))

        assert response.status_code == 200
        message = response.json()["message"]
        assert NEGOTIATION_T_EXTENSION_URI in message["metadata"]
        assert message["metadata"][NEGOTIATION_CONTEXT_METADATA_KEY]["round"] == 1


async def test_a_foreign_negotiation_id_is_422() -> None:
    config = SimConfig(fidelity=FIDELITY_FAST, negotiation_carrier=CARRIER_MESSAGE)
    async for client in _client(config):
        token = await _token(client)
        context = {"id": str(uuid.uuid4()), "round": 1, "maxRounds": 5, "performative": "ACCEPT"}
        response = await _send(
            client,
            _send_body(
                None,
                extra_metadata={
                    NEGOTIATION_T_EXTENSION_URI: "Accept.",
                    NEGOTIATION_CONTEXT_METADATA_KEY: context,
                },
            ),
            _headers(token),
        )

        assert response.status_code == 422
        assert "does not match an active negotiation" in response.json()["error"]["message"]


async def test_rounds_increment_and_abort_at_the_budget() -> None:
    config = SimConfig(
        fidelity=FIDELITY_FAST,
        negotiation_carrier=CARRIER_MESSAGE,
        max_rounds=2,
    )
    async for client in _client(config):
        token = await _token(client)
        invalid = _prompt(region="")
        first = await _send(client, _send_body(invalid), _headers(token))
        context = first.json()["message"]["metadata"][NEGOTIATION_CONTEXT_METADATA_KEY]

        def reply(performative: str) -> dict[str, object]:
            return _send_body(
                None,
                extra_metadata={
                    NEGOTIATION_T_EXTENSION_URI: invalid,
                    NEGOTIATION_CONTEXT_METADATA_KEY: {**context, "performative": performative},
                },
            )

        second = await _send(client, reply("PROPOSE"), _headers(token))
        assert second.json()["message"]["metadata"][NEGOTIATION_CONTEXT_METADATA_KEY]["round"] == 2

        aborted = await _send(client, reply("PROPOSE"), _headers(token))

        assert aborted.status_code == 200
        metadata = aborted.json()["message"]["metadata"]
        assert metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["performative"] == "ABORT"
        assert metadata[NEGOTIATION_T_EXTENSION_URI].startswith("## Abort Negotiation")


@pytest.mark.parametrize(
    ("prompt", "kind"),
    [
        (_prompt(region=""), "information"),
        (_prompt(window="19:00~22:00"), "feasibility"),
        (_prompt(percent="50%"), "target"),
    ],
)
async def test_negotiation_kind_selection(prompt: str, kind: str) -> None:
    config = SimConfig(fidelity=FIDELITY_FAST, negotiation_carrier=CARRIER_MESSAGE)
    async for client in _client(config):
        token = await _token(client)
        response = await _send(client, _send_body(prompt), _headers(token))

        metadata = response.json()["message"]["metadata"]
        assert metadata[TEMPLATE_URI_METADATA_KEY] == f"Negotiation-T/{kind}-negotiation/propose/v1"
        assert metadata[NEGOTIATION_T_EXTENSION_URI].startswith(f"## {kind.capitalize()} Negotiation")


async def test_task_shape_toplevel_adds_state_and_timestamp() -> None:
    pushes: list[dict[str, object]] = []
    config = SimConfig(fidelity=FIDELITY_FAST, task_shape=TASK_SHAPE_TOPLEVEL)
    app = _push_app(config, pushes)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sim") as client:
        token = await _token(client)
        response = await _send(client, _send_body(_prompt()), _headers(token))
        task_id = response.json()["task"]["id"]
        await _wait_for_pushes(app)

        document = (
            await client.get(f"/a2a/json/tasks/{task_id}", headers={"X-Auth-Token": token})
        ).json()["task"]
        assert document["state"] == document["status"]["state"]
        assert isinstance(document["timestamp"], int)


async def test_hold_working_webhook_pushes_the_artifact_then_stays_working() -> None:
    pushes: list[dict[str, object]] = []
    config = SimConfig(
        fidelity=FIDELITY_FAST,
        negotiation_carrier=CARRIER_MESSAGE,
        hold_working=True,
    )
    app = _push_app(config, pushes)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sim") as client:
        token = await _token(client)
        response = await _send(client, _send_body(_prompt(), push_config=_push_config()), _headers(token))
        task_id = response.json()["task"]["id"]
        await _wait_for_pushes(app)

        kinds = [next(iter(push["body"])) for push in pushes]
        assert kinds == ["statusUpdate", "artifactUpdate", "statusUpdate"]
        assert pushes[-1]["body"]["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"
        stored = (
            await client.get(f"/a2a/json/tasks/{task_id}", headers={"X-Auth-Token": token})
        ).json()["task"]
        assert stored["status"]["state"] == "TASK_STATE_WORKING"


async def test_message_stream_task_carrier_closes_after_the_input_required_task() -> None:
    config = SimConfig(fidelity=FIDELITY_FAST, negotiation_carrier=CARRIER_TASK)
    async for client in _client(config):
        token = await _token(client)
        frames = await _frames(
            client,
            _send_body("## Task Context\n\nEnergy saving time: 00:00~06:00; 30 Mbps."),
            _headers(token),
        )

        assert frames[0]["task"]["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        metadata = frames[0]["task"]["status"]["message"]["metadata"]
        assert metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["round"] == 1
