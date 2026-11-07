"""Tests of the background pump that keeps a long-running OSS Task updated."""

from __future__ import annotations

import asyncio
from pathlib import Path

from a2a.server.context import ServerCallContext, UnauthenticatedUser
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    Artifact,
    Message,
    Role,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf.json_format import MessageToDict
from protocol.downstream_binding_store import DownstreamBinding, DownstreamBindingStore
from protocol.downstream_pump import DownstreamPump, DownstreamPumpContext


def _context() -> ServerCallContext:
    """Build one unauthenticated request context."""
    return ServerCallContext(state={}, user=UnauthenticatedUser())


def _status_payload(
    task_id: str,
    context_id: str,
    state: str,
    text: str = "",
) -> dict[str, object]:
    """Build one webhook status payload."""
    status = TaskStatus(state=state)
    if text:
        message = Message(message_id="message-1", role=Role.ROLE_AGENT)
        message.parts.add(text=text)
        status.message.CopyFrom(message)
    return MessageToDict(
        StreamResponse(
            status_update=TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=status,
            )
        )
    )


def _artifact_payload(task_id: str, context_id: str, name: str, text: str) -> dict[str, object]:
    """Build one webhook report artifact payload."""
    artifact = Artifact(artifact_id="artifact-1", name=name)
    artifact.parts.add(text=text)
    return MessageToDict(
        StreamResponse(
            artifact_update=TaskArtifactUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                artifact=artifact,
            )
        )
    )


def _completed_event(task_id: str, context_id: str) -> dict[str, object]:
    """Build one normalized terminal event returned by a fake follow-up transport."""
    return {
        "kind": "status",
        "state": "TASK_STATE_COMPLETED",
        "task_id": task_id,
        "context_id": context_id,
        "text": "The NMS task completed.",
        "metadata": {},
    }


class _Subscription:
    """Test double of one webhook subscription."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self.unsubscribed = False

    def unsubscribe(self) -> None:
        """Record the release of the queue."""
        self.unsubscribed = True


class _Transport:
    """Test double of the downstream follow-up transport."""

    def __init__(self, batch: list[dict[str, object]] | None = None) -> None:
        self.batch = batch or []
        self.requests: list[object] = []

    async def send(self, request: object, call_context: object) -> list[dict[str, object]]:
        """Record the follow-up and return the configured batch."""
        del call_context
        self.requests.append(request)
        return list(self.batch)


class _PushSender:
    """Test double recording A2A push notifications."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def send_notification(self, task_id: str, event: object) -> None:
        """Record one push."""
        self.events.append((task_id, event))


def _pump(
    *,
    store: InMemoryTaskStore,
    binding_store: DownstreamBindingStore,
    subscription: _Subscription,
    transport: _Transport,
    push_sender: _PushSender,
    answer: object = None,
) -> DownstreamPump:
    """Build one pump over the shared outer Task."""
    return DownstreamPump(
        context=DownstreamPumpContext(
            run_id="run-1",
            inner_task_id="inner-1",
            inner_context_id="inner-ctx",
            outer_task_id="outer-1",
            outer_context_id="outer-ctx",
            downstream_hop="nms",
            downstream_name="NMS",
            followup="both",
            max_rounds=3,
            call_timeout_seconds=10.0,
            call_context=_context(),
        ),
        subscription=subscription,
        transport=transport,
        task_store=store,
        binding_store=binding_store,
        push_sender=push_sender,
        answer=answer,  # type: ignore[arg-type]
    )


async def test_the_pump_updates_the_outer_task_and_pushes_late_events(tmp_path: Path) -> None:
    store = InMemoryTaskStore()
    await store.save(
        Task(id="outer-1", context_id="outer-ctx", status=TaskStatus(state=TaskState.TASK_STATE_WORKING)),
        _context(),
    )
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
    subscription = _Subscription()
    push_sender = _PushSender()
    pump = _pump(
        store=store,
        binding_store=binding_store,
        subscription=subscription,
        transport=_Transport(),
        push_sender=push_sender,
    )

    task = pump.start()
    await subscription.queue.put(_status_payload("inner-1", "inner-ctx", "TASK_STATE_WORKING", "Collecting data."))
    await subscription.queue.put(_artifact_payload("inner-1", "inner-ctx", "energy saving report", "Done."))
    await asyncio.wait_for(task, timeout=3.0)

    outer = await store.get("outer-1", _context())
    assert outer is not None
    assert outer.status.state == TaskState.TASK_STATE_COMPLETED
    assert outer.artifacts[0].parts[0].text == "Done."
    assert len(push_sender.events) == 3
    last_event = push_sender.events[-1][1]
    assert isinstance(last_event, TaskStatusUpdateEvent)
    assert last_event.status.state == TaskState.TASK_STATE_COMPLETED
    binding = await binding_store.get("inner-1", "inner-ctx")
    assert binding is not None
    assert binding.terminal is True
    assert subscription.unsubscribed is True


async def test_reconcile_applies_a_terminal_task_polled_after_restart(tmp_path: Path) -> None:
    """A terminal push may be missed while the process is down; the startup poll recovers it."""
    store = InMemoryTaskStore()
    await store.save(
        Task(id="outer-1", context_id="outer-ctx", status=TaskStatus(state=TaskState.TASK_STATE_WORKING)),
        _context(),
    )
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
    subscription = _Subscription()
    push_sender = _PushSender()
    pump = _pump(
        store=store,
        binding_store=binding_store,
        subscription=subscription,
        transport=_Transport(),
        push_sender=push_sender,
    )

    await pump.reconcile(
        {
            "id": "inner-1",
            "contextId": "inner-ctx",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [
                {
                    "artifactId": "artifact-1",
                    "name": "energy saving report",
                    "parts": [{"text": "Done after restart."}],
                }
            ],
        }
    )

    outer = await store.get("outer-1", _context())
    assert outer is not None
    assert outer.status.state == TaskState.TASK_STATE_COMPLETED
    assert outer.artifacts[0].parts[0].text == "Done after restart."
    binding = await binding_store.get("inner-1", "inner-ctx")
    assert binding is not None
    assert binding.terminal is True
    assert pump.terminal is True


async def test_reconcile_keeps_a_working_task_active(tmp_path: Path) -> None:
    """A non-terminal poll only refreshes the outer Task; the pump stays available for pushes."""
    store = InMemoryTaskStore()
    await store.save(
        Task(id="outer-1", context_id="outer-ctx", status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED)),
        _context(),
    )
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
    pump = _pump(
        store=store,
        binding_store=binding_store,
        subscription=_Subscription(),
        transport=_Transport(),
        push_sender=_PushSender(),
    )

    await pump.reconcile(
        {"id": "inner-1", "contextId": "inner-ctx", "status": {"state": "TASK_STATE_WORKING"}}
    )

    outer = await store.get("outer-1", _context())
    assert outer is not None
    assert outer.status.state == TaskState.TASK_STATE_WORKING
    assert pump.terminal is False


async def test_the_pump_answers_a_delayed_input_required(tmp_path: Path) -> None:
    store = InMemoryTaskStore()
    await store.save(
        Task(id="outer-1", context_id="outer-ctx", status=TaskStatus(state=TaskState.TASK_STATE_WORKING)),
        _context(),
    )
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
    subscription = _Subscription()
    transport = _Transport([_completed_event("inner-1", "inner-ctx")])
    push_sender = _PushSender()
    answers: list[dict[str, object]] = []

    def answer(event: dict[str, object]) -> str:
        answers.append(event)
        return "Region A, 30Mbps"

    pump = _pump(
        store=store,
        binding_store=binding_store,
        subscription=subscription,
        transport=transport,
        push_sender=push_sender,
        answer=answer,
    )

    task = pump.start()
    await subscription.queue.put(
        _status_payload("inner-1", "inner-ctx", "TASK_STATE_INPUT_REQUIRED", "Which region?")
    )
    await asyncio.wait_for(task, timeout=3.0)

    assert len(answers) == 1
    assert transport.requests
    outer = await store.get("outer-1", _context())
    assert outer is not None
    assert outer.status.state == TaskState.TASK_STATE_COMPLETED
    assert subscription.unsubscribed is True
