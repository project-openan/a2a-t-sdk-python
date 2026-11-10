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
    PURE_FAILURE_RETRY,
    PURE_QUESTION_CONFIRM,
    RATE_POLICY_OPTIONAL,
    TASK_SHAPE_TOPLEVEL,
    WINDOW_POLICY_ANY,
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


async def test_unknown_region_is_rejected_without_naming_a_candidate() -> None:
    async for client in _client():
        token = await _token(client)
        frames = await _frames(
            client,
            _body(_prompt(region="Region A")),
            _headers(token),
        )

        text = _message_text(frames[0])
        assert "not recognized" in text
        # The vendor's region check never names a candidate; a hint here would let the client
        # depend on something the real network never sends.
        assert "A2ATestRegion" not in text


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
        assert "not recognized" in message["parts"][0]["text"]

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


def _pure_message(text: str, *, context_id: str | None = None) -> dict[str, object]:
    """Build one plain-A2A user message (no extension metadata)."""
    message: dict[str, object] = {
        "messageId": str(uuid.uuid4()),
        "role": "ROLE_USER",
        "parts": [{"text": text}],
    }
    if context_id is not None:
        message["contextId"] = context_id
    return {"message": message}


def _pure_task_message(
    text: str, *, task_id: str, context_id: str
) -> dict[str, object]:
    """Build one plain-A2A message continuing an existing Task (a Modify)."""
    body = _pure_message(text, context_id=context_id)
    message = body["message"]
    assert isinstance(message, dict)
    message["taskId"] = task_id
    return body


async def test_pure_region_answer_in_the_vendor_phrasing_resolves_the_task() -> None:
    """The vendor asks for a region in plain text and accepts "The region is X." as the answer."""
    async for client in _client():
        token = await _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        frames = await _frames(
            client,
            _pure_message(
                "Create a RAN energy saving task. Energy saving time is 00:00~06:00 at 30 Mbps."
            ),
            headers,
        )
        assert "message" in frames[0]
        context_id = str(frames[0]["message"]["contextId"])
        assert "Energy saving region" in json.dumps(frames[0])

        resumed = await _frames(
            client,
            _pure_message("The region is A2ATestRegion.", context_id=context_id),
            headers,
        )

        assert any(frame.get("artifactUpdate") for frame in resumed)
        assert any(
            frame.get("statusUpdate", {}).get("status", {}).get("state")
            == "TASK_STATE_COMPLETED"
            for frame in resumed
        )


async def test_confirm_mode_asks_the_vendor_sentence_before_accepting() -> None:
    """The real NMS asks the same terse sentence even when the request already validates."""
    config = SimConfig(
        fidelity=FIDELITY_FAST,
        negotiation_carrier=CARRIER_MESSAGE,
        window_policy=WINDOW_POLICY_ANY,
        pure_question=PURE_QUESTION_CONFIRM,
    )
    async for client in _client(config):
        token = await _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        frames = await _frames(
            client,
            _pure_message(
                "Create a RAN energy saving task in A2ATestRegion for 00:00 to 22:00 at 10Mbps."
            ),
            headers,
        )

        assert frames[0]["message"]["parts"][0]["text"] == "Not feasible, please confirm"
        assert "task" not in frames[0]
        context_id = str(frames[0]["message"]["contextId"])

        resumed = await _frames(
            client,
            _pure_message("Confirmed. Proceed.", context_id=context_id),
            headers,
        )

        assert any(frame.get("artifactUpdate") for frame in resumed)


def test_confirm_mode_defaults_to_retry_because_it_may_need_several_rounds() -> None:
    config = SimConfig(pure_question=PURE_QUESTION_CONFIRM)

    assert config.pure_failure == PURE_FAILURE_RETRY


async def test_confirm_mode_converges_a_modify_over_several_answers() -> None:
    """A Modify starts a new exchange; the outage ladder 10→5→2 converges against it."""
    config = SimConfig(
        fidelity=FIDELITY_FAST,
        negotiation_carrier=CARRIER_MESSAGE,
        window_policy=WINDOW_POLICY_ANY,
        rate_policy=RATE_POLICY_OPTIONAL,
        long_outage_feasibility=True,
        hold_working=True,
        pure_question=PURE_QUESTION_CONFIRM,
    )
    async for client in _client(config):
        token = await _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        baseline = await _frames(
            client,
            _pure_message(
                "Create an energy saving task for A2ATestRegion: guarantee 2Mbps from 00:00 to "
                "07:00, 10Mbps from 07:00 to 18:00 and 20Mbps from 18:00 to 24:00."
            ),
            headers,
        )
        context_id = str(baseline[0]["message"]["contextId"])
        held = await _frames(
            client, _pure_message("Confirmed. Proceed.", context_id=context_id), headers
        )
        task_id = next(
            str(frame["statusUpdate"]["taskId"])
            for frame in held
            if "statusUpdate" in frame and frame["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"
        )

        modify_text = (
            "Due to a power outage from 08:00 to 18:00 in the A2ATestRegion region, modify "
            "their guaranteed throughput to 10 Mbps."
        )
        modify = await _frames(
            client,
            _pure_task_message(modify_text, task_id=task_id, context_id=context_id),
            headers,
        )
        assert modify[0]["message"]["parts"][0]["text"] == "Not feasible, please confirm"

        first = await _frames(
            client,
            _pure_task_message(
                "OK, lower ... to 5Mbps during the outage.",
                task_id=task_id,
                context_id=context_id,
            ),
            headers,
        )
        assert first[0]["message"]["parts"][0]["text"] == "Not feasible, please confirm"

        second = await _frames(
            client,
            _pure_task_message(
                "OK, lower ... to 2Mbps during the outage.",
                task_id=task_id,
                context_id=context_id,
            ),
            headers,
        )
        assert any(
            frame.get("statusUpdate", {}).get("status", {}).get("state")
            == "TASK_STATE_COMPLETED"
            for frame in second
        )


async def test_pure_region_must_match_the_served_name_exactly() -> None:
    """A suffix must not be accepted by substring: A2ATestRegion111 is not A2ATestRegion."""
    async for client in _client():
        token = await _token(client)
        frames = await _frames(
            client,
            _pure_message(
                "Create a RAN energy saving task in the A2ATestRegion111 area. "
                "Energy saving time is 00:00~06:00 at 30 Mbps."
            ),
            {"Authorization": f"Bearer {token}"},
        )

        assert "message" in frames[0]
        assert "not recognized" in json.dumps(frames[0])


async def test_pure_unservable_follow_up_completes_with_a_refusal() -> None:
    """The vendor signals a refusal as COMPLETED plus an error sentence and no artifact."""
    async for client in _client():
        token = await _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        frames = await _frames(
            client,
            _pure_message(
                "Create an energy saving task for TestRegion with "
                "Energy saving time 00:00~06:00 at 30 Mbps."
            ),
            headers,
        )
        assert "message" in frames[0]
        context_id = str(frames[0]["message"]["contextId"])

        resumed = await _frames(
            client,
            _pure_message("{00:00~06:00,30Mbps}", context_id=context_id),
            headers,
        )

        statuses = [
            frame["statusUpdate"]["status"]
            for frame in resumed
            if isinstance(frame.get("statusUpdate"), dict)
        ]
        assert statuses[-1]["state"] == "TASK_STATE_COMPLETED"
        assert 'The region name "TestRegion" is not recognized.' in statuses[-1]["message"]["parts"][0]["text"]
        assert not any(frame.get("artifactUpdate") for frame in resumed)


async def test_pure_failure_retry_asks_the_same_question_again() -> None:
    """``retry`` keeps the polite local dialogue instead of the vendor's COMPLETED refusal."""
    config = SimConfig(
        fidelity=FIDELITY_FAST,
        negotiation_carrier=CARRIER_MESSAGE,
        pure_failure=PURE_FAILURE_RETRY,
    )
    async for client in _client(config):
        token = await _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        frames = await _frames(
            client,
            _pure_message("Create an energy saving task for TestRegion with 00:00~06:00 at 30 Mbps."),
            headers,
        )
        context_id = str(frames[0]["message"]["contextId"])

        resumed = await _frames(
            client,
            _pure_message("{00:00~06:00,30Mbps}", context_id=context_id),
            headers,
        )

        assert "message" in resumed[0]
        assert "Energy saving region" in json.dumps(resumed[0])


async def test_window_policy_any_accepts_an_osp_activation_window() -> None:
    """``any`` accepts the windows the OSP cases use instead of asking for a feasibility change."""
    config = SimConfig(
        fidelity=FIDELITY_FAST,
        negotiation_carrier=CARRIER_MESSAGE,
        window_policy=WINDOW_POLICY_ANY,
    )
    async for client in _client(config):
        token = await _token(client)
        frames = await _frames(
            client,
            _pure_message(
                "Create an energy saving task in the A2ATestRegion area. "
                "Guaranteed throughput: 00:00-08:00 at 2Mbps, 08:00-24:00 at 10Mbps."
            ),
            {"Authorization": f"Bearer {token}"},
        )

        assert "task" in frames[0]
        assert any(frame.get("artifactUpdate") for frame in frames)


async def test_window_policy_any_treats_a_missing_activation_time_as_full_period() -> None:
    """The Task-T template makes the period optional; ``any`` accepts the full-period default."""
    config = SimConfig(
        fidelity=FIDELITY_FAST,
        negotiation_carrier=CARRIER_MESSAGE,
        window_policy=WINDOW_POLICY_ANY,
    )
    async for client in _client(config):
        token = await _token(client)
        frames = await _frames(
            client,
            _pure_message(
                "Create an energy saving task in the A2ATestRegion area. "
                "EARFCN 1600 can be shut down. Guaranteed throughput: 10Mbps."
            ),
            {"Authorization": f"Bearer {token}"},
        )

        assert "task" in frames[0]
        assert any(frame.get("artifactUpdate") for frame in frames)


async def test_rate_policy_optional_accepts_an_area_level_request_without_a_rate() -> None:
    """The OSP corpus has area-level Creates without a rate; the vendor default mode runs them."""
    config = SimConfig(
        fidelity=FIDELITY_FAST,
        negotiation_carrier=CARRIER_MESSAGE,
        window_policy=WINDOW_POLICY_ANY,
        rate_policy=RATE_POLICY_OPTIONAL,
    )
    async for client in _client(config):
        token = await _token(client)
        frames = await _frames(
            client,
            _pure_message(
                "Create an energy saving task in the A2ATestRegion area. "
                "The time range is from 00:00 to 22:00."
            ),
            {"Authorization": f"Bearer {token}"},
        )

        assert "task" in frames[0]
        assert any(frame.get("artifactUpdate") for frame in frames)


def test_sim_config_reads_the_hold_working_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Modify picker's local rehearsal switch is opt-in."""
    from demo.nms_sim.config import FIDELITY_ENV_KEY, HOLD_WORKING_ENV_KEY

    monkeypatch.setenv(FIDELITY_ENV_KEY, FIDELITY_FAST)
    monkeypatch.delenv(HOLD_WORKING_ENV_KEY, raising=False)
    assert SimConfig.from_env().hold_working is False

    monkeypatch.setenv(HOLD_WORKING_ENV_KEY, "1")
    assert SimConfig.from_env().hold_working is True


def test_sim_config_reads_the_pure_failure_and_window_policies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from demo.nms_sim.config import (
        FIDELITY_ENV_KEY,
        PURE_FAILURE_ENV_KEY,
        WINDOW_POLICY_ENV_KEY,
    )

    monkeypatch.setenv(FIDELITY_ENV_KEY, FIDELITY_FAST)
    monkeypatch.delenv(PURE_FAILURE_ENV_KEY, raising=False)
    monkeypatch.delenv(WINDOW_POLICY_ENV_KEY, raising=False)
    default = SimConfig.from_env()
    assert default.pure_failure == "completed"
    assert default.window_policy == "exact"

    monkeypatch.setenv(PURE_FAILURE_ENV_KEY, "retry")
    monkeypatch.setenv(WINDOW_POLICY_ENV_KEY, "any")
    configured = SimConfig.from_env()
    assert configured.pure_failure == "retry"
    assert configured.window_policy == "any"

    monkeypatch.setenv(PURE_FAILURE_ENV_KEY, "unknown")
    with pytest.raises(ValueError):
        SimConfig.from_env()


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


async def test_hold_working_keeps_a_pure_stream_task_working() -> None:
    """A plain-A2A create can be rehearsed as a running Task the picker may Modify."""
    config = SimConfig(fidelity=FIDELITY_FAST, window_policy=WINDOW_POLICY_ANY, hold_working=True)
    async for client in _client(config):
        token = await _token(client)
        frames = await _frames(
            client,
            {
                "message": {
                    "messageId": "m1",
                    "role": "ROLE_USER",
                    "parts": [
                        {
                            "text": (
                                "Implement energy-saving in A2ATestRegion region, and set the "
                                "guaranteed throughput targets as follows: 00:00-07:00→2Mbps."
                            )
                        }
                    ],
                }
            },
            _headers(token, extension=None),
        )

        assert frames[0]["task"]["status"]["state"] == "TASK_STATE_SUBMITTED"
        assert frames[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"


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


def _long_outage_issue(source: str) -> object:
    """Return the issue the simulator proposes for one request under the rehearsal profile."""
    from demo.nms_sim.config import RATE_POLICY_OPTIONAL, WINDOW_POLICY_ANY, SimConfig
    from demo.nms_sim.intent import extract_content, select_issue

    config = SimConfig(
        window_policy=WINDOW_POLICY_ANY,
        rate_policy=RATE_POLICY_OPTIONAL,
        long_outage_feasibility=True,
    )
    return select_issue(extract_content(source), config)


def test_long_outage_feasibility_requires_more_than_four_hours() -> None:
    """The vendor's rule is strict: an outage ends at 07:00 means exactly four hours."""
    from demo.nms_sim.negotiation import KIND_FEASIBILITY

    assert _long_outage_issue(
        "Modify the A2ATestRegion task. Power outage from 03:00 to 07:00. "
        "Guaranteed throughput 10Mbps."
    ) is None
    issue = _long_outage_issue(
        "Modify the A2ATestRegion task. Power outage from 08:00 to 18:00. "
        "Guaranteed throughput 10Mbps."
    )
    assert getattr(issue, "kind", None) == KIND_FEASIBILITY


def test_long_outage_feasibility_requires_more_than_two_mbps() -> None:
    """The other half of the rule: a long outage at 2 Mbps is acceptable."""
    from demo.nms_sim.negotiation import KIND_FEASIBILITY

    assert _long_outage_issue(
        "Modify the A2ATestRegion task. Power outage from 08:00 to 18:00. "
        "Guaranteed throughput 2Mbps."
    ) is None
    issue = _long_outage_issue(
        "Modify the A2ATestRegion task. Power outage from 08:00 to 18:00. "
        "Guaranteed throughput 3Mbps."
    )
    assert getattr(issue, "kind", None) == KIND_FEASIBILITY


def test_a_split_outage_is_measured_across_its_envelope() -> None:
    """The vendor reads the envelope, so two windows 08:00-10:00 and 15:00-18:00 span 10 hours."""
    from demo.nms_sim.negotiation import KIND_FEASIBILITY
    from shared import energy_rules

    assert energy_rules.outage_duration_hours(
        "A power outage from 08:00 to 10:00 and from 15:00 to 18:00."
    ) == 10.0
    issue = _long_outage_issue(
        "Modify the A2ATestRegion task. Power outage from 08:00 to 10:00 and from "
        "15:00 to 18:00. Guaranteed throughput 10Mbps."
    )
    assert getattr(issue, "kind", None) == KIND_FEASIBILITY


def test_the_osp_baseline_prose_names_the_region() -> None:
    """The tester's seeded Create names the region as "for <Region>:" and must parse."""
    from demo.nms_sim.intent import extract_content

    content = extract_content(
        "Create an energy saving task for A2ATestRegion: guarantee 2Mbps from 00:00 to 07:00, "
        "10Mbps from 07:00 to 18:00 and 20Mbps from 18:00 to 24:00."
    )

    assert content.region == "A2ATestRegion"
    assert content.windows == ("00:00~07:00", "07:00~18:00", "18:00~24:00")


def test_an_unknown_region_negotiation_names_no_candidate() -> None:
    """The vendor never names a candidate region; the simulator must not either."""
    from demo.nms_sim.config import SimConfig
    from demo.nms_sim.intent import extract_content, select_issue

    issue = select_issue(
        extract_content("The area is Region Z. Guaranteed throughput 10Mbps."),
        SimConfig(),
    )
    assert issue is not None
    assert "not recognized" in issue.detail
    assert "A2ATestRegion" not in issue.detail
