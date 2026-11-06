"""A2A client flow of the RAN energy-saving sample (SSE and webhook, multi-round negotiation).

The flow owns the protocol correctness of the client side:

* it generates the initial Task-T prompt with the SDK ``fromText`` API and sends it as one A2A
  ``Message`` (the text part is the short request label; the prompt travels under ``Task-T/v1``);
* it consumes the response events — the SSE stream or the webhook pushes plus the non-blocking RPC
  reply — through one normalized event shape;
* whenever the server sends a pre-task ``Propose``, it asks the configured
  :class:`~protocol.decision_provider.NegotiationDecisionProvider` for a decision and renders the
  matching protocol message with the SDK ``fromText`` generators;
* an ``accept`` refills the Task-T prompt with ``fromDataWithSchema``, a ``counter`` keeps the
  inbound round and carries ``metadata["sampleProposal"]``, and ``reject`` / ``abort`` are terminal;
* the server owns round counting: the client always uses ``session.with_performative(...)`` and
  never calls ``next_round()``.

A negotiation ends on a plain end ``Message`` ("协商已结束"), a server ``Abort`` message, or a
terminal Task state. Reject and abort never create a Task.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Final, Protocol

from a2a.client.client import ClientCallContext
from a2a.types import (
    CancelTaskRequest,
    Role,
    SendMessageRequest,
    StreamResponse,
    TaskPushNotificationConfig,
    TaskState,
)
from a2a_t.core.errors.exceptions import ContentValidationError
from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    TASK_T_EXTENSION_URI,
    TEMPLATE_URI_METADATA_KEY,
    MetadataContent,
    NegotiationContext,
    NegotiationPerformative,
)
from a2a_t.core.standard_templates import (
    FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI,
    FEASIBILITY_NEGOTIATION_PROPOSE_URI,
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
    NEGOTIATION_ABORT_URI,
    TARGET_NEGOTIATION_ACCEPT_REJECT_URI,
    TARGET_NEGOTIATION_PROPOSE_URI,
)
from google.protobuf.json_format import ParseDict
from shared.config import DEFAULT_HTTP_TIMEOUT_SECONDS, DEFAULT_WEBHOOK_IDLE_TIMEOUT
from shared.logging_setup import log_context
from shared.models import NegotiationDecision, NegotiationOptions, NegotiationProposeView
from shared.run_constants import (
    FINAL_STATE_INPUT_INVALID,
    FINAL_STATE_INTENT_NOT_CONFIRMED,
    FINAL_STATE_NEGOTIATION_ABORTED,
    FINAL_STATE_NEGOTIATION_ENDED,
    FINAL_STATE_TASK_COMPLETED,
    LANGUAGE_ZH,
    RUN_MODE_CREATE,
    RUN_MODE_MODIFY,
)
from shared.sse_event_consumer import normalize_event
from shared.task_schema import TASK_PARAM_SCHEMA
from shared.trace import (
    PHASE_NEGOTIATION,
    PHASE_RESOLUTION,
    PHASE_TASK_MODIFY,
    PHASE_TASK_PROMPT,
    PHASE_TASK_VALIDATION,
    TraceRecorder,
    current_trace,
    infer_decision_phase,
    is_report_artifact,
    trace_stage,
)

from protocol.decision_provider import NegotiationDecisionProvider
from protocol.messages import (
    END_MESSAGE_TEXT,
    SAMPLE_NEGOTIATION_OPTIONS_METADATA_KEY,
    SAMPLE_PHASE_BASELINE,
    SAMPLE_PHASE_METADATA_KEY,
    SAMPLE_PHASE_MODIFY,
    SAMPLE_PROPOSAL_METADATA_KEY,
    TASK_TEMPLATE_URI,
    is_negotiation_message,
    metadata_to_dict,
    negotiation_context_metadata,
    parse_negotiation_context,
)
from protocol.negotiation_policy import (
    NegotiationKind,
    negotiation_kind_for_errors,
    negotiation_kind_for_template_uri,
)
from protocol.run_spec import DEFAULT_CLIENT_POLICY, ClientPolicy

_LOGGER = logging.getLogger("ran_energy_saving.client_flow")

#: Short label carried in the text part of every message (the protocol content lives in metadata).
TASK_REQUEST_LABEL: Final[str] = "create ran energy saving task"

#: Short label of the follow-up Task-T that modifies an existing Task.
TASK_MODIFY_LABEL: Final[str] = "modify ran energy saving task"

#: Final state when a terminal Task state other than COMPLETED is reached.
FINAL_STATE_TASK_REJECTED: Final[str] = "task_rejected"
FINAL_STATE_TASK_FAILED: Final[str] = "task_failed"

#: Final state when the event stream ends without a terminal event.
FINAL_STATE_INCOMPLETE: Final[str] = "incomplete"

#: Terminal Task states of the A2A SDK request handler.
_TERMINAL_STATE_NAMES: Final[frozenset[str]] = frozenset(
    TaskState.Name(state)
    for state in (
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_REJECTED,
    )
)

#: Default maximum number of client sends; a backstop against a server that never terminates.
DEFAULT_MAX_EXCHANGES: Final[int] = 16

#: Default HTTP budget of one A2A send; the client runtime passes the configured value. It reuses
#: the sample configuration default so the flow and the httpx client cannot drift.
DEFAULT_CALL_TIMEOUT_SECONDS: Final[float] = DEFAULT_HTTP_TIMEOUT_SECONDS

#: Negotiation-T accept/reject template URI per kind.
_ACCEPT_REJECT_TEMPLATE_URI_BY_KIND: Final[dict[str, str]] = {
    NegotiationKind.INFORMATION.value: INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    NegotiationKind.TARGET.value: TARGET_NEGOTIATION_ACCEPT_REJECT_URI,
    NegotiationKind.FEASIBILITY.value: FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI,
}

#: Negotiation-T propose template URI per kind.
_PROPOSE_TEMPLATE_URI_BY_KIND: Final[dict[str, str]] = {
    NegotiationKind.INFORMATION.value: INFORMATION_NEGOTIATION_PROPOSE_URI,
    NegotiationKind.TARGET.value: TARGET_NEGOTIATION_PROPOSE_URI,
    NegotiationKind.FEASIBILITY.value: FEASIBILITY_NEGOTIATION_PROPOSE_URI,
}

#: Marker of the server's information-propose text that lists the missing parameter names.
_MISSING_ITEMS_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:missing items?|provide the following items?)\s*:\s*([^.]+)\.", re.IGNORECASE
)

#: A Task-T throughput target must be time-segmented, for example ``{00:00~07:00,2Mbps}``.
_TIME_SEGMENTED_TARGET_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\d{2}:\d{2}~\d{2}:\d{2}\s*,\s*\d+(?:\.\d+)?\s*Mbps", re.IGNORECASE
)

#: Heading opening the Task-T context section (the template writes both language labels).
_CONTEXT_HEADING_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^##\s+[^\n]*(?:任务上下文|Task Context)[^\n]*$", re.MULTILINE
)

#: Client-side errors.
_ERROR_NO_TEMPLATE_URI: Final[str] = "The negotiation propose carries no template URI."
_ERROR_NO_SESSION: Final[str] = "The negotiation propose carries no valid negotiationContext."
_ERROR_UNKNOWN_KIND: Final[str] = "The negotiation propose template is not a sample negotiation template."
_ERROR_NO_PROPOSE: Final[str] = "The server sent a negotiation message that is not a Propose or Abort."


class ClientFlowError(RuntimeError):
    """Raised when the server sends content the client flow cannot handle."""


#: SDK validation codes that mean the validator itself failed rather than the content being invalid.
#: Those calls degrade to ``unavailable`` so a model outage never blocks a demo run.
_VALIDATION_UNAVAILABLE_CODES: Final[frozenset[str]] = frozenset(
    {
        "llm.not_configured",
        "llm.invocation_failed",
        "llm.response_invalid",
        "template.load_failed",
        "infra.resource_read_failed",
        "infra.internal_error",
    }
)

#: Deterministic validation failures the user can act on; they block regardless of the LLM verdict.
_VALIDATION_BLOCKING_CODES: Final[frozenset[str]] = frozenset(
    {
        "input.text_too_long",
        "template.not_found",
        "negotiation.invalid_input",
    }
)


@dataclass(frozen=True)
class TaskValidation:
    """Outcome of one pre-send Task-T validation call."""

    verdict: str
    negotiation_kind: NegotiationKind | None
    errors: list[dict[str, object]]
    reason: str | None
    duration_ms: int


def _elapsed_ms(started: float) -> int:
    """Return the whole milliseconds elapsed since ``started``."""
    return max(0, int((time.monotonic() - started) * 1000))


def _validation_errors(error: ContentValidationError) -> list[dict[str, object]]:
    """Render the SDK slot errors as JSON-ready documents for the trace and the UI."""
    return [
        {
            "slotName": item.slot_name,
            "code": str(item.code),
            "message": item.message,
            "facts": dict(item.facts or {}),
        }
        for item in error.errors or []
    ]


def _classify_validation_error(
    error: ContentValidationError,
) -> tuple[str, NegotiationKind | None]:
    """Map one SDK validation failure to ``passed``-adjacent, negotiable or blocking."""
    code = error.code_str
    if code in _VALIDATION_UNAVAILABLE_CODES:
        return "unavailable", None
    if code in _VALIDATION_BLOCKING_CODES:
        return "blocked", None
    kind = negotiation_kind_for_errors(error.errors)
    if kind is not None:
        # The server maps the same errors to a negotiation kind, so the request must still be sent.
        return "negotiable", kind
    return "blocked", None


async def _validate_task_prompt(
    prompt_client: PromptClient,
    prompt: str,
    schema: Mapping[str, object],
    template_uri: str,
) -> TaskValidation:
    """Run the SDK content validation without blocking the event loop."""
    started = time.monotonic()
    try:
        await asyncio.to_thread(
            prompt_client.validate_task_prompt_and_data_filling,
            prompt,
            schema,
            template_uri,
        )
    except ContentValidationError as error:
        verdict, kind = _classify_validation_error(error)
        return TaskValidation(verdict, kind, _validation_errors(error), error.code_str, _elapsed_ms(started))
    except Exception as error:  # noqa: BLE001 - validation must never break the send path
        _LOGGER.warning("Task-T validation unavailable: %s", error)
        reason = f"{type(error).__name__}: {error}"
        return TaskValidation("unavailable", None, [], reason, _elapsed_ms(started))
    return TaskValidation("passed", None, [], None, _elapsed_ms(started))


class PromptClient(Protocol):
    """The ``A2ATClient`` surface the flow uses (also implemented by test fakes)."""

    def generate_task_prompt_from_text(self, text: str, template_uri: str) -> MetadataContent: ...

    def generate_task_prompt_from_data_with_schema(
        self,
        data: Mapping[str, object],
        schema: Mapping[str, object],
        template_uri: str,
    ) -> MetadataContent: ...

    def generate_negotiation_accept_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent: ...

    def generate_negotiation_reject_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent: ...

    def generate_negotiation_abort_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent: ...

    def generate_negotiation_propose_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent: ...

    def validate_task_prompt_and_data_filling(
        self,
        prompt: str,
        schema: Mapping[str, object],
        template_uri: str,
    ) -> object: ...


class SendMessageClient(Protocol):
    """Minimal a2a-sdk client surface: one async-iterator ``send_message`` call per exchange."""

    def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext,
    ) -> AsyncIterator[StreamResponse]: ...


#: Observer invoked immediately after a provider returns its decision (Run Explorer §6).
DecisionMadeCallback = Callable[[NegotiationDecision, NegotiationProposeView, NegotiationContext], None]


class ClientTransport(Protocol):
    """Send one request and return its normalized events up to the first stop point."""

    async def send(
        self,
        request: SendMessageRequest,
        call_context: ClientCallContext,
    ) -> list[dict[str, object]]: ...


@dataclass
class ClientFlowResult:
    """Outcome of one client flow run.

    Attributes:
        events: Every normalized event received, in arrival order.
        final_state: Terminal state name (``task_completed``, the two no-task states, a task
            failure name, or ``incomplete``).
        decisions: Decisions returned by the provider, in order.
        context_id: A2A context id of the conversation, when observed.
        task_id: Task id of the created task, when one was created.
        report: Last report artifact payload/text of a completed task, when observed.
        reports: Report artifact payloads in arrival order (a two-phase run has a baseline report
            and the modified report).
    """

    events: list[dict[str, object]] = field(default_factory=list)
    final_state: str = FINAL_STATE_INCOMPLETE
    decisions: list[NegotiationDecision] = field(default_factory=list)
    context_id: str | None = None
    task_id: str | None = None
    report: dict[str, object] | None = None
    reports: list[dict[str, object]] = field(default_factory=list)


def is_terminal_event(event: Mapping[str, object]) -> bool:
    """Report whether one normalized event is a terminal Task state."""
    return event.get("kind") == "status" and event.get("state") in _TERMINAL_STATE_NAMES


def is_stop_event(event: Mapping[str, object]) -> bool:
    """Report whether one normalized event ends a send phase (message or terminal state)."""
    return event.get("kind") == "message" or is_terminal_event(event)


def normalize_webhook_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Convert one webhook push JSON body into the normalized event shape.

    The receiver hands over the ``MessageToDict`` of a ``StreamResponse``; parsing it back into the
    protobuf type keeps the SSE and webhook paths on exactly one normalization implementation.
    """
    try:
        stream_response = StreamResponse()
        ParseDict(dict(payload), stream_response)
        return normalize_event(stream_response)
    except Exception as exc:
        raise ClientFlowError(f"Unsupported webhook payload: {exc}") from exc


class SseClientTransport:
    """SSE transport: stream one ``message:send`` and stop at the first message/terminal event.

    The optional event sink is invoked for every normalized event as soon as the stream yields it,
    which lets the Web UI show a round while the flow is still waiting for the next one.
    """

    def __init__(self, client: SendMessageClient) -> None:
        """Create one SSE transport over an a2a-sdk client."""
        self._client = client
        self._event_sink: Callable[[dict[str, object]], None] | None = None

    def set_event_sink(self, event_sink: Callable[[dict[str, object]], None] | None) -> None:
        """Attach the live event sink used by :func:`run_client_flow`."""
        self._event_sink = event_sink

    def _emit(self, event: dict[str, object]) -> None:
        """Forward one normalized event to the attached sink, when any."""
        if self._event_sink is not None:
            self._event_sink(event)

    async def send(
        self,
        request: SendMessageRequest,
        call_context: ClientCallContext,
    ) -> list[dict[str, object]]:
        """Send one message and consume its stream until a stop point."""
        events: list[dict[str, object]] = []
        async for stream_response in self._client.send_message(request, context=call_context):
            event = normalize_event(stream_response)
            events.append(event)
            self._emit(event)
            if is_stop_event(event):
                break
        _LOGGER.debug("sse phase finished: events=%d", len(events))
        return events

    async def cancel(self, task_id: str, call_context: ClientCallContext | None = None) -> None:
        """Cancel the named Task (used when a two-phase run cannot send its Modify)."""
        await _cancel_task(self._client, task_id, call_context)


class WebhookClientTransport:
    """Webhook transport: non-blocking send with inline push registration.

    The RPC reply is inspected first because a pre-task negotiation message is never pushed; task
    events then arrive on the embedded receiver's queue. The idle timeout is the backstop that ends
    a phase when the server pushes nothing.
    """

    def __init__(
        self,
        client: SendMessageClient,
        *,
        queue: asyncio.Queue[dict[str, object]],
        push_url: str,
        push_token: str,
        idle_timeout: float = float(DEFAULT_WEBHOOK_IDLE_TIMEOUT),
        bind: Callable[[str | None, str | None], None] | None = None,
        context_id: str = "",
    ) -> None:
        """Create one webhook transport.

        Args:
            client: a2a-sdk client whose ``send_message`` is an async generator.
            queue: Queue fed by the webhook router for this run only.
            push_url: Receiver callback URL registered inline on every request.
            push_token: Push token checked by the receiver.
            idle_timeout: Backstop timeout waiting for pushed events.
            bind: Optional callback binding ``(task_id, context_id)`` to this run's queue. The
                router uses it to demultiplex a process-level receiver; a push that arrives before
                the RPC reply is buffered and flushed when the bind lands.
            context_id: Optional client-assigned A2A context id. Setting it before the first send
                makes every pushed event routable from its ``contextId`` alone, even when the
                server answers with a bare message instead of a Task.
        """
        self._client = client
        self._queue = queue
        self._push_url = push_url
        self._push_token = push_token
        self._idle_timeout = idle_timeout
        self._bind = bind
        self._context_id = context_id
        self._event_sink: Callable[[dict[str, object]], None] | None = None

    def set_event_sink(self, event_sink: Callable[[dict[str, object]], None] | None) -> None:
        """Attach the live event sink used by :func:`run_client_flow`."""
        self._event_sink = event_sink

    def _emit(self, event: dict[str, object]) -> None:
        """Forward one normalized event to the attached sink, when any."""
        if self._event_sink is not None:
            self._event_sink(event)

    def _bind_context(self, request: SendMessageRequest) -> None:
        """Stamp the run's A2A context on the message and bind it before the request leaves.

        The server echoes the context on every pushed event, so binding before the send removes
        the pre-reply window for the context-keyed subset. The task id is still bound from the
        reply, which is the stronger key for status/artifact events.
        """
        if not self._context_id:
            return
        if not request.message.context_id:
            request.message.context_id = self._context_id
        if self._bind is not None:
            self._bind(None, request.message.context_id)

    async def send(
        self,
        request: SendMessageRequest,
        call_context: ClientCallContext,
    ) -> list[dict[str, object]]:
        """Send one non-blocking message, register push inline, and collect its events."""
        self._bind_context(request)
        request.configuration.return_immediately = True
        request.configuration.task_push_notification_config.CopyFrom(
            TaskPushNotificationConfig(url=self._push_url, token=self._push_token)
        )
        events: list[dict[str, object]] = []
        reply: StreamResponse | None = None
        async for stream_response in self._client.send_message(request, context=call_context):
            reply = stream_response
        if reply is not None:
            reply_event = normalize_event(reply)
            events.append(reply_event)
            self._emit(reply_event)
            if self._bind is not None:
                # The router can only route this run's pushes once it knows the identities the
                # server assigned to it; bind them before waiting on the shared receiver.
                self._bind(
                    _string_field(reply_event, "task_id"),
                    _string_field(reply_event, "context_id"),
                )
            if is_stop_event(reply_event):
                return events
        while True:
            try:
                payload = await asyncio.wait_for(self._queue.get(), timeout=self._idle_timeout)
            except TimeoutError:
                _LOGGER.info("webhook phase idle for %gs; treating it as complete", self._idle_timeout)
                return events
            event = normalize_webhook_payload(payload)
            events.append(event)
            self._emit(event)
            # The report artifact ends a webhook phase: the underlying energy-saving task is
            # long-running and stays WORKING after it (design §7.1 / old webhook parity). The ADK
            # executor also emits one artifact per tool call, so only the named/stamped report
            # artifact ends the phase; stopping at the first tool artifact would lose the report.
            if is_stop_event(event) or is_report_artifact(event):
                return events

    async def cancel(self, task_id: str, call_context: ClientCallContext | None = None) -> None:
        """Cancel the named Task (used when a two-phase run cannot send its Modify)."""
        await _cancel_task(self._client, task_id, call_context)


async def _cancel_task(
    client: SendMessageClient,
    task_id: str,
    call_context: ClientCallContext | None,
) -> None:
    """Cancel one Task through the SDK client, failing clearly when it cannot."""
    cancel_task = getattr(client, "cancel_task", None)
    if cancel_task is None:
        raise ClientFlowError("The A2A client does not support cancelling a Task.")
    await cancel_task(CancelTaskRequest(id=task_id), context=call_context)


async def run_client_flow(
    *,
    prompt_client: PromptClient,
    transport: ClientTransport,
    user_request: str,
    provider: NegotiationDecisionProvider,
    label: str = TASK_REQUEST_LABEL,
    task_template_uri: str = TASK_TEMPLATE_URI,
    language: str = LANGUAGE_ZH,
    policy: ClientPolicy = DEFAULT_CLIENT_POLICY,
    max_exchanges: int = DEFAULT_MAX_EXCHANGES,
    call_timeout_seconds: float = DEFAULT_CALL_TIMEOUT_SECONDS,
    event_sink: Callable[[dict[str, object]], None] | None = None,
    on_decision_requested: Callable[[NegotiationProposeView, NegotiationContext], None] | None = None,
    on_decision_made: DecisionMadeCallback | None = None,
    on_intent_ready: Callable[[str], Awaitable[bool]] | None = None,
    trace_sink: Callable[[dict[str, object]], None] | None = None,
    conversation_context: str | None = None,
    single_extension_per_message: bool = False,
    forced_region: str | None = None,
    validate_before_send: bool = False,
    run_mode: str = RUN_MODE_CREATE,
    target_task_id: str | None = None,
    target_context_id: str | None = None,
) -> ClientFlowResult:
    """Run one complete client flow: initial Task-T, multi-round negotiation, task or end.

    Args:
        prompt_client: A2A-T SDK client facade (or a test double).
        transport: SSE or webhook transport.
        user_request: Natural-language task request.
        provider: Negotiation decision provider.
        label: Short text part of every message.
        task_template_uri: Task-T template the initial and accept prompts address.
        language: Process-level language of the refilled Task-T values.
        max_exchanges: Backstop on the number of client sends.
        call_timeout_seconds: HTTP budget of every send, carried in ``ClientCallContext.timeout``.
            It must outlast a live-mode server gate that performs several LLM calls before it
            emits any event.
        event_sink: Optional observer invoked for every normalized event. The SSE and webhook
            transports forward each event as it arrives; a transport without a ``set_event_sink``
            hook has its returned batch forwarded instead, so the observer sees every event exactly
            once.
        on_decision_requested: Optional observer invoked immediately before the provider is asked
            for a decision, carrying the proposal view and its negotiation session (the Web UI
            uses it to display the manual decision form).
        on_decision_made: Optional observer invoked immediately after the provider returns, before
            the response is built or sent. It lets the Web UI and the transcript record a decision
            causally, i.e. before the task events it causes.
        on_intent_ready: Optional async approval gate invoked with the rendered Task-T before the
            initial message is sent. Returning ``False`` ends the run before any task is created.
        trace_sink: Optional observer of synthetic Run Explorer spans (``task_prompt_rendered``,
            ``message_sent``, ``decision_requested``, ``decision_made``). Protocol events keep using
            ``event_sink``; the two streams share the Web UI sequence numbering.
        conversation_context: Optional recent-turn block prepended to the natural-language request
            before Task-T generation, so a follow-up can refer to an earlier run. The wire prompt
            still contains only the current request plus this delimited context.
        single_extension_per_message: When true (the real NMS), an accept carries only the
            Negotiation-T key because that server rejects a message with more than one ``-T``
            extension key. The local demo server instead requires the agreed Task-T on the accept,
            so it keeps the default ``False``.
        forced_region: Region the vendor NMS serves. When set, it replaces the region a decision
            model fills on an accept, so the operator's configured region wins over a model that
            repeats the demo region.
        validate_before_send: Validate each rendered Task-T with the SDK content pipeline before
            sending it. Negotiable errors still go to the server for negotiation; unmapped errors
            end the flow with ``input_invalid`` and no message is sent.
        run_mode: ``create`` starts a new A2A Task; ``modify`` sends the scenario's Modify Task-T
            as the first message so an existing running Task is continued.
        target_task_id: Task the Modify-only run continues; required by ``run_mode="modify"``.
        target_context_id: Context of the target Task; required by ``run_mode="modify"``.

    Returns:
        The collected events, provider decisions and terminal state.

    Raises:
        ClientFlowError: when the server sends an unanswerable negotiation message or the stream
            exceeds the exchange budget.
    """
    transport_emits_events = False
    if event_sink is not None:
        set_event_sink = getattr(transport, "set_event_sink", None)
        if callable(set_event_sink):
            set_event_sink(event_sink)
            transport_emits_events = True

    def collect(batch: list[dict[str, object]]) -> None:
        """Store one send phase's events and forward them when the transport does not."""
        result.events.extend(batch)
        if event_sink is not None and not transport_emits_events:
            for event in batch:
                event_sink(event)

    recorder = TraceRecorder(current_trace().run_id or "", id_prefix="flow")

    def emit_trace(
        kind: str,
        *,
        phase: str,
        title: str,
        direction: str = "internal",
        round_number: int | None = None,
        context_id: str | None = None,
        detail: Mapping[str, object] | None = None,
    ) -> None:
        """Build one synthetic span and forward it to the optional trace sink."""
        if trace_sink is None:
            return
        span = recorder.emit(
            kind=kind,
            phase=phase,
            title=title,
            actor="client",
            side="client",
            direction=direction,
            round=round_number,
            context_id=context_id,
            detail=detail,
        )
        trace_sink(span.to_dict())

    def emit_validation(
        validation: TaskValidation,
        prompt_text: str,
        template_uri: str,
        phase: str,
    ) -> None:
        """Publish one pre-send validation result for the timeline and the presentation card."""
        emit_trace(
            "task_validation",
            phase=phase,
            title=f"Task-T validation {validation.verdict}",
            detail={
                "verdict": validation.verdict,
                "templateUri": template_uri,
                "promptText": prompt_text[:4000],
                "blocking": validation.verdict == "blocked",
                "errors": validation.errors,
                "negotiationKind": (
                    validation.negotiation_kind.value if validation.negotiation_kind is not None else None
                ),
                "durationMs": validation.duration_ms,
                "reason": validation.reason,
            },
        )

    def emit_message_sent(
        request: SendMessageRequest,
        *,
        phase: str,
        title: str,
        performative: str,
        round_number: int | None,
        context_id: str | None,
    ) -> None:
        """Emit one outbound ``message_sent`` span for an A2A request."""
        metadata = metadata_to_dict(request.message.metadata)
        template_uri = metadata.get(TEMPLATE_URI_METADATA_KEY)
        label_text = request.message.parts[0].text if request.message.parts else ""
        metadata_keys = sorted(str(key) for key in metadata)
        extension_uri = next(
            (key for key in metadata_keys if "Task-T" in key),
            None,
        )
        negotiation_uri = next(
            (key for key in metadata_keys if "Negotiation-T" in key),
            None,
        )
        # The refilled Task-T is the authoritative record of the values the client actually sent
        # (the accept decision itself carries no filled params). Keep a bounded copy so the wire
        # view can show the structured payload rather than the short text-part label.
        task_t = next(
            (
                value
                for key, value in metadata.items()
                if isinstance(key, str) and "Task-T" in key and isinstance(value, str)
            ),
            None,
        )
        negotiation_t = next(
            (
                value
                for key, value in metadata.items()
                if isinstance(key, str) and "Negotiation-T" in key and isinstance(value, str)
            ),
            None,
        )
        detail: dict[str, object] = {
            "performative": performative,
            "summary": label_text,
            "metadataKeys": metadata_keys,
            "templateUri": template_uri if isinstance(template_uri, str) else None,
            "extensionUri": extension_uri,
            "negotiationUri": negotiation_uri,
            "messageId": getattr(request.message, "message_id", None),
            "taskId": getattr(request.message, "task_id", None),
        }
        if task_t is not None:
            detail["taskT"] = task_t[:4000]
        if negotiation_t is not None:
            detail["negotiationT"] = negotiation_t[:4000]
        sample_phase = metadata.get(SAMPLE_PHASE_METADATA_KEY)
        if isinstance(sample_phase, str) and sample_phase:
            detail["samplePhase"] = sample_phase
        emit_trace(
            "message_sent",
            phase=phase,
            title=title,
            direction="client->server",
            round_number=round_number,
            context_id=context_id or (request.message.context_id or None),
            detail=detail,
        )

    if run_mode not in (RUN_MODE_CREATE, RUN_MODE_MODIFY):
        raise ClientFlowError(f"Unsupported run mode: {run_mode}")
    modify_only = run_mode == RUN_MODE_MODIFY
    if modify_only and (not target_task_id or not target_context_id):
        raise ClientFlowError(
            "A Modify-only run needs both the target Task id and its context id."
        )
    if not modify_only and (target_task_id or target_context_id):
        raise ClientFlowError(
            "A target Task can only be used by a Modify-only run."
        )
    resolved_label = TASK_MODIFY_LABEL if modify_only else label
    follow_up = None if modify_only else policy.follow_up_data(language)
    with trace_stage("task_t_modify_extraction" if modify_only else "task_t_slot_extraction"):
        initial_data = (
            policy.follow_up_data(language) if modify_only else policy.initial_data(language)
        )
        if modify_only and initial_data is None:
            raise ClientFlowError(
                "The scenario does not define the Task-T values of a Modify-only run."
            )
        if initial_data is not None:
            data, data_schema = initial_data
            validation_schema: Mapping[str, object] = data_schema
            prompt_content = prompt_client.generate_task_prompt_from_data_with_schema(
                data,
                data_schema,
                task_template_uri,
            )
        else:
            validation_schema = TASK_PARAM_SCHEMA
            prompt_content = prompt_client.generate_task_prompt_from_text(
                _request_with_context(user_request, conversation_context),
                task_template_uri,
            )
    prompt_text = _require_prompt_text(prompt_content, "the initial Task-T prompt")
    initial_phase = PHASE_TASK_MODIFY if modify_only else PHASE_TASK_PROMPT
    emit_trace(
        "task_prompt_rendered",
        phase=initial_phase,
        title="Modify Task-T prompt rendered" if modify_only else "Task-T prompt rendered",
        detail={
            "promptText": prompt_text,
            "templateUri": prompt_content.template_uri or task_template_uri,
            "length": len(prompt_text),
            **({"samplePhase": SAMPLE_PHASE_MODIFY} if modify_only else {}),
        },
    )
    if validate_before_send:
        validation = await _validate_task_prompt(prompt_client, prompt_text, validation_schema, task_template_uri)
        emit_validation(validation, prompt_text, task_template_uri, initial_phase)
        if validation.verdict == "blocked":
            # Nothing is sent and no negotiation starts: the operator repairs the request first.
            blocked = ClientFlowResult()
            blocked.final_state = FINAL_STATE_INPUT_INVALID
            _fill_result(blocked, None)
            return blocked
    if on_intent_ready is not None and not await on_intent_ready(prompt_text):
        # The engineer did not confirm the structured intent, so the Task-T never leaves the OSS
        # side. No message is sent and no task is created.
        blocked = ClientFlowResult()
        blocked.final_state = FINAL_STATE_INTENT_NOT_CONFIRMED
        _fill_result(blocked, None)
        return blocked

    initial_metadata: dict[str, object] = {
        prompt_content.extension_uri or TASK_T_EXTENSION_URI: prompt_text,
        TEMPLATE_URI_METADATA_KEY: prompt_content.template_uri or task_template_uri,
    }
    if modify_only:
        initial_metadata[SAMPLE_PHASE_METADATA_KEY] = SAMPLE_PHASE_MODIFY
    elif follow_up is not None:
        # A two-phase run leaves the baseline Task WORKING so the Modify follow-up can continue
        # it. The marker is sample-only and does not change the Task-T extension payload.
        initial_metadata[SAMPLE_PHASE_METADATA_KEY] = SAMPLE_PHASE_BASELINE
    initial_request = _build_message_request(
        metadata=initial_metadata,
        label=resolved_label,
        context_id=target_context_id if modify_only else None,
        task_id=target_task_id if modify_only else None,
    )
    # Declare Negotiation-T alongside Task-T: the vendor NMS only returns a structured negotiation
    # (Negotiation-T metadata plus negotiationContext) when the negotiation extension is declared.
    # Declaring Task-T alone makes it answer with a bare Message whose empty metadata the client
    # cannot distinguish from a negotiation-ended message.
    initial_context = _call_context((TASK_T_EXTENSION_URI, NEGOTIATION_T_EXTENSION_URI), call_timeout_seconds)

    result = ClientFlowResult()
    context_id: str | None = None
    exchanges = 0
    emit_message_sent(
        initial_request,
        phase=PHASE_TASK_MODIFY if modify_only else PHASE_TASK_VALIDATION,
        title="Modify Task-T sent" if modify_only else "Task-T sent",
        performative="TASK_T",
        round_number=None,
        context_id=target_context_id if modify_only else None,
    )
    _LOGGER.info("client flow started: transport=%s", type(transport).__name__)
    batch = await transport.send(initial_request, initial_context)
    exchanges += 1
    collect(batch)
    context_id = context_id or _context_id_of(batch)

    if follow_up is not None:
        follow_task_id = _task_id_of(batch)
        if not follow_task_id:
            raise ClientFlowError("The baseline Task did not expose a task id for the Modify follow-up.")
        try:
            follow_data, follow_schema = follow_up
            with trace_stage("task_t_modify_extraction"):
                follow_content = prompt_client.generate_task_prompt_from_data_with_schema(
                    follow_data,
                    follow_schema,
                    task_template_uri,
                )
            follow_text = _require_prompt_text(follow_content, "the Modify Task-T prompt")
            emit_trace(
                "task_prompt_rendered",
                phase=PHASE_TASK_MODIFY,
                title="Modify Task-T prompt rendered",
                detail={
                    "promptText": follow_text,
                    "templateUri": follow_content.template_uri or task_template_uri,
                    "length": len(follow_text),
                    "samplePhase": SAMPLE_PHASE_MODIFY,
                },
            )
            if validate_before_send:
                validation = await _validate_task_prompt(prompt_client, follow_text, follow_schema, task_template_uri)
                emit_validation(validation, follow_text, task_template_uri, PHASE_TASK_MODIFY)
                if validation.verdict == "blocked":
                    # The baseline Task is still WORKING; cancel it instead of leaving a half-open
                    # Task behind, then end the run without sending the Modify.
                    cancel = getattr(transport, "cancel", None)
                    if callable(cancel):
                        with suppress(Exception):
                            await cancel(follow_task_id, initial_context)
                    blocked = ClientFlowResult()
                    blocked.final_state = FINAL_STATE_INPUT_INVALID
                    _fill_result(blocked, context_id)
                    return blocked
            follow_metadata: dict[str, object] = {
                follow_content.extension_uri or TASK_T_EXTENSION_URI: follow_text,
                TEMPLATE_URI_METADATA_KEY: follow_content.template_uri or task_template_uri,
                SAMPLE_PHASE_METADATA_KEY: SAMPLE_PHASE_MODIFY,
            }
            follow_request = _build_message_request(
                metadata=follow_metadata,
                label=TASK_MODIFY_LABEL,
                context_id=context_id,
                task_id=follow_task_id,
            )
            emit_message_sent(
                follow_request,
                phase=PHASE_TASK_MODIFY,
                title="Modify Task-T sent",
                performative="TASK_T",
                round_number=None,
                context_id=context_id,
            )
            batch = await transport.send(follow_request, initial_context)
        except Exception:
            # The baseline Task is WORKING. If the Modify cannot be generated or sent, cancel it
            # instead of leaving a half-open Task behind for a presenter to discover later.
            cancel = getattr(transport, "cancel", None)
            if callable(cancel):
                with suppress(Exception):
                    await cancel(follow_task_id, initial_context)
            raise
        exchanges += 1
        collect(batch)
        context_id = context_id or _context_id_of(batch)

    def finish(state: str) -> ClientFlowResult:
        """Record the terminal state, fill the result and log the outcome."""
        result.final_state = state
        _fill_result(result, context_id)
        _LOGGER.info(
            "client flow finished: state=%s task_id=%s decisions=%d",
            result.final_state,
            result.task_id or "-",
            len(result.decisions),
        )
        return result

    while True:
        inbound = _find_negotiation_message(batch)
        if inbound is None:
            # Only the last phase can terminate the flow: an earlier round's proposal must not be
            # mistaken for the end message (webhook phases end at the report artifact).
            return finish(_final_state(batch))

        event, metadata = inbound
        context_id = context_id or _string_field(event, "context_id")
        session = parse_negotiation_context(metadata)
        if session is None:
            raise ClientFlowError(_ERROR_NO_SESSION)
        context_id = context_id or session.id

        if session.performative is NegotiationPerformative.ABORT:
            _LOGGER.info("negotiation aborted by the server at round %s", session.round)
            return finish(FINAL_STATE_NEGOTIATION_ABORTED)
        if session.performative is not NegotiationPerformative.PROPOSE:
            raise ClientFlowError(_ERROR_NO_PROPOSE)

        template_uri = _template_uri(metadata)
        kind = negotiation_kind_for_template_uri(template_uri)
        if kind is None:
            raise ClientFlowError(f"{_ERROR_UNKNOWN_KIND} templateUri={template_uri}")
        propose = _propose_view(event, metadata, session, kind, template_uri, context_id)
        emit_trace(
            "decision_requested",
            phase=PHASE_NEGOTIATION,
            title=f"Decision requested · {kind.value} R{session.round}",
            round_number=session.round,
            context_id=context_id,
            detail={
                "negotiationKind": kind.value,
                "round": session.round,
                "maxRounds": session.max_rounds,
                "templateUri": template_uri,
                "content": propose.content,
            },
        )
        _LOGGER.info(
            "proposal received: kind=%s round=%s/%s",
            kind.value,
            session.round,
            session.max_rounds,
        )
        if on_decision_requested is not None:
            on_decision_requested(propose, session)
        with log_context(
            negotiation_id=session.id or context_id,
            round=session.round,
            context_id=context_id,
        ):
            decision = await provider.respond(propose, session)
            decision = await _reask_rejected_region(provider, decision, propose, session, trace_sink=trace_sink)
        _LOGGER.info("decision made: action=%s round=%s", decision.action, session.round)
        # Exactly one timeline row per decision: a consumer callback (Web UI/transcript) records
        # the decision itself, so the synthetic span would be an adjacent duplicate. Without a
        # callback (CLI), the flow emits the span so the decision still reaches the trace.
        if on_decision_made is not None:
            on_decision_made(decision, propose, session)
        else:
            emit_trace(
                "decision_made",
                phase=infer_decision_phase(decision.action),
                title=f"Decision made · {decision.action}",
                round_number=session.round,
                context_id=context_id,
                detail={
                    "action": decision.action,
                    "rationale": decision.rationale,
                    "filledParams": dict(decision.filled_params) if decision.filled_params else None,
                    "counterProposal": decision.counter_proposal,
                    "counterParams": dict(decision.counter_params) if decision.counter_params else None,
                    "confirmedIntent": decision.confirmed_intent,
                    "feasibilitySummary": decision.feasibility_summary,
                },
            )
        result.decisions.append(decision)

        if exchanges >= max_exchanges:
            raise ClientFlowError(f"The client flow exceeded {max_exchanges} exchanges.")

        if decision.action == "accept":
            request, call_context = _build_accept_request(
                prompt_client=prompt_client,
                decision=decision,
                kind=kind,
                session=session,
                context_id=context_id,
                label=resolved_label,
                task_template_uri=task_template_uri,
                language=language,
                policy=policy,
                call_timeout_seconds=call_timeout_seconds,
                single_extension_per_message=single_extension_per_message,
                forced_region=forced_region,
                task_id=target_task_id if modify_only else None,
            )
            send_phase = PHASE_RESOLUTION
            send_performative = NegotiationPerformative.ACCEPT.value
            send_title = "Accept sent"
        elif decision.action == "counter":
            request, call_context = _build_counter_request(
                prompt_client=prompt_client,
                decision=decision,
                kind=kind,
                session=session,
                context_id=context_id,
                label=resolved_label,
                call_timeout_seconds=call_timeout_seconds,
                task_id=target_task_id if modify_only else None,
            )
            send_phase = PHASE_NEGOTIATION
            send_performative = NegotiationPerformative.PROPOSE.value
            send_title = f"Counter proposal sent · R{session.round}"
        else:
            performative = (
                NegotiationPerformative.REJECT if decision.action == "reject" else NegotiationPerformative.ABORT
            )
            request, call_context = _build_terminal_request(
                prompt_client=prompt_client,
                decision=decision,
                kind=kind,
                session=session,
                context_id=context_id,
                label=resolved_label,
                propose_content=propose.content,
                performative=performative,
                call_timeout_seconds=call_timeout_seconds,
                task_id=target_task_id if modify_only else None,
            )
            send_phase = PHASE_RESOLUTION
            send_performative = performative.value
            send_title = f"{performative.value.capitalize()} sent"

        emit_message_sent(
            request,
            phase=send_phase,
            title=send_title,
            performative=send_performative,
            round_number=session.round,
            context_id=context_id,
        )
        batch = await transport.send(request, call_context)
        exchanges += 1
        collect(batch)
        if decision.action in ("reject", "abort"):
            return finish(FINAL_STATE_NEGOTIATION_ENDED)
        if decision.action == "accept":
            # The vendor may open a new negotiation id on the same context id right after the
            # accept. That new PROPOSE is part of the accept response stream and must be decided;
            # only an accept batch without another proposal finishes the flow.
            if _find_negotiation_message(batch) is None:
                return finish(_final_state(batch))


# ----------------------------------------------------------------------
# Request construction
# ----------------------------------------------------------------------


def _build_message_request(
    *,
    metadata: Mapping[str, object],
    label: str,
    context_id: str | None = None,
    task_id: str | None = None,
) -> SendMessageRequest:
    """Build one user-role A2A message request with an optional metadata map.

    ``task_id`` turns the request into a real A2A continuation of the named Task; without it the
    server creates a new Task.
    """
    request = SendMessageRequest()
    request.message.message_id = str(uuid.uuid4())
    request.message.role = Role.ROLE_USER
    request.message.parts.add().text = label
    if context_id:
        request.message.context_id = context_id
    if task_id:
        request.message.task_id = task_id
    for key, value in metadata.items():
        if value is not None:
            # The bundled protobuf stubs do not model the dynamic Struct mapping.
            request.message.metadata[key] = value  # ty: ignore[invalid-assignment]
    return request


def _call_context(extension_uris: tuple[str, ...], timeout: float) -> ClientCallContext:
    """Build the call context carrying the ``A2A-Extensions`` header and the request budget.

    The SDK applies ``timeout`` to the HTTP request of every send (``get_http_args`` builds an
    ``httpx.Timeout`` from it), so the configured budget wins over the httpx client default.
    """
    return ClientCallContext(
        timeout=timeout,
        service_parameters={"A2A-Extensions": ",".join(sorted(set(extension_uris)))},
    )


def _negotiation_request(
    *,
    prompt_client: PromptClient,
    content: MetadataContent,
    label: str,
    context_id: str,
    session: NegotiationContext,
    call_timeout_seconds: float,
    extra_metadata: Mapping[str, object] | None = None,
    task_id: str | None = None,
) -> tuple[SendMessageRequest, ClientCallContext]:
    """Build one negotiation response request from generated protocol content."""
    metadata = {key: value for key, value in content.build_metadata_content().items() if value is not None}
    # The negotiation id is the server's own handle for the negotiation and differs from the
    # context id. The vendor NMS rejects a reply that carries the context id in its place with
    # ``negotiation id missing or mismatched``; the context id still travels in ``contextId``.
    # A continuing negotiation may also start a new negotiation id on the same context id.
    metadata[NEGOTIATION_CONTEXT_METADATA_KEY] = negotiation_context_metadata(
        NegotiationContext(
            id=session.id or context_id,
            round=session.round,
            max_rounds=session.max_rounds,
            performative=session.performative,
        )
    )
    if extra_metadata:
        metadata.update(extra_metadata)
    request = _build_message_request(
        metadata=metadata,
        label=label,
        context_id=context_id,
        task_id=task_id,
    )
    return request, _call_context((TASK_T_EXTENSION_URI, NEGOTIATION_T_EXTENSION_URI), call_timeout_seconds)


def _accept_filled_values(
    kind: NegotiationKind,
    decision: NegotiationDecision,
    language: str,
    policy: ClientPolicy,
    *,
    forced_region: str | None = None,
) -> dict[str, object]:
    """Merge model-filled values into the policy's agreed Task-T parameters safely.

    Live models sometimes fill an information request with per-cell SLA floors instead of the
    required time-segmented target. Only whitelisted Task-T keys are accepted, and the throughput
    target must have the ``{HH:MM~HH:MM,<rate>Mbps}`` shape; otherwise the policy's agreed value
    wins. For target/feasibility accepts, the negotiated target is authoritative.
    """
    filled = dict(policy.filled_params(kind.value, language))
    if kind is NegotiationKind.INFORMATION and decision.filled_params:
        allowed = TASK_PARAM_SCHEMA.get("properties")
        if isinstance(allowed, Mapping):
            for key, value in decision.filled_params.items():
                if key not in allowed or value is None:
                    continue
                if isinstance(value, (str, int, float)) and str(value).strip():
                    filled[key] = value
        target = filled.get("guaranteedThroughputTarget")
        if not isinstance(target, str) or not _TIME_SEGMENTED_TARGET_PATTERN.search(target):
            filled["guaranteedThroughputTarget"] = policy.filled_params(kind.value, language).get(
                "guaranteedThroughputTarget"
            )
    if forced_region:
        # The operator configured the region the vendor serves; a decision model must not replace
        # it with the demo region.
        filled["region"] = forced_region
    if filled.get("operationType") not in {"Create", "Modify"}:
        filled["operationType"] = "Create"
    return filled


def _ensure_agreed_target(task_text: str, filled: Mapping[str, object]) -> str:
    """Keep the agreed throughput target in a refilled Task-T prompt that dropped it.

    The refill is a model call even though the accepted value is already known. A live model can
    render the template without the negotiated target (typically by leaving the Task Context
    section empty), and the server then rejects the accept for the very value the client supplied.
    Every time segment of the agreed target must appear in the prompt; when one is missing, the
    target is written into the Task Context section per the template layout.

    Args:
        task_text: prompt rendered from the accepted Task-T parameters.
        filled: merged Task-T parameters the accept carries.

    Returns:
        The prompt unchanged when it already carries every segment, otherwise the repaired prompt.
    """
    target = filled.get("guaranteedThroughputTarget")
    if not isinstance(target, str) or not target.strip():
        return task_text
    segments = [re.sub(r"\s+", "", segment) for segment in _TIME_SEGMENTED_TARGET_PATTERN.findall(target)]
    if not segments:
        return task_text
    compact_prompt = re.sub(r"\s+", "", task_text)
    if all(segment in compact_prompt for segment in segments):
        return task_text
    _LOGGER.warning("the refilled Task-T prompt dropped the agreed throughput target; writing it into the task context")
    heading = _CONTEXT_HEADING_PATTERN.search(task_text)
    if heading is None:
        return f"{task_text.rstrip()}\n\n{target}\n"
    start = heading.end()
    following = re.search(r"^##\s", task_text[start:], re.MULTILINE)
    end = start + following.start() if following is not None else len(task_text)
    existing = task_text[start:end].strip()
    body = f"\n\n{existing}\n\n{target}\n\n" if existing else f"\n\n{target}\n\n"
    return f"{task_text[:start]}{body}{task_text[end:]}"


def _build_accept_request(
    *,
    prompt_client: PromptClient,
    decision: NegotiationDecision,
    kind: NegotiationKind,
    session: NegotiationContext,
    context_id: str,
    label: str,
    task_template_uri: str,
    language: str,
    policy: ClientPolicy,
    call_timeout_seconds: float,
    single_extension_per_message: bool,
    forced_region: str | None = None,
    task_id: str | None = None,
) -> tuple[SendMessageRequest, ClientCallContext]:
    """Refill the Task-T prompt and generate the Negotiation-T accept of the proposal type."""
    filled = _accept_filled_values(kind, decision, language, policy, forced_region=forced_region)
    with trace_stage("task_t_slot_extraction_refill"):
        task_content = prompt_client.generate_task_prompt_from_data_with_schema(
            filled, TASK_PARAM_SCHEMA, task_template_uri
        )
    task_text = _ensure_agreed_target(_require_prompt_text(task_content, "the refilled Task-T prompt"), filled)

    accept_text = _accept_text(
        kind,
        filled,
        decision,
        language,
        policy,
        forced_region=forced_region,
    )
    accept_context = session.with_performative(NegotiationPerformative.ACCEPT)
    with trace_stage("negotiation_accept_extraction"):
        content = prompt_client.generate_negotiation_accept_prompt_from_text(
            accept_text, accept_context, _ACCEPT_REJECT_TEMPLATE_URI_BY_KIND[kind.value]
        )
    # The real NMS rejects a message whose metadata holds more than one ``-T`` extension key, so
    # the NMS path sends the accept with the negotiation key only; the local demo server reads the
    # refilled Task-T prompt from the accept message and keeps receiving it.
    metadata = None if single_extension_per_message else {task_content.extension_uri or TASK_T_EXTENSION_URI: task_text}
    return _negotiation_request(
        prompt_client=prompt_client,
        content=content,
        label=label,
        context_id=context_id,
        session=accept_context,
        call_timeout_seconds=call_timeout_seconds,
        extra_metadata=metadata,
        task_id=task_id,
    )


#: Markers of an information proposal that rejected the requested region.
_REGION_REJECTION_MARKERS: Final[tuple[str, ...]] = (
    "not recognized",
    "not served",
    "does not serve",
    "unrecognized",
    "不识别",
    "无法识别",
    "不支持",
    "未识别",
)

#: Candidate-list labels the vendor uses in an information proposal.
_REGION_CANDIDATE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:Served areas|Available areas)\s*:\s*([^.\n]+)", re.IGNORECASE),
    re.compile(r"(?:可用区域|受服务区域)\s*[：:]\s*([^。\n]+)"),
)

#: Quoted region in an information proposal.
_REGION_QUOTED_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:Region|area)\s+['\"“]([^'\"”]+)['\"”]", re.IGNORECASE),
    re.compile(r"(?:区域|地区)\s*[“\"]([^”\"]+)[”\"]"),
)


def _rejected_region(propose: NegotiationProposeView) -> str | None:
    """Return the region an information proposal rejected, or ``None`` when it did not."""
    content = propose.content or ""
    lowered = content.lower()
    if not any(marker.lower() in lowered for marker in _REGION_REJECTION_MARKERS):
        return None
    for pattern in _REGION_QUOTED_PATTERNS:
        match = pattern.search(content)
        if match is not None and match.group(1).strip():
            return match.group(1).strip()
    return ""


def _region_candidates(propose: NegotiationProposeView) -> tuple[str, ...]:
    """Return the served regions an information proposal offers, best first."""
    options = propose.options
    if options is not None and options.options:
        return tuple(option.value for option in options.options if option.value)
    for pattern in _REGION_CANDIDATE_PATTERNS:
        match = pattern.search(propose.content or "")
        if match is None:
            continue
        values = [part.strip(" .;，；") for part in re.split(r"[;；,，、]", match.group(1))]
        candidates = tuple(value for value in values if value)
        if candidates:
            return candidates
    return ()


def _region_reask_needed(
    decision: NegotiationDecision,
    propose: NegotiationProposeView,
) -> tuple[str, tuple[str, ...]] | None:
    """Return the rejected region and candidates when the accept should be re-asked."""
    if decision.action != "accept" or propose.kind != "information":
        return None
    rejected = _rejected_region(propose)
    if rejected is None:
        return None
    candidates = _region_candidates(propose)
    if not candidates:
        return None
    current = (decision.filled_params or {}).get("region")
    if isinstance(current, str) and current.strip() and current.strip() != rejected:
        return None
    return rejected, candidates


def _region_reask_content(propose: NegotiationProposeView, candidates: tuple[str, ...]) -> str:
    """Append the candidate regions to a proposal for one corrective model turn."""
    joined = "; ".join(candidates)
    return (
        f"{propose.content}\n\n"
        "## Region Correction\n"
        "The previous ACCEPT used a region the server rejected. Return one corrected ACCEPT and "
        f"set `filled_params.region` to one of these served regions: {joined}. Keep every other "
        "value unchanged. Return only the NegotiationDecision JSON object."
    )


async def _reask_rejected_region(
    provider: NegotiationDecisionProvider,
    decision: NegotiationDecision,
    propose: NegotiationProposeView,
    session: NegotiationContext,
    *,
    trace_sink: Callable[[dict[str, object]], None] | None = None,
) -> NegotiationDecision:
    """Ask the ADK decision agent once more when it repeated a rejected region."""
    reask = _region_reask_needed(decision, propose)
    if reask is None:
        return decision
    # Only the ADK provider can take a corrective model turn. Manual and scripted providers keep
    # their existing behavior; the human can choose the region in the approval card.
    from protocol.decision_provider import AdkDecisionProvider

    if not isinstance(provider, AdkDecisionProvider):
        return decision
    rejected, candidates = reask
    if trace_sink is not None:
        recorder = TraceRecorder(current_trace().run_id or "", id_prefix="flow")
        span = recorder.emit(
            kind="region_reask",
            phase=PHASE_NEGOTIATION,
            title="Region rejected; asking the decision model again",
            actor="client",
            side="client",
            direction="internal",
            round=session.round,
            context_id=propose.context_id,
            detail={"rejectedRegion": rejected, "candidates": list(candidates)},
        )
        trace_sink(span.to_dict())
    corrected = propose.model_copy(update={"content": _region_reask_content(propose, candidates)})
    return await provider.respond(corrected, session)


def _build_counter_request(
    *,
    prompt_client: PromptClient,
    decision: NegotiationDecision,
    kind: NegotiationKind,
    session: NegotiationContext,
    context_id: str,
    label: str,
    call_timeout_seconds: float,
    task_id: str | None = None,
) -> tuple[SendMessageRequest, ClientCallContext]:
    """Generate a counter proposal that keeps the inbound round (same-round ``Propose``)."""
    text = decision.counter_proposal or decision.rationale
    if not text.strip():
        raise ClientFlowError("A counter decision carries neither a counter proposal nor a rationale.")
    propose_context = session.with_performative(NegotiationPerformative.PROPOSE)
    with trace_stage("negotiation_propose_extraction"):
        content = prompt_client.generate_negotiation_propose_prompt_from_text(
            text, propose_context, _PROPOSE_TEMPLATE_URI_BY_KIND[kind.value]
        )
    extra: dict[str, object] = {}
    if decision.counter_params:
        extra[SAMPLE_PROPOSAL_METADATA_KEY] = dict(decision.counter_params)
    return _negotiation_request(
        prompt_client=prompt_client,
        content=content,
        label=label,
        context_id=context_id,
        session=propose_context,
        call_timeout_seconds=call_timeout_seconds,
        extra_metadata=extra or None,
        task_id=task_id,
    )


def _build_terminal_request(
    *,
    prompt_client: PromptClient,
    decision: NegotiationDecision,
    kind: NegotiationKind,
    session: NegotiationContext,
    context_id: str,
    label: str,
    propose_content: str,
    performative: NegotiationPerformative,
    call_timeout_seconds: float,
    task_id: str | None = None,
) -> tuple[SendMessageRequest, ClientCallContext]:
    """Generate a terminal reject or abort message (no new Task is created)."""
    terminal_context = session.with_performative(performative)
    if performative is NegotiationPerformative.REJECT:
        text = _reject_text(kind, propose_content, decision)
        with trace_stage("negotiation_reject_extraction"):
            content = prompt_client.generate_negotiation_reject_prompt_from_text(
                text, terminal_context, _ACCEPT_REJECT_TEMPLATE_URI_BY_KIND[kind.value]
            )
    else:
        text = decision.rationale or "The client aborted the negotiation."
        with trace_stage("negotiation_abort_extraction"):
            content = prompt_client.generate_negotiation_abort_prompt_from_text(
                text, terminal_context, NEGOTIATION_ABORT_URI
            )
    return _negotiation_request(
        prompt_client=prompt_client,
        content=content,
        label=label,
        context_id=context_id,
        session=terminal_context,
        call_timeout_seconds=call_timeout_seconds,
        task_id=task_id,
    )


# ----------------------------------------------------------------------
# Event helpers
# ----------------------------------------------------------------------


def _request_with_context(user_request: str, conversation_context: str | None) -> str:
    """Prepend a delimited recent-turn block to the natural-language request."""
    if not conversation_context or not conversation_context.strip():
        return user_request
    return (
        "Conversation context (earlier turns of this client conversation):\n"
        f"{conversation_context.strip()}\n\n"
        "Current request:\n"
        f"{user_request}"
    )


def _require_prompt_text(content: MetadataContent, what: str) -> str:
    """Return the rendered prompt text of generated content, raising when it is missing."""
    text = content.prompt_text
    if not isinstance(text, str) or not text.strip():
        raise ClientFlowError(f"The SDK did not generate {what}.")
    return text


def _string_field(event: Mapping[str, object], key: str) -> str | None:
    """Return one string field of a normalized event, ``None`` when absent or not a string."""
    value = event.get(key)
    return value if isinstance(value, str) and value else None


def _task_id_of(events: list[dict[str, object]]) -> str | None:
    """Return the last task id carried by a batch of normalized events."""
    for event in reversed(events):
        task_id = _string_field(event, "task_id")
        if task_id:
            return task_id
    return None


def _context_id_of(events: list[dict[str, object]]) -> str | None:
    """Return the first context id carried by a batch of normalized events."""
    for event in events:
        context_id = _string_field(event, "context_id")
        if context_id:
            return context_id
        metadata = event.get("metadata")
        if isinstance(metadata, Mapping):
            session = parse_negotiation_context(metadata)
            if session is not None:
                return session.id
    return None


def _find_negotiation_message(
    events: list[dict[str, object]],
) -> tuple[dict[str, object], dict[str, object]] | None:
    """Return the last standalone Negotiation-T message of a batch and its metadata."""
    for event in reversed(events):
        if event.get("kind") != "message":
            continue
        metadata = event.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        metadata_dict = metadata_to_dict(metadata)
        if is_negotiation_message(metadata_dict):
            return event, metadata_dict
    return None


def _template_uri(metadata: Mapping[str, object]) -> str:
    """Return the ``templateUri`` of one negotiation metadata map."""
    value = metadata.get(TEMPLATE_URI_METADATA_KEY)
    if not isinstance(value, str) or not value.strip():
        raise ClientFlowError(_ERROR_NO_TEMPLATE_URI)
    return value


def _propose_view(
    event: Mapping[str, object],
    metadata: Mapping[str, object],
    session: NegotiationContext,
    kind: NegotiationKind,
    template_uri: str,
    context_id: str,
) -> NegotiationProposeView:
    """Build the provider-facing view of one inbound server proposal."""
    content = metadata.get(NEGOTIATION_T_EXTENSION_URI)
    return NegotiationProposeView(
        negotiation_id=session.id,
        kind=kind.value,
        round=session.round,
        max_rounds=session.max_rounds,
        content=content if isinstance(content, str) else "",
        performative=session.performative.value,
        template_uri=template_uri,
        context_id=_string_field(event, "context_id") or context_id,
        options=_proposal_options(metadata.get(SAMPLE_NEGOTIATION_OPTIONS_METADATA_KEY)),
    )


def _proposal_options(value: object) -> NegotiationOptions | None:
    """Parse the sample-only option payload of one proposal, ignoring malformed metadata."""
    if not isinstance(value, Mapping):
        return None
    try:
        return NegotiationOptions.model_validate(value)
    except ValueError:
        return None


def _accept_text(
    kind: NegotiationKind,
    filled: Mapping[str, object],
    decision: NegotiationDecision,
    language: str,
    policy: ClientPolicy,
    *,
    forced_region: str | None = None,
) -> str:
    """Render the natural-language input of an accept for the proposal type."""
    if kind is NegotiationKind.INFORMATION:
        # The information accept result must answer the requested item only. Repeating every base
        # Task-T field (region, description, ...) makes the Negotiation-T validator treat unrelated
        # text as result content and, on live models, reject the accept. The configured NMS region
        # is the exception: the operator selected it, so an accept always states it.
        allowed = TASK_PARAM_SCHEMA.get("properties")
        requested = [
            name for name in (decision.filled_params or {}) if isinstance(allowed, Mapping) and name in allowed
        ]
        if forced_region and "region" not in requested:
            requested.insert(0, "region")
        if not requested:
            requested = ["guaranteedThroughputTarget"]
        items = " ".join(f"{name}: {filled.get(name, '')}." for name in requested)
        return f"Accept. {items}"
    confirmation = decision.confirmed_intent if kind is NegotiationKind.TARGET else decision.feasibility_summary
    return f"Accept. {confirmation or policy.accept_text(kind.value, language) or decision.rationale}"


def _reject_text(kind: NegotiationKind, propose_content: str, decision: NegotiationDecision) -> str:
    """Render the natural-language input of a reject; information rejects itemize every item."""
    rationale = decision.rationale or "The requested information cannot be provided."
    if kind is not NegotiationKind.INFORMATION:
        return f"Reject. {rationale}"
    match = _MISSING_ITEMS_PATTERN.search(propose_content)
    items = [item.strip() for item in match.group(1).split(",")] if match else []
    items = [item for item in items if item] or ["requested information"]
    return "Reject. " + " ".join(f"{item}: {rationale}" for item in items)


def _final_state(batch: list[dict[str, object]]) -> str:
    """Derive the terminal state from one send phase's events.

    Only the last send phase may terminate the flow: scanning every accumulated event would let a
    prior round's ``Propose`` look like the end message after a webhook phase stops at the report
    artifact. A plain non-negotiation message (or the explicit end text) is the negotiation-end
    signal; a negotiation message is not.
    """
    report_seen = False
    for event in reversed(batch):
        if event.get("kind") == "artifact":
            report_seen = True
            continue
        if event.get("kind") == "message":
            text = event.get("text")
            metadata = event.get("metadata")
            if text == END_MESSAGE_TEXT:
                return FINAL_STATE_NEGOTIATION_ENDED
            if isinstance(metadata, Mapping) and is_negotiation_message(metadata_to_dict(metadata)):
                continue
            return FINAL_STATE_NEGOTIATION_ENDED
        if event.get("kind") != "status":
            continue
        state = event.get("state")
        if state == TaskState.Name(TaskState.TASK_STATE_COMPLETED):
            return FINAL_STATE_TASK_COMPLETED
        if state == TaskState.Name(TaskState.TASK_STATE_REJECTED):
            return FINAL_STATE_TASK_REJECTED
        if state in _TERMINAL_STATE_NAMES:
            return FINAL_STATE_TASK_FAILED
    return FINAL_STATE_INCOMPLETE if not report_seen else FINAL_STATE_TASK_COMPLETED


def _fill_result(result: ClientFlowResult, context_id: str | None) -> None:
    """Fill the context id, task id and report payload of one result.

    The ADK executor emits one artifact per tool call and a final artifact named after the report;
    the report artifact wins, the first artifact is only the fallback so a Task that produced no
    named report still exposes something useful.
    """
    result.context_id = result.context_id or context_id
    report_events: list[dict[str, object]] = []
    fallback_event: dict[str, object] | None = None
    for event in result.events:
        task_id = _string_field(event, "task_id")
        if task_id:
            result.task_id = task_id
        if event.get("kind") == "artifact":
            fallback_event = fallback_event or event
            if "report" in (_string_field(event, "name") or "").lower():
                report_events.append(event)
    # A rejected change still carries tool artifacts (the profile and the evaluation), but it has
    # no report. Never expose the first tool result as if it were the energy-saving report.
    if result.final_state != FINAL_STATE_TASK_COMPLETED:
        return
    selected = report_events or ([fallback_event] if fallback_event is not None else [])
    for event in selected:
        data = event.get("data")
        if isinstance(data, Mapping):
            result.reports.append(dict(data))
        else:
            result.reports.append(
                {key: value for key, value in event.items() if key in ("text", "name", "artifact_id")}
            )
    if result.reports:
        # A two-phase run has a baseline report and the modified report; the latter is the result.
        result.report = result.reports[-1]
