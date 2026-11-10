"""End-to-end test of the pure A2A three-hop OSS->RAN->MIE chain."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import uvicorn
from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext, UnauthenticatedUser
from a2a.server.events import Event as A2AEvent
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    Message,
    Role,
    SendMessageRequest,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a_t.core.metadata import TASK_T_EXTENSION_URI
from demo.composition import build_demo_runtime
from demo.pure_policy import build_demo_pure_policy, pure_answer_from_event
from google.protobuf.json_format import MessageToDict
from protocol.client_flow import ClientFlowResult
from protocol.downstream_binding_store import DownstreamBinding, DownstreamBindingStore
from protocol.intent import ProcessedIntent
from protocol.mie_agent import build_mie_app
from protocol.nms_client import NmsProtocolError
from protocol.oss_agent import (
    OSSPureExecutor,
    _build_trace_emitter,
    _PendingQuestion,
    _QuestionPendingError,
    _terminal_target_failure,
    a2a_request_text,
    build_oss_agent_card,
    build_oss_app,
)
from protocol.shared_webhook import WebhookRouter
from protocol.sqlite_task_store import SqliteTaskStore
from shared.config import (
    DISCOVERY_DIRECT,
    DISCOVERY_NMS,
    LONG_TASK_MODE_OFF,
    LONG_TASK_MODE_ON,
    TRANSPORT_SSE,
    TRANSPORT_WEBHOOK,
    SampleConfig,
)
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

    # The report event names the far-side Task: the client owns the OSS Task, never this one.
    reports = [
        event["metadata"]["pure"]
        for event in result.events
        if isinstance(event.get("metadata"), dict)
        and isinstance(event["metadata"].get("pure"), dict)
        and event["metadata"]["pure"].get("phase") == "report"
    ]
    assert reports, "the OSS Agent publishes a report event"
    assert reports[-1]["downstreamHop"] == "ran"
    assert reports[-1]["downstreamTaskId"]
    assert reports[-1]["downstreamContextId"]

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
        call_context: object,
    ) -> ClientFlowResult:
        del user_request, run_id, event_sink, updater, call_context
        return ClientFlowResult(final_state=final_state, task_id="nms-task-1")

    monkeypatch.setattr(executor, "_run_ran", run)


def _task_context(text: str, *, task_id: str = "outer-1") -> RequestContext:
    """Build one inbound request that continues an existing outer Task."""
    message = Message(message_id="message-2", role=Role.ROLE_USER, context_id="outer-ctx")
    message.parts.add(text=text)
    return RequestContext(
        call_context=ServerCallContext(state={}, user=UnauthenticatedUser(), requested_extensions=set()),
        request=SendMessageRequest(message=message),
        task_id=task_id,
        context_id="outer-ctx",
        task=Task(
            id=task_id,
            context_id="outer-ctx",
            status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
        ),
    )


async def test_a_plain_downstream_question_becomes_an_outer_input_required() -> None:
    """The OSS relays a vendor text question and remembers the downstream context."""
    executor = OSSPureExecutor(
        config=replace(offline_direct_config(8000), discovery=DISCOVERY_NMS),
        answer=lambda event: "Region A",
    )

    question = {
        "kind": "message",
        "message_id": "question-1",
        "context_id": "inner-ctx",
        "text": "## Information Negotiation\nPlease provide a real region.",
    }

    async def run_ran(*args: object, **kwargs: object) -> ClientFlowResult:
        del args, kwargs
        raise _QuestionPendingError(question)

    executor._run_ran = run_ran  # type: ignore[method-assign]
    queue = _RecordingQueue()
    message = Message(message_id="message-1", role=Role.ROLE_USER, context_id="ctx-oss")
    message.parts.add(text="Start energy saving in Region A.")
    context = RequestContext(
        call_context=ServerCallContext(state={}, user=UnauthenticatedUser(), requested_extensions=set()),
        request=SendMessageRequest(message=message),
        task_id="outer-1",
        context_id="outer-ctx",
    )

    await executor.execute(context, queue)

    states = [event.status.state for event in queue.events if isinstance(event, TaskStatusUpdateEvent)]
    assert TaskState.TASK_STATE_INPUT_REQUIRED in states
    status = next(
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent) and event.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    )
    pure = status.metadata["pure"]
    assert pure["kind"] == "negotiation"
    # A question without a downstream kind is a generic information request.
    assert pure["negotiationKind"] == "information"
    assert pure["recommendation"] == "Region A"
    assert pure["downstreamContextId"] == "inner-ctx"
    pending = executor._pending_questions["outer-1"]
    assert pending.inner_context_id == "inner-ctx"
    assert pending.run_id == ""


async def test_a_downstream_target_question_keeps_its_kind_on_the_outer_hop() -> None:
    """The outer phase describes the hop; the downstream kind must survive it."""
    executor = OSSPureExecutor(
        config=replace(offline_direct_config(8000), discovery=DISCOVERY_NMS),
        answer=lambda event: "80",
    )
    question = {
        "kind": "status",
        "context_id": "inner-ctx",
        "state": "TASK_STATE_INPUT_REQUIRED",
        "text": "Which target rate should the cell reach?",
        "metadata": {
            "pure": {"kind": "negotiation", "phase": "target", "round": 2, "maxRounds": 3}
        },
    }

    async def run_ran(*args: object, **kwargs: object) -> ClientFlowResult:
        del args, kwargs
        raise _QuestionPendingError(question)

    executor._run_ran = run_ran  # type: ignore[method-assign]
    queue = _RecordingQueue()
    message = Message(message_id="message-1", role=Role.ROLE_USER, context_id="ctx-oss")
    message.parts.add(text="Start energy saving in Region A.")
    context = RequestContext(
        call_context=ServerCallContext(state={}, user=UnauthenticatedUser(), requested_extensions=set()),
        request=SendMessageRequest(message=message),
        task_id="outer-1",
        context_id="outer-ctx",
    )

    await executor.execute(context, queue)

    status = next(
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent) and event.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    )
    pure = status.metadata["pure"]
    assert pure["phase"] == "question"
    assert pure["negotiationKind"] == "target"
    # The OSS numbers its own outer rounds; only the kind is forwarded from the downstream hop.
    assert pure["round"] == 1


def _status_texts(events: list[object]) -> list[str]:
    """Return the non-empty status texts one executor enqueued, in order."""
    texts: list[str] = []
    for event in events:
        if not isinstance(event, TaskStatusUpdateEvent):
            continue
        message = event.status.message
        if message is None or not message.parts:
            continue
        text = message.parts[0].text
        if isinstance(text, str) and text:
            texts.append(text)
    return texts


async def test_the_receive_status_does_not_announce_forwarding() -> None:
    """The opening status says what is true at that moment; the forward claim waits for the hop."""
    executor = OSSPureExecutor(
        config=replace(offline_direct_config(8000), discovery=DISCOVERY_NMS),
        answer=lambda event: "",
    )
    queue = _RecordingQueue()
    at_downstream_start: list[str] = []

    async def run_ran(*args: object, **kwargs: object) -> ClientFlowResult:
        del args, kwargs
        at_downstream_start.extend(_status_texts(queue.events))
        raise _QuestionPendingError(
            {"kind": "message", "context_id": "inner-ctx", "text": "Please provide the region."}
        )

    executor._run_ran = run_ran  # type: ignore[method-assign]
    await executor.execute(
        _new_oss_context("Create an energy saving task for TestRegion.", task_id="outer-1"),
        queue,
    )

    assert at_downstream_start[0] == "OSS Agent received the request and is processing it."
    assert at_downstream_start[-1] == "OSS Agent is forwarding the request to the NMS."
    intent_status = next(
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent)
        and event.status.message is not None
        and event.status.message.parts
        and event.status.message.parts[0].text.startswith("Business intent ready")
    )
    intent_metadata = MessageToDict(intent_status.status.message.metadata)
    assert "intentProcessed" in intent_metadata
    assert intent_metadata["pure"]["phase"] == "intent"
    assert intent_metadata["pure"]["downstream"] == "nms"


async def test_a_recommendation_repeating_the_sent_value_is_suppressed() -> None:
    """A recommendation equal to what was just sent cannot answer the vendor's question."""

    async def recommend(event: dict[str, object]) -> dict[str, object]:
        del event
        return {
            "action": "accept",
            "rationale": "grounded",
            "text": "The region is TestRegion.",
            "filledParams": {"region": "TestRegion"},
        }

    executor = OSSPureExecutor(
        config=replace(offline_direct_config(8000), discovery=DISCOVERY_NMS),
        answer=lambda event: "",
        recommendation=recommend,
    )
    queue = _RecordingQueue()

    async def run_ran(*args: object, **kwargs: object) -> ClientFlowResult:
        del args, kwargs
        raise _QuestionPendingError({"kind": "message", "context_id": "inner-ctx", "text": "Region not recognized."})

    executor._run_ran = run_ran  # type: ignore[method-assign]
    await executor.execute(
        _new_oss_context("Create an energy saving task for TestRegion.", task_id="outer-1"),
        queue,
    )

    status = next(
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent) and event.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    )
    pure = status.metadata["pure"]
    assert pure["recommendation"] is None
    assert pure["recommendationSuppressed"] == "already_sent"


async def test_a_recommendation_with_a_new_value_is_kept() -> None:
    """Suppression is about repeating the rejected value, not about having any recommendation."""

    async def recommend(event: dict[str, object]) -> dict[str, object]:
        del event
        return {
            "action": "accept",
            "rationale": "grounded in the answer",
            "filledParams": {"region": "A2ATestRegion"},
        }

    executor = OSSPureExecutor(
        config=replace(offline_direct_config(8000), discovery=DISCOVERY_NMS),
        answer=lambda event: "",
        recommendation=recommend,
    )
    queue = _RecordingQueue()

    async def run_ran(*args: object, **kwargs: object) -> ClientFlowResult:
        del args, kwargs
        raise _QuestionPendingError({"kind": "message", "context_id": "inner-ctx", "text": "Region not recognized."})

    executor._run_ran = run_ran  # type: ignore[method-assign]
    await executor.execute(
        _new_oss_context("Create an energy saving task for TestRegion.", task_id="outer-1"),
        queue,
    )

    status = next(
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent) and event.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    )
    assert status.metadata["pure"]["recommendation"]["filledParams"] == {"region": "A2ATestRegion"}


class _ContextRequestProvider:
    """Test double mirroring the ADK provider's per-task request storage."""

    def __init__(self) -> None:
        self._request: ContextVar[str | None] = ContextVar("test_oss_user_request", default=None)

    def set_user_request(self, value: str | None) -> None:
        """Store the request of the current asyncio task."""
        self._request.set(value)

    def current(self) -> str | None:
        """Return the request visible to the current asyncio task."""
        return self._request.get()


def _new_oss_context(text: str, *, task_id: str) -> RequestContext:
    """Build one request that creates a new outer Task with a known id."""
    message = Message(message_id="message-1", role=Role.ROLE_USER, context_id="outer-ctx")
    message.parts.add(text=text)
    return RequestContext(
        call_context=ServerCallContext(state={}, user=UnauthenticatedUser(), requested_extensions=set()),
        request=SendMessageRequest(message=message),
        task_id=task_id,
        context_id="outer-ctx",
    )


async def _ask_a_first_question(executor: OSSPureExecutor, user_request: str) -> None:
    """Run the initial request until the downstream asks its first text question."""

    async def run_ran(request_text: str, *args: object, **kwargs: object) -> ClientFlowResult:
        del args, kwargs
        setter = getattr(executor._decision_provider, "set_user_request", None)
        if callable(setter):
            setter(request_text)
        raise _QuestionPendingError(
            {"kind": "message", "context_id": "inner-ctx", "text": "Please provide the region."}
        )

    executor._run_ran = run_ran  # type: ignore[method-assign]
    await asyncio.create_task(executor.execute(_new_oss_context(user_request, task_id="outer-1"), _RecordingQueue()))


async def test_the_continuation_recommendation_sees_the_original_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator answer runs in a new request task; it must still carry the original request."""
    provider = _ContextRequestProvider()
    seen: list[str | None] = []

    async def recommendation(event: dict[str, object]) -> dict[str, object] | None:
        del event
        seen.append(provider.current())
        return {"action": "accept", "rationale": "grounded", "text": "Region A"}

    executor = OSSPureExecutor(
        config=replace(offline_direct_config(8000), discovery=DISCOVERY_NMS),
        answer=lambda event: "",
        recommendation=recommendation,
        decision_provider=provider,
    )
    await _ask_a_first_question(executor, "Create a task for Region A.")

    async def fake_continuation(
        pending: _PendingQuestion,
        answer_text: str,
        event_sink: object,
        updater: TaskUpdater,
        call_context: object,
    ) -> ClientFlowResult:
        del pending, answer_text, event_sink, updater, call_context
        raise _QuestionPendingError({"kind": "message", "context_id": "inner-ctx", "text": "Which region?"})

    monkeypatch.setattr(executor, "_run_question_continuation", fake_continuation)
    await asyncio.create_task(executor.execute(_task_context("Region A"), _RecordingQueue()))

    assert seen[-1] == "Create a task for Region A."


async def test_the_continuation_answer_sees_the_original_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A structured question after the operator answer needs the request just as much."""
    provider = _ContextRequestProvider()
    seen: list[str | None] = []

    def answer(event: dict[str, object]) -> str:
        del event
        seen.append(provider.current())
        return "Region A"

    executor = OSSPureExecutor(answer=answer, decision_provider=provider)
    await _ask_a_first_question(executor, "Create a task for Region A.")

    async def fake_continuation(
        pending: _PendingQuestion,
        answer_text: str,
        event_sink: object,
        updater: TaskUpdater,
        call_context: object,
    ) -> ClientFlowResult:
        del pending, answer_text, event_sink, updater, call_context
        await executor._answer_or_relay(
            {"kind": "status", "state": "TASK_STATE_INPUT_REQUIRED", "text": "Which region?"}
        )
        return ClientFlowResult(final_state=FINAL_STATE_TASK_COMPLETED, context_id="inner-ctx")

    monkeypatch.setattr(executor, "_run_question_continuation", fake_continuation)
    await asyncio.create_task(executor.execute(_task_context("Region A"), _RecordingQueue()))

    assert seen[-1] == "Create a task for Region A."


async def test_the_operator_answer_resumes_the_downstream_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The follow-up text is forwarded to the downstream and the outer Task completes."""
    executor = OSSPureExecutor(answer=lambda event: "")
    captured: dict[str, str] = {}

    async def fake_continuation(
        pending: _PendingQuestion,
        answer_text: str,
        event_sink: object,
        updater: TaskUpdater,
        call_context: object,
    ) -> ClientFlowResult:
        del pending, event_sink, updater, call_context
        captured["text"] = answer_text
        return ClientFlowResult(final_state=FINAL_STATE_TASK_COMPLETED, context_id="inner-ctx")

    monkeypatch.setattr(executor, "_run_question_continuation", fake_continuation)
    executor._pending_questions["outer-1"] = _PendingQuestion(
        outer_task_id="outer-1",
        outer_context_id="outer-ctx",
        run_id="run-1",
        inner_task_id="",
        inner_context_id="inner-ctx",
        round_number=1,
    )
    queue = _RecordingQueue()

    await executor.execute(_task_context("Region A"), queue)

    assert captured["text"] == "Region A"
    assert "outer-1" not in executor._pending_questions
    states = [event.status.state for event in queue.events if isinstance(event, TaskStatusUpdateEvent)]
    assert TaskState.TASK_STATE_COMPLETED in states


async def test_a_follow_up_on_a_working_outer_task_reaches_the_bound_downstream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pure-A2A follow-up is a continuation of the vendor Task, not an echo of the outer Task."""
    executor = OSSPureExecutor(answer=lambda event: "")
    executor._binding_store = DownstreamBindingStore(tmp_path / "tasks.sqlite3")
    await executor._binding_store.save(
        DownstreamBinding(
            inner_task_id="nms-task-1",
            inner_context_id="inner-ctx",
            outer_task_id="outer-1",
            protocol=PROTOCOL_PURE,
        )
    )
    captured: dict[str, object] = {}

    async def fake_continuation(
        pending: _PendingQuestion,
        text: str,
        event_sink: object,
        updater: TaskUpdater,
        call_context: object,
    ) -> ClientFlowResult:
        del event_sink, updater, call_context
        captured["inner"] = (pending.inner_task_id, pending.inner_context_id)
        captured["text"] = text
        return ClientFlowResult(
            final_state=FINAL_STATE_TASK_COMPLETED,
            task_id="nms-task-1",
            context_id="inner-ctx",
        )

    monkeypatch.setattr(executor, "_run_question_continuation", fake_continuation)
    context = _task_context("Modify the rate to 10 Mbps.", task_id="outer-1")
    context.current_task.status.state = TaskState.TASK_STATE_WORKING
    queue = _RecordingQueue()

    await executor.execute(context, queue)

    assert captured == {
        "inner": ("nms-task-1", "inner-ctx"),
        "text": "Modify the rate to 10 Mbps.",
    }
    states = [event.status.state for event in queue.events if isinstance(event, TaskStatusUpdateEvent)]
    assert states[-1] == TaskState.TASK_STATE_COMPLETED


async def test_a_follow_up_without_a_binding_fails_instead_of_echoing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Telling the caller the message was accepted while nothing was sent is worse than failing."""
    executor = OSSPureExecutor(answer=lambda event: "")
    executor._binding_store = DownstreamBindingStore(tmp_path / "tasks.sqlite3")
    context = _task_context("Modify the rate to 10 Mbps.", task_id="outer-unbound")
    context.current_task.status.state = TaskState.TASK_STATE_WORKING
    queue = _RecordingQueue()

    await executor.execute(context, queue)

    states = [event.status.state for event in queue.events if isinstance(event, TaskStatusUpdateEvent)]
    assert states[-1] == TaskState.TASK_STATE_FAILED
    failed = queue.events[-1]
    assert isinstance(failed, TaskStatusUpdateEvent)
    assert failed.status.message is not None
    assert "No downstream Task is bound" in failed.status.message.parts[0].text


async def test_an_incomplete_sse_hop_remembers_its_downstream_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The SSE hop has no pump; a later follow-up still needs the outer→downstream mapping."""
    executor = OSSPureExecutor(answer=lambda event: "")
    executor._binding_store = DownstreamBindingStore(tmp_path / "tasks.sqlite3")
    executor._nms_client = object()  # type: ignore[assignment]
    _fake_downstream(monkeypatch, executor, FINAL_STATE_INCOMPLETE)
    queue = _RecordingQueue()

    await executor.execute(_new_oss_context("Start energy saving.", task_id="outer-1"), queue)

    binding = await executor._binding_store.get_by_outer_task("outer-1")
    assert binding is not None
    assert binding.inner_task_id == "nms-task-1"


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
    assert last.metadata["pure"]["downstreamHop"] == "ran"


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


async def test_oss_failure_message_carries_the_downstream_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The outer FAILED message must say why the downstream refused, not a generic state name."""
    executor = OSSPureExecutor(answer=lambda event: "")

    async def run(
        user_request: str,
        run_id: str | None,
        event_sink: object,
        updater: object,
        call_context: object,
    ) -> ClientFlowResult:
        del user_request, run_id, event_sink, updater, call_context
        return ClientFlowResult(
            final_state=FINAL_STATE_TASK_FAILED,
            task_id="nms-task-1",
            events=[
                {
                    "kind": "status",
                    "state": "TASK_STATE_COMPLETED",
                    "text": 'The region name "TestRegion" is not recognized.',
                }
            ],
        )

    monkeypatch.setattr(executor, "_run_ran", run)
    queue = _RecordingQueue()

    await executor.execute(_oss_context(), queue)

    failed = [
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent) and event.status.state == TaskState.TASK_STATE_FAILED
    ][-1]
    assert 'The region name "TestRegion" is not recognized.' in failed.status.message.parts[0].text


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


class _FakeNmsClient:
    """Minimal NMS client exposing the Task poll used by restart recovery."""

    def __init__(self, document: dict[str, object]) -> None:
        self.document = document
        self.calls: list[str] = []

    async def get_task(self, task_id: str) -> dict[str, object]:
        """Return the configured Task document."""
        self.calls.append(task_id)
        return dict(self.document)


async def _restart_fixture(
    tmp_path: Path,
    *,
    outer_state: TaskState,
    document: dict[str, object],
) -> tuple[OSSPureExecutor, SqliteTaskStore, DownstreamBindingStore, _FakeNmsClient]:
    """Build an OSS executor whose durable state looks like a restarted process."""
    store = SqliteTaskStore(tmp_path / "oss-task-status.sqlite3")
    context = ServerCallContext(user=UnauthenticatedUser())
    await store.save(
        Task(id="outer-1", context_id="outer-ctx", status=TaskStatus(state=outer_state)),
        context,
    )
    binding_store = DownstreamBindingStore(store.path)
    await binding_store.save(
        DownstreamBinding(
            inner_task_id="inner-1",
            inner_context_id="inner-ctx",
            outer_task_id="outer-1",
            run_id="run-1",
            protocol="pure",
        )
    )
    config = replace(
        offline_direct_config(8000),
        discovery=DISCOVERY_NMS,
        transport=TRANSPORT_WEBHOOK,
    )
    executor = OSSPureExecutor(
        config=config,
        answer=lambda event: "",
        task_store=store,
        binding_store=binding_store,
    )
    executor._webhook_router = WebhookRouter()
    nms_client = _FakeNmsClient(document)
    executor._nms_client = nms_client  # type: ignore[assignment]
    executor._webhook_push_url = "http://127.0.0.1:9000/webhook"
    executor._webhook_push_token = "token"
    return executor, store, binding_store, nms_client


async def test_restart_resumes_a_working_binding_and_keeps_tracking(tmp_path: Path) -> None:
    """A persisted working binding recreates its pump and binds the callback identity."""
    executor, _store, binding_store, nms_client = await _restart_fixture(
        tmp_path,
        outer_state=TaskState.TASK_STATE_WORKING,
        document={"id": "inner-1", "contextId": "inner-ctx", "status": {"state": "TASK_STATE_WORKING"}},
    )

    await executor._resume_active_bindings()

    assert nms_client.calls == ["inner-1"]
    assert "outer-1" in executor._pumps
    assert executor._webhook_router is not None
    assert executor._webhook_router.active_subscriptions == 1
    binding = await binding_store.get("inner-1", "inner-ctx")
    assert binding is not None
    assert binding.terminal is False
    await executor._stop_pumps()


async def test_restart_reconciles_a_terminal_task_and_releases_the_pump(tmp_path: Path) -> None:
    """A Task that finished while the OSS was down is applied at startup, without a pump."""
    executor, store, binding_store, _nms_client = await _restart_fixture(
        tmp_path,
        outer_state=TaskState.TASK_STATE_WORKING,
        document={
            "id": "inner-1",
            "contextId": "inner-ctx",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [
                {
                    "artifactId": "artifact-1",
                    "name": "energy saving report",
                    "parts": [{"text": "Done while the OSS was down."}],
                }
            ],
        },
    )

    await executor._resume_active_bindings()

    outer = await store.get("outer-1", ServerCallContext(user=UnauthenticatedUser()))
    assert outer is not None
    assert outer.status.state == TaskState.TASK_STATE_COMPLETED
    assert outer.artifacts[0].parts[0].text == "Done while the OSS was down."
    binding = await binding_store.get("inner-1", "inner-ctx")
    assert binding is not None
    assert binding.terminal is True
    assert "outer-1" not in executor._pumps


async def test_restart_drops_a_binding_whose_outer_task_is_gone(tmp_path: Path) -> None:
    """A binding without its outer Task cannot publish anything; retire it instead of polling."""
    executor, store, binding_store, nms_client = await _restart_fixture(
        tmp_path,
        outer_state=TaskState.TASK_STATE_WORKING,
        document={"id": "inner-1", "status": {"state": "TASK_STATE_WORKING"}},
    )
    await store.delete("outer-1", ServerCallContext(user=UnauthenticatedUser()))

    await executor._resume_active_bindings()

    assert nms_client.calls == []
    binding = await binding_store.get("inner-1", "inner-ctx")
    assert binding is not None
    assert binding.terminal is True
    assert "outer-1" not in executor._pumps


def test_trace_emitter_prefers_the_resolved_config(
    monkeypatch: pytest.MonkeyPatch,
    offline_config: SampleConfig,
) -> None:
    """A manual OSS start must use ``.env`` values even when the process env is empty."""
    monkeypatch.delenv("A2AT_SAMPLE_TRACE_URL", raising=False)
    monkeypatch.delenv("A2AT_SAMPLE_TRACE_TOKEN", raising=False)
    monkeypatch.delenv("A2AT_SAMPLE_TRACE_DIR", raising=False)
    config = replace(
        offline_config,
        trace_url="http://127.0.0.1:18080",
        trace_token="config-token",
        trace_dir="",
    )

    emitter = _build_trace_emitter(config)

    assert emitter is not None
    assert emitter.base_url == "http://127.0.0.1:18080"
    emitter.close()


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
    assert isinstance(app.state.binding_store, DownstreamBindingStore)
    assert app.state.binding_store.path == store.path


def test_long_task_mode_auto_enables_only_for_the_nms_webhook_hop(tmp_path: Path) -> None:
    """``auto`` is the real NMS hop: webhook transport plus a persistent binding store."""
    binding_store = DownstreamBindingStore(tmp_path / "tasks.sqlite3")
    nms_config = replace(
        offline_direct_config(8000),
        discovery=DISCOVERY_NMS,
        transport=TRANSPORT_WEBHOOK,
    )

    enabled = OSSPureExecutor(config=nms_config, answer=lambda event: "", binding_store=binding_store)
    assert enabled._long_task_enabled() is True

    sse = OSSPureExecutor(
        config=replace(nms_config, transport=TRANSPORT_SSE),
        answer=lambda event: "",
        binding_store=binding_store,
    )
    assert sse._long_task_enabled() is False

    no_store = OSSPureExecutor(config=nms_config, answer=lambda event: "")
    assert no_store._long_task_enabled() is False

    forced = OSSPureExecutor(
        config=replace(nms_config, transport=TRANSPORT_SSE),
        answer=lambda event: "",
        binding_store=binding_store,
        long_task_mode=LONG_TASK_MODE_ON,
    )
    assert forced._long_task_enabled() is True

    off = OSSPureExecutor(
        config=nms_config,
        answer=lambda event: "",
        binding_store=binding_store,
        long_task_mode=LONG_TASK_MODE_OFF,
    )
    assert off._long_task_enabled() is False


async def test_the_question_continuation_hands_an_incomplete_task_to_the_pump(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """After the operator answer, a still-running vendor Task needs the pump again."""
    binding_store = DownstreamBindingStore(tmp_path / "tasks.sqlite3")
    config = replace(
        offline_direct_config(8000),
        discovery=DISCOVERY_NMS,
        transport=TRANSPORT_WEBHOOK,
    )
    executor = OSSPureExecutor(config=config, answer=lambda event: "", binding_store=binding_store)
    router = WebhookRouter()
    executor._webhook_router = router
    executor._nms_client = object()  # type: ignore[assignment]

    async def fake_flow(**kwargs: object) -> ClientFlowResult:
        del kwargs
        return ClientFlowResult(
            final_state=FINAL_STATE_INCOMPLETE,
            task_id="inner-1",
            context_id="inner-ctx",
        )

    monkeypatch.setattr("protocol.oss_agent.continue_pure_client_flow", fake_flow)
    pending = _PendingQuestion(
        outer_task_id="outer-1",
        outer_context_id="outer-ctx",
        run_id="run-1",
        inner_task_id="inner-1",
        inner_context_id="inner-ctx",
        round_number=1,
        user_request="Create a task for Region A.",
    )
    updater = TaskUpdater(_RecordingQueue(), "outer-1", "outer-ctx")

    result = await executor._run_question_continuation(
        pending,
        "Region A",
        lambda event: None,
        updater,
        _task_context("Region A").call_context,
    )

    assert result.final_state == FINAL_STATE_INCOMPLETE
    binding = await binding_store.get("inner-1", "inner-ctx")
    assert binding is not None
    assert binding.outer_task_id == "outer-1"
    assert "outer-1" in executor._pending_handoffs
    assert executor._pending_handoffs["outer-1"].user_request == "Create a task for Region A."
    assert router.active_subscriptions == 1

    executor._start_pending_pump("outer-1")
    assert "outer-1" in executor._pumps
    await executor._stop_pumps()
    assert router.active_subscriptions == 0


async def test_the_question_continuation_retires_the_binding_on_a_terminal_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The pump suspended for the question; a terminal answer must not leave a live binding."""
    binding_store = DownstreamBindingStore(tmp_path / "tasks.sqlite3")
    await binding_store.save(
        DownstreamBinding(
            inner_task_id="inner-1",
            inner_context_id="inner-ctx",
            outer_task_id="outer-1",
            run_id="run-1",
            protocol="pure",
        )
    )
    config = replace(
        offline_direct_config(8000),
        discovery=DISCOVERY_NMS,
        transport=TRANSPORT_WEBHOOK,
    )
    executor = OSSPureExecutor(config=config, answer=lambda event: "", binding_store=binding_store)
    executor._webhook_router = WebhookRouter()
    executor._nms_client = object()  # type: ignore[assignment]

    async def fake_flow(**kwargs: object) -> ClientFlowResult:
        del kwargs
        return ClientFlowResult(
            final_state=FINAL_STATE_TASK_COMPLETED,
            task_id="inner-1",
            context_id="inner-ctx",
        )

    monkeypatch.setattr("protocol.oss_agent.continue_pure_client_flow", fake_flow)
    pending = _PendingQuestion(
        outer_task_id="outer-1",
        outer_context_id="outer-ctx",
        run_id="run-1",
        inner_task_id="inner-1",
        inner_context_id="inner-ctx",
        round_number=1,
        user_request="Create a task for Region A.",
    )

    await executor._run_question_continuation(
        pending,
        "Region A",
        lambda event: None,
        TaskUpdater(_RecordingQueue(), "outer-1", "outer-ctx"),
        _task_context("Region A").call_context,
    )

    binding = await binding_store.get("inner-1", "inner-ctx")
    assert binding is not None
    assert binding.terminal is True


async def test_the_pump_callbacks_restore_the_request_for_each_decision() -> None:
    """A pump started by a later request must see that request, not the last one another task set."""
    provider = _ContextRequestProvider()
    seen: list[str | None] = []

    async def recommendation(event: dict[str, object]) -> None:
        del event
        seen.append(provider.current())

    def answer(event: dict[str, object]) -> str:
        del event
        seen.append(provider.current())
        return "Region A"

    executor = OSSPureExecutor(
        answer=answer,
        recommendation=recommendation,  # type: ignore[arg-type]
        decision_provider=provider,
    )
    wrapped_answer = executor._answer_for_request("Create a task for Region A.")
    wrapped_recommendation = executor._recommendation_for_request("Create a task for Region A.")

    assert wrapped_recommendation is not None
    await wrapped_recommendation({})
    assert wrapped_answer({}) == "Region A"
    assert seen == ["Create a task for Region A.", "Create a task for Region A."]


async def test_the_nms_webhook_flow_hands_the_subscription_to_the_pump(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An incomplete NMS flow transfers its callback queue instead of unsubscribing."""
    binding_store = DownstreamBindingStore(tmp_path / "tasks.sqlite3")
    config = replace(
        offline_direct_config(8000),
        discovery=DISCOVERY_NMS,
        transport=TRANSPORT_WEBHOOK,
    )
    executor = OSSPureExecutor(config=config, answer=lambda event: "", binding_store=binding_store)
    router = WebhookRouter()
    executor._webhook_router = router
    executor._nms_client = object()  # type: ignore[assignment]

    async def fake_flow(**kwargs: object) -> ClientFlowResult:
        del kwargs
        return ClientFlowResult(
            final_state=FINAL_STATE_INCOMPLETE,
            task_id="inner-1",
            context_id="inner-ctx",
        )

    monkeypatch.setattr("protocol.oss_agent.run_pure_client_flow", fake_flow)
    context = _oss_context()
    updater = TaskUpdater(_RecordingQueue(), "outer-1", "outer-ctx")

    result = await executor._run_ran_webhook(
        "request",
        "run-1",
        lambda event: None,
        updater,
        context.call_context,
    )

    assert result.final_state == FINAL_STATE_INCOMPLETE
    binding = await binding_store.get("inner-1", "inner-ctx")
    assert binding is not None
    assert binding.outer_task_id == "outer-1"
    assert "outer-1" in executor._pending_handoffs
    assert router.active_subscriptions == 1

    executor._start_pending_pump("outer-1")
    assert "outer-1" in executor._pumps
    await executor._stop_pumps()
    assert router.active_subscriptions == 0


async def test_an_ungrounded_vendor_question_is_relayed_instead_of_answered() -> None:
    """The OSS must not send an empty answer when its own guard refused to invent a value."""
    executor = OSSPureExecutor(
        config=replace(offline_direct_config(8000), discovery=DISCOVERY_NMS),
        answer=lambda event: "",
    )

    with pytest.raises(_QuestionPendingError):
        await executor._answer_or_relay(
            {"kind": "status", "state": "TASK_STATE_INPUT_REQUIRED", "text": "Not feasible, please confirm"}
        )


async def test_a_grounded_vendor_question_is_answered_without_relaying() -> None:
    executor = OSSPureExecutor(
        config=replace(offline_direct_config(8000), discovery=DISCOVERY_NMS),
        answer=lambda event: "Region A",
    )

    assert (
        await executor._answer_or_relay(
            {"kind": "status", "state": "TASK_STATE_INPUT_REQUIRED", "text": "Which region?"}
        )
        == "Region A"
    )


async def test_a_follow_up_to_a_terminal_target_task_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The picker check is point-in-time; the vendor Task can end before the follow-up is sent."""
    executor = OSSPureExecutor(
        config=replace(offline_direct_config(8000), discovery=DISCOVERY_NMS),
        answer=lambda event: "",
    )
    executor._binding_store = DownstreamBindingStore(tmp_path / "tasks.sqlite3")
    await executor._binding_store.save(
        DownstreamBinding(
            inner_task_id="nms-task-1",
            inner_context_id="inner-ctx",
            outer_task_id="outer-1",
            protocol=PROTOCOL_PURE,
        )
    )
    nms_client = _FakeNmsClient({"status": {"state": "TASK_STATE_FAILED"}})
    executor._nms_client = nms_client  # type: ignore[assignment]

    async def fail_if_forwarded(*args: object, **kwargs: object) -> ClientFlowResult:
        raise AssertionError("a terminal target Task must not receive the follow-up")

    monkeypatch.setattr(executor, "_run_question_continuation", fail_if_forwarded)
    context = _task_context("Modify the rate to 10 Mbps.", task_id="outer-1")
    context.current_task.status.state = TaskState.TASK_STATE_WORKING
    queue = _RecordingQueue()

    await executor.execute(context, queue)

    assert nms_client.calls == ["nms-task-1"]
    failed = queue.events[-1]
    assert isinstance(failed, TaskStatusUpdateEvent)
    assert failed.status.state == TaskState.TASK_STATE_FAILED
    text = failed.status.message.parts[0].text
    assert "already ended" in text
    assert "Start a new Task" in text


def test_a_terminal_state_400_is_reported_as_a_target_condition() -> None:
    exc = NmsProtocolError(
        'NMS A2A request returned HTTP 400: {"error":{"code":400,"status":"UNIMPLEMENTED",'
        '"message":"Cannot send message to task t1: task is in terminal state '
        'TASK_STATE_FAILED and cannot accept further messages"}}',
        http_status=400,
    )

    message = _terminal_target_failure(exc)

    assert message is not None
    assert "(TASK_STATE_FAILED)" in message
    assert "Start a new Task" in message


def test_an_unrelated_400_keeps_the_generic_hop_failure() -> None:
    assert _terminal_target_failure(NmsProtocolError("HTTP 400: bad request", http_status=400)) is None
