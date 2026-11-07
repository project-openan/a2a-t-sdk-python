"""Background pump of downstream NMS events for a long-running OSS Task.

The OSS Agent forwards one request to the NMS and then waits for a Task that may run for days. The
initial synchronous flow only stays attached for a configurable settle window; after it returns,
this pump keeps consuming the run's webhook queue and applies every later event to the outer A2A
Task:

* the outer Task is persisted through ``TaskManager`` (SQLite in a real run);
* the client receives the same event as an A2A push notification (polling is the reconnect
  fallback);
* a Run Explorer span is emitted with the run id so Langfuse keeps the late execution;
* a delayed ``INPUT_REQUIRED`` is answered with the same decision callback the initial flow uses.

The pump is best effort: a malformed push or a failed push notification never stops the tracking.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol

from a2a.client.client import ClientCallContext
from a2a.server.context import ServerCallContext
from a2a.server.tasks.push_notification_sender import PushNotificationSender
from a2a.server.tasks.task_manager import TaskManager
from a2a.server.tasks.task_store import TaskStore
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf.json_format import ParseDict
from shared.trace import infer_event_phase, record_span, trace_scope

from protocol.client_flow import is_report_artifact, normalize_webhook_payload
from protocol.downstream_binding_store import DownstreamBindingStore

_LOGGER = logging.getLogger("ran_energy_saving.downstream_pump")

#: Downstream states that end the outer Task.
_TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {
        "TASK_STATE_COMPLETED",
        "TASK_STATE_REJECTED",
        "TASK_STATE_FAILED",
        "TASK_STATE_CANCELED",
    }
)

#: Recent event fingerprints kept for webhook retry deduplication.
_RECENT_EVENTS: Final[int] = 128


class PumpTransport(Protocol):
    """The one method the pump needs from a downstream transport."""

    async def send(
        self,
        request: Any,
        call_context: ClientCallContext,
    ) -> list[dict[str, object]]:
        """Send one follow-up and return its events."""
        ...


class PumpSubscription(Protocol):
    """The one queue/unsubscribe surface of a webhook subscription."""

    queue: asyncio.Queue[dict[str, object]]

    def unsubscribe(self) -> None:
        """Release the run's callback queue."""
        ...


@dataclass(frozen=True)
class DownstreamPumpContext:
    """Correlation and policy values of one tracked downstream Task.

    Attributes:
        run_id: Run Explorer id used for late spans.
        inner_task_id: Downstream/NMS Task id used as the binding key.
        inner_context_id: Downstream/NMS context id (empty when the vendor has none).
        outer_task_id: A2A Task id exposed to the client.
        outer_context_id: A2A context id of the outer Task.
        downstream_hop: ``nms`` or ``ran`` tag written on every outer event.
        downstream_name: Human name used in the status text.
        followup: Fields a pure follow-up carries (``both`` / ``task`` / ``context``).
        max_rounds: Negotiation round budget after the handoff.
        call_timeout_seconds: HTTP budget of a follow-up send.
        call_context: Request context owning the outer Task (for ``TaskManager`` persistence).
    """

    run_id: str
    inner_task_id: str
    outer_task_id: str
    downstream_name: str
    followup: str
    max_rounds: int
    call_timeout_seconds: float
    call_context: ServerCallContext
    inner_context_id: str = ""
    outer_context_id: str = ""
    downstream_hop: str = "nms"


class DownstreamPump:
    """Consume one run's webhook queue until the downstream Task reaches a terminal state."""

    def __init__(
        self,
        *,
        context: DownstreamPumpContext,
        subscription: PumpSubscription,
        transport: PumpTransport,
        task_store: TaskStore,
        binding_store: DownstreamBindingStore,
        push_sender: PushNotificationSender | None = None,
        answer: Callable[[dict[str, object]], str | Awaitable[str]] | None = None,
    ) -> None:
        """Create one pump.

        Args:
            context: Correlation and policy values of the tracked Task.
            subscription: The run's webhook queue and unsubscribe hook.
            transport: Downstream transport used to answer a delayed ``INPUT_REQUIRED``.
            task_store: Outer Task store shared with the A2A request handler.
            binding_store: Durable binding used by restart recovery and retention.
            push_sender: Optional A2A push-notification sender.
            answer: Decision callback answering a downstream ``INPUT_REQUIRED``.
        """
        self._context = context
        self._subscription = subscription
        self._transport = transport
        self._task_store = task_store
        self._binding_store = binding_store
        self._push_sender = push_sender
        self._answer = answer
        self._terminal = False
        self._rounds = 0
        self._recent: deque[str] = deque(maxlen=_RECENT_EVENTS)
        self._manager: TaskManager | None = None

    @property
    def terminal(self) -> bool:
        """Whether the downstream Task already reached a terminal state."""
        return self._terminal

    async def reconcile(self, task: Mapping[str, object]) -> None:
        """Apply one polled vendor Task document before the push stream resumes.

        A restart can miss terminal pushes. The authoritative Task is polled once at startup; a
        terminal state (and its report artifact) is applied through the normal outer-Task path, so
        the client sees the same events it would have received from a push.
        """
        if self._terminal:
            return
        status = task.get("status")
        state_name = str(status.get("state") or "") if isinstance(status, Mapping) else ""
        context_id = _text(task.get("contextId") or task.get("context_id"))
        task_id = self._context.inner_task_id
        artifacts = task.get("artifacts")
        if state_name == "TASK_STATE_COMPLETED" and isinstance(artifacts, list):
            for raw in artifacts:
                event = _artifact_event_from_document(raw, task_id=task_id, context_id=context_id)
                if event is not None:
                    await self._apply(event)
        if self._terminal or not state_name:
            return
        await self._apply(
            {
                "kind": "status",
                "task_id": task_id,
                "context_id": context_id,
                "state": state_name,
                "text": _status_text(status),
            }
        )

    def start(self) -> asyncio.Task[None]:
        """Start the pump as one background task."""
        return asyncio.create_task(self._run(), name=f"oss-downstream-pump-{self._context.outer_task_id}")

    async def _run(self) -> None:
        """Consume the run's queue until the downstream finishes or the task is cancelled."""
        with trace_scope(run_id=self._context.run_id or None, role="oss"):
            try:
                # Let the request handler persist the outer Task's last synchronous event first.
                await asyncio.sleep(0)
                while not self._terminal:
                    payload = await self._subscription.queue.get()
                    event = normalize_webhook_payload(payload)
                    try:
                        await self._apply(event)
                    except Exception:  # noqa: BLE001 - one bad push must not stop the tracking
                        _LOGGER.exception("failed to apply one downstream event")
            except asyncio.CancelledError:
                raise
            finally:
                self._subscription.unsubscribe()

    async def _apply(self, event: dict[str, object]) -> None:
        """Apply one normalized downstream event to the outer Task."""
        if self._terminal or self._is_duplicate(event):
            return
        kind = str(event.get("kind") or "")
        if kind == "status" and event.get("state") == "TASK_STATE_INPUT_REQUIRED":
            await self._answer_input_required(event)
            return
        await self._publish_outer(event)
        if self._is_terminal_event(event):
            if kind == "artifact":
                # The sample treats the report artifact as the end of the execution; the vendor
                # may not send a separate COMPLETED status.
                await self._publish_status(
                    TaskState.TASK_STATE_COMPLETED,
                    f"The {self._context.downstream_name} report arrived.",
                    "report",
                )
            self._terminal = True
            await self._mark_terminal(event)

    async def _answer_input_required(self, event: dict[str, object]) -> None:
        """Answer one delayed negotiation request and send the follow-up."""
        text = await _answer_for(event, self._answer)
        if not text.strip():
            await self._publish_status(
                TaskState.TASK_STATE_WORKING,
                f"The {self._context.downstream_name} requested input; no answer is configured.",
                "input_required",
            )
            return
        await self._publish_status(
            TaskState.TASK_STATE_WORKING,
            f"OSS Agent answered the {self._context.downstream_name}: {text}",
            "answer",
        )
        if self._rounds >= self._context.max_rounds:
            return
        self._rounds += 1
        request = _follow_up_request(
            text,
            task_id=_text(event.get("task_id")),
            context_id=_text(event.get("context_id")),
            followup=self._context.followup,
        )
        batch = await self._transport.send(
            request,
            ClientCallContext(
                timeout=self._context.call_timeout_seconds,
                service_parameters={"A2A-Extensions": ""},
            ),
        )
        for item in batch:
            await self._apply(item)

    async def _publish_outer(self, event: dict[str, object]) -> None:
        """Persist one outer Task event, push it, and trace it."""
        kind = str(event.get("kind") or "")
        outer_event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent | None
        if kind == "artifact":
            outer_event = self._artifact_event(event)
        elif kind == "status":
            state = str(event.get("state") or "")
            outer_event = self._status_event(event, state)
        elif kind == "message":
            text = _text(event.get("text")) or f"The {self._context.downstream_name} sent a message."
            outer_event = self._status_message(TaskState.TASK_STATE_WORKING, text, "message")
        else:
            outer_event = None
        if outer_event is None:
            return
        await self._persist(outer_event)
        await self._push(outer_event)
        self._trace(event)

    def _status_event(
        self,
        event: dict[str, object],
        state: str,
    ) -> TaskStatusUpdateEvent:
        """Map one downstream status to an outer status event."""
        outer_state, default_text, phase = _map_status(state)
        text = _text(event.get("text")) or default_text.format(name=self._context.downstream_name)
        metadata = self._metadata(event, phase)
        return self._status_message(outer_state, text, phase, metadata)

    async def _publish_status(self, state: TaskState, text: str, phase: str) -> None:
        """Persist and push one pump-generated outer status (answer / waiting)."""
        event = self._status_message(state, text, phase)
        await self._persist(event)
        await self._push(event)

    def _status_message(
        self,
        state: TaskState,
        text: str,
        phase: str,
        metadata: dict[str, object] | None = None,
    ) -> TaskStatusUpdateEvent:
        """Build one outer status event with an agent message."""
        message = Message(message_id=str(uuid.uuid4()), role=Role.ROLE_AGENT)
        message.parts.add(text=text)
        status = TaskStatus(state=state)
        status.message.CopyFrom(message)
        return TaskStatusUpdateEvent(
            task_id=self._context.outer_task_id,
            context_id=self._context.outer_context_id,
            status=status,
            metadata=metadata or self._metadata(None, phase),
        )

    def _artifact_event(self, event: dict[str, object]) -> TaskArtifactUpdateEvent | None:
        """Map one downstream artifact to an outer artifact event."""
        artifact = Artifact(
            artifact_id=_text(event.get("artifact_id")) or str(uuid.uuid4()),
            name=_text(event.get("name")) or "report",
        )
        data = event.get("data")
        if isinstance(data, Mapping):
            part = Part()
            ParseDict(dict(data), part.data)
            artifact.parts.add().CopyFrom(part)
        text = _text(event.get("text"))
        if text:
            artifact.parts.add(text=text)
        if not artifact.parts:
            return None
        return TaskArtifactUpdateEvent(
            task_id=self._context.outer_task_id,
            context_id=self._context.outer_context_id,
            artifact=artifact,
            last_chunk=True,
            metadata=self._metadata(event, "report"),
        )

    def _metadata(self, event: Mapping[str, object] | None, phase: str) -> dict[str, object]:
        """Build the sample's hop/pure metadata of one outer event."""
        pure: dict[str, object] = {}
        if event is not None:
            raw = event.get("metadata")
            if isinstance(raw, Mapping) and isinstance(raw.get("pure"), Mapping):
                pure.update(dict(raw["pure"]))
        pure.update({"hop": self._context.downstream_hop, "phase": phase})
        return {"pure": pure, "hop": self._context.downstream_hop}

    async def _persist(self, event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent) -> None:
        """Persist one outer event through the same TaskStore the handler uses."""
        if self._manager is None:
            self._manager = TaskManager(
                self._task_store,
                self._context.call_context,
                self._context.outer_task_id,
                self._context.outer_context_id,
                None,
            )
        await self._manager.process(event)

    async def _push(self, event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent) -> None:
        """Send one A2A push notification when a sender is configured."""
        if self._push_sender is None:
            return
        try:
            await self._push_sender.send_notification(self._context.outer_task_id, event)
        except Exception:  # noqa: BLE001 - push is best effort; polling is the fallback
            _LOGGER.warning("downstream push notification failed", exc_info=True)

    def _trace(self, event: Mapping[str, object]) -> None:
        """Emit one Run Explorer span for a late downstream event."""
        kind = str(event.get("kind") or "event")
        state = _text(event.get("state"))
        title = f"{self._context.downstream_name} {state or kind}"
        record_span(
            kind="status" if kind == "status" else kind,
            phase=infer_event_phase(event),
            title=title,
            prefix="pump",
            actor="nms",
            side="oss",
            detail=dict(event),
        )

    def _is_terminal_event(self, event: Mapping[str, object]) -> bool:
        """Whether one downstream event ends the tracked Task."""
        kind = str(event.get("kind") or "")
        if kind == "status":
            return str(event.get("state") or "") in _TERMINAL_STATES
        return kind == "artifact" and is_report_artifact(event)

    async def _mark_terminal(self, event: Mapping[str, object]) -> None:
        """Mark the durable binding terminal after the outer Task was updated."""
        try:
            await self._binding_store.mark_terminal(
                self._context.inner_task_id,
                self._context.inner_context_id,
                event_hash=_event_hash(event),
            )
        except Exception:  # noqa: BLE001 - persistence must not break the tracking
            _LOGGER.warning("failed to mark the downstream binding terminal", exc_info=True)

    def _is_duplicate(self, event: dict[str, object]) -> bool:
        """Drop an exact webhook retry of the last event."""
        fingerprint = _event_hash(event)
        if fingerprint in self._recent:
            return True
        self._recent.append(fingerprint)
        return False


def _map_status(state: str) -> tuple[TaskState, str, str]:
    """Map one downstream Task state to the outer state, default text and phase."""
    if state == "TASK_STATE_COMPLETED":
        return TaskState.TASK_STATE_COMPLETED, "The {name} task completed.", "report"
    if state == "TASK_STATE_REJECTED":
        return TaskState.TASK_STATE_REJECTED, "The {name} rejected the request.", "resolution"
    if state in ("TASK_STATE_FAILED", "TASK_STATE_CANCELED"):
        return TaskState.TASK_STATE_FAILED, f"The {{name}} task ended with {state}.", "resolution"
    return TaskState.TASK_STATE_WORKING, "The {name} task is working.", "execution"


def _text(value: object) -> str | None:
    """Return a non-empty string value, or ``None``."""
    return value if isinstance(value, str) and value else None


def _status_text(status: Mapping[str, object]) -> str:
    """Return the first text part of a Task status message."""
    message = status.get("message")
    if not isinstance(message, Mapping):
        return ""
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts:
        return ""
    first = parts[0]
    if isinstance(first, Mapping):
        text = first.get("text")
        if isinstance(text, str):
            return text
    return ""


def _artifact_event_from_document(
    raw: object,
    *,
    task_id: str,
    context_id: str | None,
) -> dict[str, object] | None:
    """Render one Task-document artifact as a normalized downstream event."""
    if not isinstance(raw, Mapping):
        return None
    event: dict[str, object] = {
        "kind": "artifact",
        "task_id": task_id,
        "context_id": context_id or "",
        "artifact_id": str(raw.get("artifactId") or raw.get("artifact_id") or ""),
        "name": str(raw.get("name") or ""),
    }
    parts = raw.get("parts")
    if isinstance(parts, list) and parts and isinstance(parts[0], Mapping):
        first = parts[0]
        if isinstance(first.get("data"), Mapping):
            event["data"] = dict(first["data"])
        elif isinstance(first.get("text"), str):
            event["text"] = first["text"]
    return event


async def _answer_for(
    event: dict[str, object],
    answer: Callable[[dict[str, object]], str | Awaitable[str]] | None,
) -> str:
    """Return the follow-up text for one negotiation event (sync or async callback)."""
    if answer is None:
        proposal = event.get("text")
        return proposal if isinstance(proposal, str) else ""
    result = answer(event)
    if inspect.isawaitable(result):
        result = await result
    return result if isinstance(result, str) else ""


def _follow_up_request(
    text: str,
    *,
    task_id: str | None,
    context_id: str | None,
    followup: str,
) -> Any:
    """Build the continuation message of one delayed negotiation."""
    from protocol.pure_client_flow import _follow_up_request as build_follow_up

    return build_follow_up(text, task_id=task_id, context_id=context_id, followup=followup)


def _event_hash(event: Mapping[str, object]) -> str:
    """Return a stable fingerprint of one normalized downstream event."""
    rendered = json.dumps(dict(event), sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


__all__ = [
    "DownstreamPump",
    "DownstreamPumpContext",
    "PumpSubscription",
    "PumpTransport",
]
