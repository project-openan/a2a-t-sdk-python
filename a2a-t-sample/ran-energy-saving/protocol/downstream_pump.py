"""Background tracker of downstream NMS events for a long-running OSS Task.

The OSS Agent forwards one request to the NMS and then waits for a Task that may run for days. When
the synchronous flow cannot stay in the foreground (no stream exists, the caller disconnected, or
the flow ended with nothing left to read), this pump keeps consuming the run's webhook queue and
applies every later event to the outer A2A Task:

* the outer Task is persisted through ``TaskManager`` (SQLite in a real run);
* the client receives the same event as an A2A push notification (polling is the reconnect
  fallback);
* a Run Explorer span is emitted with the run id so Langfuse keeps the late execution;
* a delayed ``INPUT_REQUIRED`` is relayed to the caller and the pump suspends.

The pump never answers a vendor question. It has no decision callback at all: a background tracker
that answers is how a rejected value gets resent until the vendor returns
``TASK_STREAM_CONFLICT``. The caller answers (a confirm card, or its own automatic policy) and that
answer resumes the downstream context in the synchronous flow.

The pump is best effort: a malformed push or a failed push notification never stops the tracking.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol

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
from shared.run_constants import reports_failed_completion
from shared.trace import infer_event_phase, record_span, trace_scope

from protocol.client_flow import is_report_artifact, normalize_webhook_payload
from protocol.downstream_binding_store import DownstreamBindingStore
from protocol.manual_answer import ungrounded_answer_fields
from protocol.negotiation_policy import negotiation_kind_for_propose_content
from protocol.pure_client_flow import is_plain_question

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
        max_rounds: Negotiation round budget reported on the relayed question.
        call_context: Request context owning the outer Task (for ``TaskManager`` persistence).
    """

    run_id: str
    inner_task_id: str
    outer_task_id: str
    downstream_name: str
    max_rounds: int
    call_context: ServerCallContext
    inner_context_id: str = ""
    outer_context_id: str = ""
    downstream_hop: str = "nms"
    #: Texts this run already sent downstream, oldest first. The relayed card uses it to suppress
    #: a recommendation that only repeats a value the vendor already rejected.
    sent_texts: list[str] = field(default_factory=list)


class DownstreamPump:
    """Consume one run's webhook queue until the downstream Task reaches a terminal state."""

    def __init__(
        self,
        *,
        context: DownstreamPumpContext,
        subscription: PumpSubscription,
        task_store: TaskStore,
        binding_store: DownstreamBindingStore,
        push_sender: PushNotificationSender | None = None,
        recommendation: Callable[[dict[str, object]], Awaitable[dict[str, object] | None]] | None = None,
        on_question: Callable[[Mapping[str, object], int], None] | None = None,
    ) -> None:
        """Create one pump.

        The pump only tracks: it consumes pushes, writes the outer Task and relays every vendor
        question. It never answers one. The caller owns the answer (a confirm card or its own
        automatic policy), which is what keeps the tester's OSP batches and the A2A contract
        intact, and what removes the whole class of ``the pump decided for the operator`` bugs.

        Args:
            context: Correlation and policy values of the tracked Task.
            subscription: The run's webhook queue and unsubscribe hook.
            task_store: Outer Task store shared with the A2A request handler.
            binding_store: Durable binding used by restart recovery and retention.
            push_sender: Optional A2A push-notification sender.
            recommendation: Advisory-answer callback for one vendor text question; ``None`` keeps
                the question as a plain status update.
            on_question: Hook registering the relayed question so the operator answer can resume
                the downstream context.
        """
        self._context = context
        self._subscription = subscription
        self._task_store = task_store
        self._binding_store = binding_store
        self._push_sender = push_sender
        self._recommendation = recommendation
        self._on_question = on_question
        self._terminal = False
        self._suspended = False
        self._rounds = 0
        self._sent_texts = context.sent_texts
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
                while not self._terminal and not self._suspended:
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
        # The recommendation is advisory card text, not a precondition: an offline or restarted
        # OSS has no recommendation builder, and the vendor question must still reach the caller.
        if is_plain_question(event) and self._on_question is not None:
            await self._relay_question(event)
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
        """Relay a delayed negotiation request; the pump never answers one.

        A background tracker has no authority to take a business decision, and an automatic
        answer it invents is exactly the failure mode this class used to produce (a rejected value
        resent until the vendor answered ``TASK_STREAM_CONFLICT``). The question becomes an outer
        ``INPUT_REQUIRED`` and the pump suspends; the caller answers it (a confirm card, or its own
        automatic policy) and that answer resumes the downstream context in the synchronous flow.
        """
        await self._relay_question(event)

    async def _relay_question(
        self,
        event: Mapping[str, object],
        *,
        conflict: str = "",
        retry_after: float | None = None,
    ) -> None:
        """Publish one vendor text question as an outer ``INPUT_REQUIRED`` and stop the pump.

        The operator answer resumes the downstream context in a new request, which creates its own
        subscription and binds the same vendor identity. This pump releases the queue instead of
        competing with that subscription; an incomplete answer hands the Task back to a new pump.
        """
        text = _text(event.get("text")) or f"The {self._context.downstream_name} requested information."
        recommendation: dict[str, object] | None = None
        if self._recommendation is not None:
            try:
                recommendation = await self._recommendation(dict(event))
            except Exception:  # noqa: BLE001 - the operator can still answer manually
                _LOGGER.warning("could not draft an answer to the downstream question", exc_info=True)
        suppressed = ""
        if isinstance(recommendation, Mapping):
            filled = recommendation.get("filledParams")
            if (
                isinstance(filled, Mapping)
                and filled
                and self._sent_texts
                and not ungrounded_answer_fields("\n".join(self._sent_texts), dict(filled))
            ):
                # The card must not offer a value the vendor already rejected.
                _LOGGER.info("suppressed a pump recommendation that repeats an already-sent value")
                recommendation = None
                suppressed = "already_sent"
        round_number = self._rounds + 1
        metadata = self._metadata(event, "question")
        pure = metadata.get("pure")
        if not isinstance(pure, dict):
            pure = {}
            metadata["pure"] = pure
        # A real-NMS question carries no Negotiation-T metadata. Its typed heading is the only
        # kind signal; without this a feasibility card is rendered as an information proposal.
        kind = str(pure.get("negotiationKind") or "information")
        heading_kind = negotiation_kind_for_propose_content(text)
        if heading_kind is not None:
            kind = heading_kind.value
        pure.update(
            {
                "kind": "negotiation",
                "negotiationKind": kind,
                "round": round_number,
                "maxRounds": self._context.max_rounds,
                "recommendation": recommendation or None,
            }
        )
        if suppressed:
            pure["recommendationSuppressed"] = suppressed
        if conflict:
            pure["conflict"] = conflict
            if retry_after is not None:
                pure["retryAfterSeconds"] = retry_after
        outer_event = self._status_message(
            TaskState.TASK_STATE_INPUT_REQUIRED,
            text,
            "question",
            metadata,
        )
        await self._persist(outer_event)
        await self._push(outer_event)
        self._trace(event)
        self._rounds = round_number
        if self._on_question is not None:
            self._on_question(dict(event), round_number)
        self._suspended = True

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
        downstream_text = _text(event.get("text")) or ""
        outer_state, default_text, phase = _map_status(state, downstream_text)
        text = downstream_text or default_text.format(name=self._context.downstream_name)
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
        """Build one outer status event with an agent message.

        The metadata is written on the status Message as well as on the event. ``TaskManager``
        stores event metadata on ``Task.metadata``, while the client poller reads only
        ``status.message.metadata`` (``protocol/client_service/run_manager.py``): without the copy a
        pump-relayed question loses its round/kind on the polling path, and the page's question
        de-duplication collapses to round 0.
        """
        message = Message(message_id=str(uuid.uuid4()), role=Role.ROLE_AGENT)
        message.parts.add(text=text)
        if metadata:
            ParseDict(dict(metadata), message.metadata)
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
        """Build the sample's hop/pure metadata of one outer event.

        Every outer event carries the far-side Task identity, so a client that only sees the outer
        Task can still query the NMS/RAN Agent by its own task id and context id.
        """
        pure: dict[str, object] = {}
        if event is not None:
            raw = event.get("metadata")
            if isinstance(raw, Mapping) and isinstance(raw.get("pure"), Mapping):
                pure.update(dict(raw["pure"]))
        pure.update({"hop": self._context.downstream_hop, "phase": phase})
        if self._context.inner_task_id:
            pure["downstreamTaskId"] = self._context.inner_task_id
        if self._context.inner_context_id:
            pure["downstreamContextId"] = self._context.inner_context_id
        pure["downstreamHop"] = self._context.downstream_hop
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


def _map_status(state: str, text: str = "") -> tuple[TaskState, str, str]:
    """Map one downstream Task state to the outer state, default text and phase.

    A report artifact already terminated the pump before a status can arrive here, so a
    ``COMPLETED`` that carries a refusal sentence is the vendor's non-standard failure signal and
    must not be relayed as an outer success (W-PURE-3).
    """
    if state == "TASK_STATE_COMPLETED":
        if reports_failed_completion(text):
            return TaskState.TASK_STATE_FAILED, "The {name} could not complete the request.", "resolution"
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


def _event_hash(event: Mapping[str, object]) -> str:
    """Return a stable fingerprint of one normalized downstream event."""
    rendered = json.dumps(dict(event), sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


__all__ = [
    "DownstreamPump",
    "DownstreamPumpContext",
    "PumpSubscription",
]
