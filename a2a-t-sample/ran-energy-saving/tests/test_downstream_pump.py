"""Tests of the background pump that keeps a long-running OSS Task updated."""

from __future__ import annotations

import asyncio
import contextlib
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
from protocol.nms_client import NmsProtocolError


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


def _message_payload(text: str) -> dict[str, object]:
    """Build one webhook agent-message payload with no Task identity (a plain vendor question)."""
    message = Message(message_id="message-2", role=Role.ROLE_AGENT)
    message.parts.add(text=text)
    return MessageToDict(StreamResponse(message=message))


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
    recommendation: object = None,
    on_question: object = None,
    sent_texts: list[str] | None = None,
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
            sent_texts=sent_texts or [],
        ),
        subscription=subscription,
        transport=transport,
        task_store=store,
        binding_store=binding_store,
        push_sender=push_sender,
        answer=answer,  # type: ignore[arg-type]
        recommendation=recommendation,  # type: ignore[arg-type]
        on_question=on_question,  # type: ignore[arg-type]
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
    # Every late outer event still names the far-side Task, so the client can query the NMS.
    assert last_event.metadata["pure"]["downstreamTaskId"] == "inner-1"
    assert last_event.metadata["pure"]["downstreamContextId"] == "inner-ctx"
    assert last_event.metadata["pure"]["downstreamHop"] == "nms"
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


async def test_a_plain_message_question_is_relayed_to_the_operator(tmp_path: Path) -> None:
    """A vendor text question after the handoff must reach the operator, not become WORKING."""
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
    registered: list[tuple[dict[str, object], int]] = []

    async def recommendation(event: dict[str, object]) -> dict[str, object]:
        return {
            "action": "accept",
            "rationale": "the request names Region A",
            "text": "Region A",
            "filledParams": {"region": "Region A"},
        }

    def on_question(event: dict[str, object], round_number: int) -> None:
        registered.append((dict(event), round_number))

    pump = _pump(
        store=store,
        binding_store=binding_store,
        subscription=subscription,
        transport=_Transport(),
        push_sender=push_sender,
        recommendation=recommendation,
        on_question=on_question,
    )

    task = pump.start()
    await subscription.queue.put(
        _message_payload("Energy saving region is not recognized. Please provide the correct region name.")
    )
    await asyncio.wait_for(task, timeout=3.0)

    outer = await store.get("outer-1", _context())
    assert outer is not None
    assert outer.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert "correct region name" in outer.status.message.parts[0].text
    last = push_sender.events[-1][1]
    assert isinstance(last, TaskStatusUpdateEvent)
    assert last.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    pure = last.metadata["pure"]
    assert pure["phase"] == "question"
    assert pure["kind"] == "negotiation"
    assert pure["round"] == 1
    assert pure["recommendation"]["text"] == "Region A"
    assert pure["downstreamTaskId"] == "inner-1"
    assert len(registered) == 1
    assert registered[0][1] == 1
    assert registered[0][0]["text"].startswith("Energy saving region")
    # The pump releases the queue: the answer continuation owns the downstream identity next.
    assert subscription.unsubscribed is True
    assert pump.terminal is False


async def test_a_plain_message_with_a_task_id_stays_an_observation(tmp_path: Path) -> None:
    """A message that belongs to the Task is a status note, not a question to the operator."""
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
    registered: list[object] = []

    def on_question(event: dict[str, object], round_number: int) -> None:
        del round_number
        registered.append(event)

    pump = _pump(
        store=store,
        binding_store=binding_store,
        subscription=subscription,
        transport=_Transport(),
        push_sender=_PushSender(),
        on_question=on_question,
    )

    payload = _message_payload("Collecting data.")
    payload["message"]["taskId"] = "inner-1"  # type: ignore[index]
    task = pump.start()
    await subscription.queue.put(payload)
    await asyncio.sleep(0)
    await subscription.queue.put(_artifact_payload("inner-1", "inner-ctx", "energy saving report", "Done."))
    await asyncio.wait_for(task, timeout=3.0)

    assert registered == []
    outer = await store.get("outer-1", _context())
    assert outer is not None
    assert outer.artifacts[0].parts[0].text == "Done."


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


def test_completed_with_a_refusal_text_maps_to_failed() -> None:
    """The pump must not relay the vendor's COMPLETED-with-error as an outer success."""
    from protocol.downstream_pump import _map_status

    state, _text, phase = _map_status(
        "TASK_STATE_COMPLETED",
        'The region name "TestRegion" is not recognized.',
    )

    assert state == TaskState.TASK_STATE_FAILED
    assert phase == "resolution"


def test_benign_completed_text_maps_to_completed() -> None:
    from protocol.downstream_pump import _map_status

    state, _text, phase = _map_status("TASK_STATE_COMPLETED", "Task completed.")

    assert state == TaskState.TASK_STATE_COMPLETED
    assert phase == "report"


async def test_an_ungrounded_input_required_is_relayed_not_answered(tmp_path: Path) -> None:
    """A guard that refuses to invent a value must hand the question to the operator."""
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
    questions: list[dict[str, object]] = []
    pump = _pump(
        store=store,
        binding_store=binding_store,
        subscription=subscription,
        transport=transport,
        push_sender=_PushSender(),
        answer=lambda event: "",
        on_question=lambda event, round_number: questions.append(dict(event)),
    )

    task = pump.start()
    await subscription.queue.put(
        _status_payload("inner-1", "inner-ctx", "TASK_STATE_INPUT_REQUIRED", "Not feasible, please confirm")
    )
    await asyncio.wait_for(task, timeout=3.0)

    assert transport.requests == []
    assert questions and questions[0]["text"] == "Not feasible, please confirm"
    outer = await store.get("outer-1", _context())
    assert outer is not None
    assert outer.status.state == TaskState.TASK_STATE_INPUT_REQUIRED


class _ConflictTransport(_Transport):
    """A transport whose follow-up is refused by a still-open vendor stream."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def send(self, request: object, call_context: object) -> list[dict[str, object]]:
        del call_context
        self.attempts += 1
        self.requests.append(request)
        raise NmsProtocolError(
            "HTTP 409: Task already has an active stream (TASK_STREAM_CONFLICT)",
            http_status=409,
            retry_after=7.0,
        )


async def _pump_over(
    tmp_path: Path,
    transport: _Transport,
    **kwargs: object,
) -> tuple[DownstreamPump, _Subscription, InMemoryTaskStore]:
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
    pump = _pump(
        store=store,
        binding_store=binding_store,
        subscription=subscription,
        transport=transport,
        push_sender=_PushSender(),
        **kwargs,  # type: ignore[arg-type]
    )
    return pump, subscription, store


async def test_the_pump_reopens_the_question_on_a_stream_conflict(tmp_path: Path) -> None:
    """A 409 is retryable per-Task state: keep the card open instead of ending the Task."""
    registered: list[int] = []
    transport = _ConflictTransport()
    pump, subscription, store = await _pump_over(
        tmp_path,
        transport,
        answer=lambda event: "Region A",
        on_question=lambda event, round_number: registered.append(round_number),
    )

    task = pump.start()
    await subscription.queue.put(
        _status_payload("inner-1", "inner-ctx", "TASK_STATE_INPUT_REQUIRED", "Which region?")
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert transport.attempts == 1
    assert registered == [1]
    outer = await store.get("outer-1", _context())
    assert outer is not None
    assert outer.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    metadata = MessageToDict(outer.status.message.metadata)
    assert metadata["pure"]["conflict"] == "stream_conflict"
    assert metadata["pure"]["retryAfterSeconds"] == 7.0


async def test_the_pump_relays_when_the_round_budget_is_exhausted(tmp_path: Path) -> None:
    """Out of rounds is not an answer: the operator must get the question."""
    registered: list[int] = []
    transport = _Transport([])
    pump, subscription, store = await _pump_over(
        tmp_path,
        transport,
        answer=lambda event: "Region A",
        on_question=lambda event, round_number: registered.append(round_number),
    )
    pump._rounds = 3  # type: ignore[attr-defined]

    task = pump.start()
    await subscription.queue.put(
        _status_payload("inner-1", "inner-ctx", "TASK_STATE_INPUT_REQUIRED", "Which region?")
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert transport.requests == []
    assert registered == [4]
    outer = await store.get("outer-1", _context())
    assert outer is not None
    assert outer.status.state == TaskState.TASK_STATE_INPUT_REQUIRED


async def test_a_pump_relayed_question_carries_metadata_on_the_status_message(tmp_path: Path) -> None:
    """The client poller reads ``status.message.metadata``; the event metadata alone is lost."""
    transport = _Transport([])
    pump, subscription, store = await _pump_over(tmp_path, transport, answer=lambda event: "")

    task = pump.start()
    await subscription.queue.put(
        _status_payload(
            "inner-1",
            "inner-ctx",
            "TASK_STATE_INPUT_REQUIRED",
            "## Feasibility Negotiation\nNot feasible, please confirm.",
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    outer = await store.get("outer-1", _context())
    assert outer is not None
    metadata = MessageToDict(outer.status.message.metadata)
    assert metadata["pure"]["round"] == 1
    assert metadata["pure"]["negotiationKind"] == "feasibility"
