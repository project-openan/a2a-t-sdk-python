"""End-to-end test of the pure A2A three-hop OSS->RAN->MIE chain."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import uvicorn
from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext, UnauthenticatedUser
from a2a.server.events import Event as A2AEvent
from a2a.server.events.event_queue import EventQueue
from a2a.types import Message, Role, SendMessageRequest, TaskState, TaskStatusUpdateEvent
from a2a_t.core.metadata import TASK_T_EXTENSION_URI
from demo.composition import build_demo_runtime
from demo.pure_policy import build_demo_pure_policy, pure_answer_from_event
from protocol.client_flow import ClientFlowResult
from protocol.intent import ProcessedIntent
from protocol.mie_agent import build_mie_app
from protocol.oss_agent import (
    OSSPureExecutor,
    _build_trace_emitter,
    a2a_request_text,
    build_oss_agent_card,
    build_oss_app,
)
from protocol.sqlite_task_store import SqliteTaskStore
from shared.config import DISCOVERY_DIRECT, DISCOVERY_NMS, TRANSPORT_SSE
from shared.run_constants import (
    FINAL_STATE_INCOMPLETE,
    FINAL_STATE_TASK_COMPLETED,
    FINAL_STATE_TASK_FAILED,
    LANGUAGE_EN,
    PROTOCOL_PURE,
)
from tests.integration_helpers import (
    free_tcp_port,
    offline_direct_config,
    running_app_on_port,
    running_server,
)


def test_a2a_request_text_prefers_the_plain_request() -> None:
    """The OSS->RAN hop is standard A2A: it must not forward the Task-T section layout."""
    intent = ProcessedIntent(
        raw_request="Start energy saving in Region A.",
        normalized_text="## Operation Type\nCreate\n\n## Task Object\nThe area is Region A.",
        plain_text="Create a RAN energy saving task in Region A.",
        operation_type="Create",
        region="Region A",
    )

    assert a2a_request_text(intent) == "Create a RAN energy saving task in Region A."
    assert "##" not in a2a_request_text(intent)


def test_a2a_request_text_falls_back_to_the_raw_request() -> None:
    """An old or passthrough intent has no plain renderer output; the raw text still works."""
    intent = ProcessedIntent(
        raw_request="Start energy saving in Region A.",
        normalized_text="## Operation Type\nCreate",
    )

    assert a2a_request_text(intent) == "Start energy saving in Region A."


async def _approve_next_request(mie_url: str) -> str:
    """Listen to the MIE SSE stream and approve the first pending plan."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        async with client.stream("GET", f"{mie_url}/approvals/events") as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                if event.get("type") != "approval_requested":
                    continue
                task_id = str(event["taskId"])
                decision = await client.post(f"{mie_url}/approvals/{task_id}", json={"approved": True})
                decision.raise_for_status()
                return task_id
    raise AssertionError("MIE received no approval request")


async def test_the_three_hop_chain_completes_and_tags_every_hop() -> None:
    mie_port = free_tcp_port()
    ran_port = free_tcp_port()
    oss_port = free_tcp_port()
    config = offline_direct_config(ran_port)

    with _running_mie(mie_port, timeout_seconds=10.0) as mie_url:
        with running_server(
            config,
            protocol_mode=PROTOCOL_PURE,
            pure_policy=build_demo_pure_policy(LANGUAGE_EN),
            mie_url=mie_url,
            approval_gate=True,
            approval_timeout_seconds=10.0,
        ):
            oss_app = build_oss_app(
                host="127.0.0.1",
                port=oss_port,
                ran_url=f"http://127.0.0.1:{ran_port}",
                answer=lambda event: pure_answer_from_event(event, LANGUAGE_EN),
            )
            with running_app_on_port(oss_app, oss_port, label="oss"):
                approver = asyncio.create_task(_approve_next_request(mie_url))
                client_config = replace(config, port=oss_port)
                runtime = await build_demo_runtime(
                    client_config,
                    "information",
                    "auto",
                    TRANSPORT_SSE,
                    DISCOVERY_DIRECT,
                    protocol=PROTOCOL_PURE,
                )
                try:
                    result = await runtime.run()
                finally:
                    await runtime.aclose()
                approval_task = await asyncio.wait_for(approver, timeout=15.0)

    assert approval_task
    assert result.final_state == FINAL_STATE_TASK_COMPLETED

    hops = {event["metadata"].get("hop") for event in result.events if isinstance(event.get("metadata"), dict)}
    assert "oss" in hops
    assert "ran" in hops or "ran->mie" in hops

    assert any(
        isinstance(event.get("metadata"), dict)
        and isinstance(event["metadata"].get("pure"), dict)
        and event["metadata"]["pure"].get("kind") == "approval"
        for event in result.events
    )

    for event in result.events:
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            continue
        assert TASK_T_EXTENSION_URI not in metadata
        assert "templateUri" not in metadata
        assert "negotiationContext" not in metadata


@contextmanager
def _running_mie(port: int, timeout_seconds: float) -> Generator[str, None, None]:
    """Serve one MIE app on a known port (the AgentCard must advertise it)."""
    app = build_mie_app(host="127.0.0.1", port=port, timeout_seconds=timeout_seconds)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started:
        if not thread.is_alive() or time.monotonic() >= deadline:
            raise RuntimeError("MIE server did not start")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


class _RecordingQueue(EventQueue):
    """Collect the events one OSS executor enqueues."""

    def __init__(self) -> None:
        self.events: list[object] = []

    async def enqueue_event(self, event: A2AEvent) -> None:
        self.events.append(event)


def _oss_context(text: str = "Start energy saving in Region A.") -> RequestContext:
    """Build one inbound OSS request."""
    message = Message(message_id="message-1", role=Role.ROLE_USER, context_id="ctx-oss")
    message.parts.add(text=text)
    return RequestContext(
        call_context=ServerCallContext(state={}, user=UnauthenticatedUser(), requested_extensions=set()),
        request=SendMessageRequest(message=message),
    )


def _fake_downstream(
    monkeypatch: pytest.MonkeyPatch,
    executor: OSSPureExecutor,
    final_state: str,
) -> None:
    """Replace the downstream hop with one fixed client-flow result."""

    async def run(
        user_request: str,
        run_id: str | None,
        event_sink: object,
        updater: object,
    ) -> ClientFlowResult:
        del user_request, run_id, event_sink, updater
        return ClientFlowResult(final_state=final_state, task_id="nms-task-1")

    monkeypatch.setattr(executor, "_run_ran", run)


async def test_oss_keeps_the_outer_task_working_when_the_downstream_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local wait-window expiry is not a vendor failure: the outer Task stays WORKING."""
    executor = OSSPureExecutor(answer=lambda event: "")
    _fake_downstream(monkeypatch, executor, FINAL_STATE_INCOMPLETE)
    queue = _RecordingQueue()

    await executor.execute(_oss_context(), queue)

    states = [event.status.state for event in queue.events if isinstance(event, TaskStatusUpdateEvent)]
    assert states[-1] == TaskState.TASK_STATE_WORKING
    assert TaskState.TASK_STATE_FAILED not in states
    last = queue.events[-1]
    assert isinstance(last, TaskStatusUpdateEvent)
    assert last.status.message is not None
    assert "still running" in last.status.message.parts[0].text
    assert last.metadata["pure"]["phase"] == "waiting"
    assert last.metadata["pure"]["downstreamTaskId"] == "nms-task-1"


async def test_oss_fails_the_outer_task_when_the_downstream_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real downstream failure still reaches the client as FAILED."""
    executor = OSSPureExecutor(answer=lambda event: "")
    _fake_downstream(monkeypatch, executor, FINAL_STATE_TASK_FAILED)
    queue = _RecordingQueue()

    await executor.execute(_oss_context(), queue)

    states = [event.status.state for event in queue.events if isinstance(event, TaskStatusUpdateEvent)]
    assert states[-1] == TaskState.TASK_STATE_FAILED


async def test_start_announces_the_nms_downstream(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``oss.log`` must say whether this process talks to the vendor NMS, not leave it implicit."""
    config = replace(
        offline_direct_config(8000),
        discovery=DISCOVERY_NMS,
        transport=TRANSPORT_SSE,
        nms_a2a_base="https://nms.example/a2a/json",
        nms_token_url="https://nms.example/rest/plat/smapp/v1/sessions",
        nms_token_user="user",
        nms_token_value="value",
    )
    executor = OSSPureExecutor(config=config, answer=lambda event: "")

    with caplog.at_level(logging.INFO, logger="ran_energy_saving.oss_agent"):
        await executor.start()
        await executor.stop()

    assert "OSS Agent downstream: NMS https://nms.example/a2a/json (transport=sse)" in caplog.text


async def test_start_announces_the_local_downstream(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A pure run whose OSS config is not NMS is the local topology; the log says so."""
    executor = OSSPureExecutor(
        config=offline_direct_config(8000),
        ran_url="http://127.0.0.1:8000",
        answer=lambda event: "",
    )

    with caplog.at_level(logging.INFO, logger="ran_energy_saving.oss_agent"):
        await executor.start()
        await executor.stop()

    assert "OSS Agent downstream: local RAN Agent (http://127.0.0.1:8000" in caplog.text


def test_trace_emitter_logs_the_ui_pairing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``oss.log`` names the UI instance that receives the spans, and whether the token is set."""
    monkeypatch.setenv("A2AT_SAMPLE_TRACE_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("A2AT_SAMPLE_TRACE_TOKEN", "token")
    monkeypatch.delenv("A2AT_SAMPLE_TRACE_DIR", raising=False)

    with caplog.at_level(logging.INFO, logger="ran_energy_saving.oss_agent"):
        emitter = _build_trace_emitter()
    assert emitter is not None
    emitter.close()

    assert "OSS trace emitter: http://127.0.0.1:8080 (token configured)" in caplog.text


def test_trace_emitter_warns_without_a_token(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without the shared token the UI rejects every batch; say so at startup."""
    monkeypatch.setenv("A2AT_SAMPLE_TRACE_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("A2AT_SAMPLE_TRACE_TOKEN", "")
    monkeypatch.delenv("A2AT_SAMPLE_TRACE_DIR", raising=False)

    with caplog.at_level(logging.WARNING, logger="ran_energy_saving.oss_agent"):
        emitter = _build_trace_emitter()
    assert emitter is not None
    emitter.close()

    assert "no A2AT_SAMPLE_TRACE_TOKEN" in caplog.text


def test_the_oss_card_advertises_push_notifications() -> None:
    """A long-running OSS Task needs the A2A push-notification capability."""
    card = build_oss_agent_card(host="127.0.0.1", port=8011)

    assert card.capabilities.push_notifications is True


def test_the_oss_app_uses_the_configured_task_and_push_stores(tmp_path: Path) -> None:
    """The launcher passes SQLite stores so a task that runs for days survives a restart."""

    class _PushSender:
        async def send_notification(self, task_id: str, event: object) -> None:
            del task_id, event

    store = SqliteTaskStore(tmp_path / "oss-tasks.sqlite3")
    sender = _PushSender()
    app = build_oss_app(
        answer=lambda event: "",
        task_store=store,
        push_sender=sender,
    )

    assert app.state.task_store is store
    assert app.state.push_sender is sender
    assert app.state.push_config_store is not None
