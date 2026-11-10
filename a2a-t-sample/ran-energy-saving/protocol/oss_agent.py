"""OSS Agent: the middle hop of the pure A2A three-hop mode.

The engineer client talks to this A2A server. The OSS Agent forwards the natural-language request
to the RAN Agent over standard A2A, answers the RAN's ``INPUT_REQUIRED`` negotiation questions with
its injected decision function, and aggregates every RAN event onto its own Task so the engineer
client keeps a single stream.

The agent deliberately owns no RAN planning logic. Its job is protocol and presentation
aggregation: forward, answer, tag each event with its hop, and return the final report.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

import httpx
import uvicorn
from a2a.auth.user import User
from a2a.client import ClientConfig, create_client
from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext, UnauthenticatedUser
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_rest_routes
from a2a.server.tasks.base_push_notification_sender import BasePushNotificationSender
from a2a.server.tasks.inmemory_push_notification_config_store import (
    InMemoryPushNotificationConfigStore,
)
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.push_notification_config_store import PushNotificationConfigStore
from a2a.server.tasks.push_notification_sender import PushNotificationSender
from a2a.server.tasks.task_store import TaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
    Part,
    Task,
    TaskState,
)
from a2a.utils.constants import TransportProtocol
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from google.protobuf.json_format import ParseDict
from shared.agent_card import AGENT_CARD_NAME, AGENT_CARD_ORGANIZATION
from shared.config import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_LONG_TASK_MODE,
    DEFAULT_LONG_TASK_SETTLE_SECONDS,
    DISCOVERY_NMS,
    LONG_TASK_MODE_OFF,
    LONG_TASK_MODE_ON,
    PURE_FOLLOWUP_BOTH,
    TRANSPORT_WEBHOOK,
    SampleConfig,
    load_config,
)
from shared.logging_setup import configure_logging
from shared.models import NegotiationDecision, NegotiationProposeView
from shared.run_constants import (
    FINAL_STATE_INCOMPLETE,
    FINAL_STATE_TASK_COMPLETED,
    FINAL_STATE_TASK_REJECTED,
    LANGUAGE_ZH,
    PROTOCOL_PURE,
)
from shared.trace import FileTraceEmitter, HttpTraceEmitter, set_global_sink, trace_scope
from shared.trace_propagation import (
    TracePropagationMiddleware,
    current_run_id,
    inject_outgoing,
)
from shared.webhook_receiver import WebhookReceiver, start_receiver_with_fallback
from starlette.applications import Starlette

from protocol.client_flow import (
    ClientFlowResult,
    SseClientTransport,
)
from protocol.client_policy import ClientPolicy
from protocol.discovery import DISCOVERY_DIRECT, discover_agent
from protocol.downstream_binding_store import DownstreamBinding, DownstreamBindingStore
from protocol.downstream_pump import DownstreamPump, DownstreamPumpContext
from protocol.gateway_push import ValidatingPushConfigStore
from protocol.intent import IntentProcessor, OperatorRequest, ProcessedIntent
from protocol.manual_answer import ungrounded_answer_fields
from protocol.messages import SAMPLE_RUN_ID_METADATA_KEY, metadata_to_dict
from protocol.nms_client import (
    NmsClient,
    NmsConfig,
    NmsProtocolError,
    NmsSseTransport,
    NmsWebhookTransport,
)
from protocol.pure_answer_guard import sanitize_pure_decision
from protocol.pure_client_flow import (
    continue_pure_client_flow,
    pure_metadata,
    pure_negotiation_kind,
    run_pure_client_flow,
)
from protocol.shared_webhook import WebhookRouter, WebhookSubscription
from protocol.sqlite_push_config_store import SqlitePushNotificationConfigStore
from protocol.sqlite_task_store import SqliteTaskStore
from protocol.wire_log import WIRE_OSS_FILENAME, HttpWireLog

#: Agent identity and skill.
OSS_AGENT_NAME: Final[str] = "OSS Agent"
OSS_AGENT_ORGANIZATION: Final[str] = "Huawei"
OSS_AGENT_PROVIDER_URL: Final[str] = "https://www.huawei.com"
OSS_AGENT_SKILL_ID: Final[str] = "ran-energy-saving-request"
OSS_AGENT_DESCRIPTION: Final[str] = (
    "Takes a natural-language RAN energy-saving request, forwards it to the RAN Agent over "
    "standard A2A, answers its negotiation questions, and returns the final report."
)

#: Default ports of the OSS and RAN A2A servers.
DEFAULT_OSS_PORT: Final[int] = 8001
DEFAULT_RAN_PORT: Final[int] = 8000

_LOGGER = logging.getLogger("ran_energy_saving.oss_agent")

#: Builder of the advisory answer attached to one relayed downstream question.
RecommendationBuilder = Callable[[dict[str, object]], Awaitable[dict[str, object] | None]]


class _PureDecisionProvider(Protocol):
    """Decision agent answering one pure downstream question."""

    async def respond(
        self,
        propose: NegotiationProposeView,
        session: NegotiationContext,
    ) -> NegotiationDecision:
        """Return one structured decision for the proposal."""


#: Outer Task states that need no background tracking after a restart.
_TERMINAL_TASK_STATES: Final[frozenset[TaskState]] = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
    }
)

#: Vendor Task state names that refuse a further message. The picker check runs when the run is
#: created; the downstream Task can reach a terminal state before the follow-up is forwarded, so
#: the OSS Agent re-reads it once at the last possible moment.
_TERMINAL_TASK_STATE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "TASK_STATE_COMPLETED",
        "TASK_STATE_REJECTED",
        "TASK_STATE_FAILED",
        "TASK_STATE_CANCELED",
    }
)


class _QuestionPendingError(Exception):
    """The downstream asked for information and the outer caller must answer it."""

    def __init__(self, event: dict[str, object]) -> None:
        """Carry the inbound question event."""
        super().__init__("The downstream requested information")
        self.event = event


@dataclass(frozen=True)
class _PendingQuestion:
    """One downstream question relayed to the outer caller and awaiting its answer."""

    outer_task_id: str
    outer_context_id: str
    run_id: str
    inner_task_id: str
    inner_context_id: str
    round_number: int
    #: Natural-language request that opened the run. The operator answer arrives in a new request
    #: task, so the provider cannot rely on a context variable set by the first hop.
    user_request: str = ""
    #: Texts already sent downstream in this run, oldest first. A recommendation that repeats one
    #: of them cannot answer the vendor's question.
    sent_texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class _PumpHandoff:
    """One downstream subscription transferred from a request to the background pump."""

    subscription: WebhookSubscription
    transport: NmsWebhookTransport
    binding: DownstreamBinding
    outer_context_id: str
    call_context: ServerCallContext
    #: Request that opened the run; a pump resumed after a restart has none.
    user_request: str = ""


@dataclass
class _PumpHandle:
    """One background pump and the subscription it owns."""

    task: asyncio.Task[None]
    subscription: WebhookSubscription


@dataclass(frozen=True)
class _StoredUser(User):
    """A user reconstructed from a persisted binding owner."""

    name: str

    @property
    def is_authenticated(self) -> bool:
        """The persisted owner was authenticated when the binding was written."""
        return bool(self.name)

    @property
    def user_name(self) -> str:
        """Return the persisted owner name."""
        return self.name


class OSSPureExecutor(AgentExecutor):
    """Forward one engineer request to RAN and aggregate the hop events."""

    def __init__(
        self,
        *,
        config: SampleConfig | None = None,
        ran_url: str | None = None,
        answer: Callable[[dict[str, object]], str | Awaitable[str]],
        recommendation: RecommendationBuilder | None = None,
        decision_provider: object | None = None,
        max_rounds: int = 3,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        intent_processor: IntentProcessor | None = None,
        language: str = LANGUAGE_ZH,
        followup: str = PURE_FOLLOWUP_BOTH,
        trace_sink: Callable[[dict[str, object]], None] | None = None,
        task_store: TaskStore | None = None,
        binding_store: DownstreamBindingStore | None = None,
        push_sender: PushNotificationSender | None = None,
        long_task_mode: str = DEFAULT_LONG_TASK_MODE,
        long_task_settle_seconds: float = DEFAULT_LONG_TASK_SETTLE_SECONDS,
    ) -> None:
        """Create one OSS executor.

        Args:
            config: sample configuration; when it selects NMS discovery the downstream hop uses
                the vendor ``message:send`` + webhook (or SSE for the simulator) transport.
            ran_url: RAN Agent base URL used by the local direct topology.
            answer: decision function answering one downstream ``INPUT_REQUIRED`` event. It may
                return text or an awaitable of text.
            decision_provider: optional live decision provider; the executor calls its
                ``set_user_request`` hook with the normalized intent before the downstream hop.
            max_rounds: negotiation round budget.
            timeout_seconds: A2A call budget of the downstream hop.
            intent_processor: business intent layer run before the downstream hop.
            language: process-level language used by the intent processor.
            followup: conversation fields carried by one pure follow-up (``both``/``task``/``context``).
            trace_sink: cross-process Run Explorer sink; ``None`` leaves the hop untraced.
            task_store: outer Task store shared with the request handler; the long-task pump
                persists late updates through it.
            binding_store: durable outer↔downstream binding store; required for long-task mode.
            push_sender: A2A push-notification sender used for late outer Task updates.
            long_task_mode: ``auto`` / ``on`` / ``off``; ``auto`` enables tracking for the real NMS
                webhook hop when a binding store is configured.
            long_task_settle_seconds: synchronous stream window before the subscription is handed
                to the pump; it never ends the run or cancels the downstream Task.
        """
        self._config = config
        self._ran_url = (ran_url or "").rstrip("/")
        self._answer = answer
        self._recommendation = recommendation
        self._decision_provider = decision_provider
        self._max_rounds = max_rounds
        self._timeout_seconds = timeout_seconds
        self._intent_processor = intent_processor
        self._language = language
        self._followup = followup
        self._trace_sink = trace_sink
        self._task_store = task_store or InMemoryTaskStore()
        self._binding_store = binding_store
        self._push_sender = push_sender
        self._long_task_mode = long_task_mode
        self._long_task_settle_seconds = long_task_settle_seconds
        # One background pump per handed-off outer Task id.
        self._pumps: dict[str, _PumpHandle] = {}
        # Downstream questions relayed to the outer caller: outer Task id → pending answer.
        self._pending_questions: dict[str, _PendingQuestion] = {}
        # A handoff produced by ``_run_ran_webhook`` and started after ``execute`` has written its
        # last synchronous outer status (avoids two writers racing on the Task store).
        self._pending_handoffs: dict[str, _PumpHandoff] = {}
        # Captures the local a2a-sdk hop to the RAN Agent (NMS runs use the NmsClient log).
        self._wire_log = HttpWireLog()
        # The HTTP/token client is shared (token refresh is locked); each inbound request gets its
        # own transport because ``set_event_sink`` is per run.
        self._nms_client: NmsClient | None = None
        self._webhook_receiver: WebhookReceiver | None = None
        # Webhook mode: one process-level receiver, one router queue per inbound request, so two
        # concurrent engineer requests do not consume each other's NMS pushes.
        self._webhook_router: WebhookRouter | None = None
        self._webhook_push_url = ""
        self._webhook_push_token = ""
        self._webhook_idle_timeout = 0.0

    async def start(self) -> None:
        """Create the long-lived NMS downstream transport, when NMS discovery is configured."""
        if self._config is None:
            _LOGGER.info("OSS Agent downstream: local RAN Agent (%s)", self._ran_url)
            return
        if self._config.discovery != DISCOVERY_NMS:
            # A pure + NMS deployment whose environment resolves to another discovery silently
            # runs the local topology. Say it once at startup so ``oss.log`` answers "did this
            # process talk to the real NMS?" without reading the source.
            _LOGGER.info(
                "OSS Agent downstream: local RAN Agent (%s, discovery=%s)",
                self._ran_url,
                self._config.discovery,
            )
            return
        config = self._config
        _LOGGER.info(
            "OSS Agent downstream: NMS %s (transport=%s)",
            config.nms_a2a_base,
            config.transport,
        )
        self._nms_client = NmsClient(NmsConfig.from_config(config))
        if config.transport == TRANSPORT_WEBHOOK:
            router = WebhookRouter()

            # The real NMS reaches the callback through the VPN gateway over https, so the bind
            # address is never the URL to register: carry the public URL, TLS and Basic auth over
            # exactly as the A2A-T client flow does.
            def _make_receiver(port: int) -> WebhookReceiver:
                """Build the receiver for one callback port."""
                return WebhookReceiver(
                    token=config.webhook_token,
                    on_event=router.on_event,
                    host=config.webhook_host,
                    port=port,
                    public_url=config.webhook_public_url,
                    tls_cert=config.webhook_tls_cert,
                    tls_key=config.webhook_tls_key,
                    auth_credentials=config.webhook_auth_credentials,
                )

            receiver = await start_receiver_with_fallback(
                make=_make_receiver,
                port=config.webhook_port,
                public_url=config.webhook_public_url,
            )
            self._webhook_receiver = receiver
            self._webhook_router = router
            self._webhook_push_url = receiver.url
            self._webhook_push_token = config.webhook_token
            self._webhook_idle_timeout = float(config.webhook_idle_timeout)
            _LOGGER.info("OSS Agent NMS webhook: push_url=%s", receiver.url)
            if self._long_task_enabled():
                await self._resume_active_bindings()
        # SSE mode needs no process resource: the per-run transport is built in ``_run_ran``.

    async def stop(self) -> None:
        """Stop background pumps, the webhook receiver and the NMS client."""
        self._pending_questions.clear()
        await self._stop_pumps()
        receiver = self._webhook_receiver
        if receiver is not None:
            await receiver.stop()
            self._webhook_receiver = None
        self._webhook_router = None
        if self._nms_client is not None:
            await self._nms_client.aclose()
            self._nms_client = None

    def _long_task_enabled(self) -> bool:
        """Whether this hop hands its downstream Task to the background pump."""
        if self._long_task_mode == LONG_TASK_MODE_OFF or self._binding_store is None:
            return False
        if self._long_task_mode == LONG_TASK_MODE_ON:
            return True
        return (
            self._config is not None
            and self._config.discovery == DISCOVERY_NMS
            and self._config.transport == TRANSPORT_WEBHOOK
        )

    async def _stop_pumps(self) -> None:
        """Cancel every background pump; active bindings stay durable for a restart."""
        handles = list(self._pumps.values())
        self._pumps.clear()
        self._pending_handoffs.clear()
        for handle in handles:
            handle.task.cancel()
        if handles:
            await asyncio.gather(*(handle.task for handle in handles), return_exceptions=True)
        for handle in handles:
            # The pump's own ``finally`` may not run when it is cancelled before its first turn.
            handle.subscription.unsubscribe()

    def _start_pending_pump(self, outer_task_id: str) -> bool:
        """Start the pump prepared by ``_run_ran_webhook`` after the last synchronous status."""
        handoff = self._pending_handoffs.pop(outer_task_id, None)
        if handoff is None:
            return False
        pump = self._build_pump(handoff)
        if pump is not None:
            self._register_pump(handoff, pump)
            _LOGGER.info(
                "downstream task handed to the background pump: outer=%s inner=%s",
                outer_task_id,
                handoff.binding.inner_task_id,
            )
            return True
        return False

    def _build_pump(self, handoff: _PumpHandoff) -> DownstreamPump | None:
        """Build one background pump from a handoff; ``None`` without a binding store."""
        if self._binding_store is None:
            return None
        return DownstreamPump(
            context=DownstreamPumpContext(
                run_id=handoff.binding.run_id,
                inner_task_id=handoff.binding.inner_task_id,
                inner_context_id=handoff.binding.inner_context_id,
                outer_task_id=handoff.binding.outer_task_id,
                outer_context_id=handoff.outer_context_id,
                downstream_hop=self._downstream_hop(),
                downstream_name=self._downstream_name(),
                followup=self._followup,
                max_rounds=self._max_rounds,
                call_timeout_seconds=self._timeout_seconds,
                call_context=handoff.call_context,
            ),
            subscription=handoff.subscription,
            transport=handoff.transport,
            task_store=self._task_store,
            binding_store=self._binding_store,
            push_sender=self._push_sender,
            answer=self._answer_for_request(handoff.user_request),
            recommendation=self._recommendation_for_request(handoff.user_request),
            on_question=self._question_registrar(handoff),
        )

    def _register_pump(self, handoff: _PumpHandoff, pump: DownstreamPump) -> None:
        """Start one pump and own its subscription for the rest of the process lifetime."""
        outer_task_id = handoff.binding.outer_task_id
        task = pump.start()
        self._pumps[outer_task_id] = _PumpHandle(task=task, subscription=handoff.subscription)
        task.add_done_callback(lambda _: self._pumps.pop(outer_task_id, None))

    async def _resume_active_bindings(self) -> None:
        """Recreate the pumps of a previous process from the durable bindings.

        The binding store is written when a synchronous request hands its Task to the pump. A
        restart used to lose every pump: the outer Task stayed ``WORKING`` and later pushes had no
        owner. Resume each active binding, poll the authoritative Task once (terminal pushes can be
        missed while the process is down), and expire old terminal bindings.
        """
        if self._binding_store is None or self._webhook_router is None or self._nms_client is None:
            return
        retention_days = self._config.task_retention_days if self._config is not None else 7
        try:
            removed = await self._binding_store.delete_expired(
                before=time.time() - retention_days * 86_400.0
            )
            if removed:
                _LOGGER.info("expired %d terminal downstream binding(s)", removed)
        except Exception:  # noqa: BLE001 - cleanup must not stop recovery
            _LOGGER.warning("downstream binding cleanup failed", exc_info=True)
        bindings = await self._binding_store.list_active()
        if not bindings:
            return
        _LOGGER.info("resuming %d active downstream binding(s)", len(bindings))
        for binding in bindings:
            try:
                await self._resume_binding(binding)
            except Exception:  # noqa: BLE001 - one bad binding must not stop the others
                _LOGGER.exception(
                    "failed to resume downstream binding inner=%s outer=%s",
                    binding.inner_task_id,
                    binding.outer_task_id,
                )

    async def _resume_binding(self, binding: DownstreamBinding) -> None:
        """Resume one persisted outer ↔ downstream Task binding."""
        router = self._webhook_router
        nms_client = self._nms_client
        if router is None or nms_client is None:
            return
        call_context = ServerCallContext(
            user=UnauthenticatedUser() if not binding.owner else _StoredUser(binding.owner)
        )
        outer_task = await self._task_store.get(binding.outer_task_id, call_context)
        if outer_task is None:
            _LOGGER.warning(
                "dropping downstream binding without an outer Task: outer=%s inner=%s",
                binding.outer_task_id,
                binding.inner_task_id,
            )
            await self._binding_store.mark_terminal(binding.inner_task_id, binding.inner_context_id)
            return
        if outer_task.status.state in _TERMINAL_TASK_STATES:
            await self._binding_store.mark_terminal(binding.inner_task_id, binding.inner_context_id)
            return

        subscription = router.subscribe(binding.run_id)
        subscription.bind(task_id=binding.inner_task_id, context_id=binding.inner_context_id)
        transport = NmsWebhookTransport(
            nms_client,
            queue=subscription.queue,
            push_url=self._webhook_push_url,
            push_token=self._webhook_push_token,
            push_auth_credentials=(
                self._config.webhook_auth_credentials if self._config is not None else ""
            ),
            protocol=PROTOCOL_PURE,
            idle_timeout=self._long_task_settle_seconds,
            bind=subscription.bind,
            context_id=subscription.key,
        )
        handoff = _PumpHandoff(
            subscription=subscription,
            transport=transport,
            binding=binding,
            outer_context_id=outer_task.context_id,
            call_context=call_context,
        )
        pump = self._build_pump(handoff)
        if pump is None:
            subscription.unsubscribe()
            return
        try:
            document = await nms_client.get_task(binding.inner_task_id)
        except Exception:  # noqa: BLE001 - pushes remain the live path; polling is a hint
            _LOGGER.warning(
                "could not reconcile resumed downstream Task %s", binding.inner_task_id, exc_info=True
            )
        else:
            await pump.reconcile(document)
        if pump.terminal:
            subscription.unsubscribe()
            return
        self._register_pump(handoff, pump)
        _LOGGER.info(
            "resumed downstream task: outer=%s inner=%s run=%s",
            binding.outer_task_id,
            binding.inner_task_id,
            binding.run_id or "-",
        )

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Create the outer Task, run the RAN hop, and publish the aggregated result."""
        if context.current_task is not None:
            pending = self._pending_questions.pop(context.task_id or "", None)
            if pending is not None:
                await self._resume_question(context, event_queue, pending)
                return
            await self._continue_running_task(context, event_queue, context.current_task)
            return
        message = context.message
        if message is None:
            raise ValueError("The OSS request carries no A2A message")

        task = new_task_from_user_message(message)
        task.id = context.task_id or str(uuid.uuid4())
        task.context_id = context.context_id or task.id
        await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=updater.new_agent_message(
                [Part(text="OSS Agent received the request and is processing it.")],
                metadata=self._oss_metadata("receive"),
            ),
        )

        events: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        consumer = asyncio.create_task(self._consume(events, updater), name=f"oss-ran-consumer-{task.id}")
        run_id = _run_id_of(message)
        raw_text = _message_text(message)
        intent = await self._process_intent(raw_text)
        await self._publish_intent(updater, intent)
        try:
            with trace_scope(run_id=run_id, role="oss"):
                # The downstream hop is standard A2A: send the natural-language request, never
                # the Task-T section layout used by the A2A-T path.
                await self._announce_forwarding(updater)
                result = await self._run_ran(
                    a2a_request_text(intent),
                    run_id,
                    events.put_nowait,
                    updater,
                    context.call_context,
                )
        except _QuestionPendingError as question:
            events.put_nowait(None)
            await consumer
            await self._relay_question(
                updater,
                outer_task_id=task.id,
                outer_context_id=task.context_id,
                run_id=run_id or "",
                event=question.event,
                round_number=1,
                user_request=a2a_request_text(intent),
                sent_texts=(a2a_request_text(intent),),
            )
            return
        except Exception as exc:  # noqa: BLE001 - the outer Task must reach a terminal state
            _LOGGER.exception("OSS->RAN hop failed")
            events.put_nowait(None)
            await consumer
            await updater.failed(message=updater.new_agent_message([Part(text=f"The RAN hop failed: {exc}")]))
            return
        events.put_nowait(None)
        await consumer
        await self._publish_result(updater, result)
        await self._remember_downstream_binding(task, result, run_id, context.call_context)

    async def _remember_downstream_binding(
        self,
        task: Task,
        result: ClientFlowResult,
        run_id: str | None,
        call_context: ServerCallContext,
    ) -> None:
        """Persist the outer ↔ downstream identity of a still-running hop.

        The webhook path writes the binding when it hands the Task to the pump. The SSE path has
        no pump, but a later follow-up still has to find the downstream Task, so it records the
        same durable mapping here.
        """
        # Only the NMS hop can be continued by a later message. The local RAN hop answers its
        # questions inside the synchronous flow and has no vendor Task to resume.
        if self._binding_store is None or self._nms_client is None or not result.task_id:
            return
        if result.final_state != FINAL_STATE_INCOMPLETE:
            return
        existing = await self._binding_store.get(result.task_id, result.context_id or "")
        if existing is not None:
            return
        await self._binding_store.save(
            DownstreamBinding(
                inner_task_id=result.task_id,
                inner_context_id=result.context_id or "",
                outer_task_id=task.id,
                run_id=run_id or "",
                owner=call_context.user.user_name,
                protocol=PROTOCOL_PURE,
            )
        )

    async def _publish_result(self, updater: TaskUpdater, result: ClientFlowResult) -> None:
        """Map one finished downstream flow onto the outer Task."""
        if result.final_state == FINAL_STATE_TASK_COMPLETED:
            metrics = {
                "hop": "oss",
                "rounds": _rounds(result.events),
                "messages": len(result.events),
                "hops": self._hop_count(),
            }
            identity = self._downstream_identity(result)
            await updater.update_status(
                TaskState.TASK_STATE_COMPLETED,
                message=updater.new_agent_message(
                    [Part(text="OSS Agent completed the request.")],
                    metadata={
                        "pure": {**metrics, "phase": "report", **identity},
                        "hop": "oss",
                    },
                ),
                metadata={"pure": {**metrics, **identity}},
            )
            return
        if result.final_state == FINAL_STATE_TASK_REJECTED:
            await updater.update_status(
                TaskState.TASK_STATE_REJECTED,
                message=updater.new_agent_message(
                    [Part(text="The RAN Agent rejected the request.")],
                    metadata={"pure": {"hop": "ran", **self._downstream_identity(result)}, "hop": "ran"},
                ),
            )
            return
        if result.final_state == FINAL_STATE_INCOMPLETE:
            # The local wait window (webhook idle timeout or the SSE stream) ended before the
            # downstream sent a terminal state. That is not a vendor failure: the NMS task may
            # still be running. Keep the outer Task WORKING and tell the client why, instead of
            # reporting FAILED for a state the downstream never sent.
            downstream = self._downstream_name()
            hop = self._downstream_hop()
            task_ref = f" ({result.task_id})" if result.task_id else ""
            metadata = {
                "pure": {"hop": hop, "phase": "waiting", **self._downstream_identity(result)},
                "hop": hop,
            }
            _LOGGER.info(
                "downstream still working after the local wait window: task=%s",
                result.task_id or "-",
            )
            tracked = self._start_pending_pump(updater.task_id)
            suffix = (
                "the OSS Agent continues tracking it in the background."
                if tracked
                else "the OSS Agent will report the next downstream update when one arrives."
            )
            await updater.update_status(
                TaskState.TASK_STATE_WORKING,
                message=updater.new_agent_message(
                    [
                        Part(
                            text=(
                                f"The {downstream} task is still running{task_ref}; {suffix}"
                            )
                        )
                    ],
                    metadata=metadata,
                ),
                metadata=metadata,
            )
            return
        downstream = self._downstream_name()
        hop = self._downstream_hop()
        detail = _last_status_text(result.events)
        text = (
            f"The {downstream} could not complete the request: {detail}"
            if detail
            else f"The {downstream} hop ended with state {result.final_state}."
        )
        await updater.failed(
            message=updater.new_agent_message(
                [Part(text=text)],
                metadata={
                    "pure": {"hop": hop, "phase": "resolution", **self._downstream_identity(result)},
                    "hop": hop,
                },
            )
        )

    def _oss_metadata(self, phase: str) -> dict[str, object]:
        """Return the ``pure`` tags of one OSS-owned status.

        The downstream name travels with every OSS status so the trace can label the row with the
        hop that actually exists (NMS or local RAN) instead of assuming ``RAN``.
        """
        return {
            "pure": {
                "hop": "oss",
                "phase": phase,
                "downstream": self._downstream_hop(),
            },
            "hop": "oss",
        }

    async def _announce_forwarding(self, updater: TaskUpdater) -> None:
        """Publish the forward claim only when the downstream hop is about to start."""
        downstream = self._downstream_name()
        metadata = self._oss_metadata("dispatch")
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=updater.new_agent_message(
                [Part(text=f"OSS Agent is forwarding the request to the {downstream}.")],
                metadata=metadata,
            ),
            metadata=metadata,
        )

    def _nms_mode(self) -> bool:
        """Whether the downstream hop is the vendor NMS rather than the local RAN Agent."""
        return self._config is not None and self._config.discovery == DISCOVERY_NMS

    def _downstream_name(self) -> str:
        """Human name of the downstream system shown on the OSS status messages."""
        return "NMS" if self._nms_mode() else "RAN Agent"

    def _downstream_hop(self) -> str:
        """Hop tag of downstream events: ``nms`` in a vendor run, ``ran`` in the local topology."""
        return "nms" if self._nms_mode() else "ran"

    def _downstream_identity(self, result: ClientFlowResult) -> dict[str, object]:
        """Return the far-side Task identity written on every event after the downstream hop.

        The client owns the outer A2A Task, and ``result.task_id``/``context_id`` belong to the
        downstream server, not to that outer Task. Publishing them under ``downstream*`` keeps both
        identities and lets a client query the NMS/RAN Agent with the values it actually knows.
        """
        identity: dict[str, object] = {"downstreamHop": self._downstream_hop()}
        if result.task_id:
            identity["downstreamTaskId"] = result.task_id
        if result.context_id:
            identity["downstreamContextId"] = result.context_id
        return identity

    def _hop_count(self) -> int:
        """Engineer → OSS → downstream: two hops against the NMS, three in the local topology."""
        return 2 if self._nms_mode() else 3

    async def _process_intent(self, raw_text: str) -> ProcessedIntent:
        """Run the business intent layer on the inbound engineer request."""
        if self._intent_processor is None:
            return ProcessedIntent.raw(raw_text)
        return await self._intent_processor.process(
            OperatorRequest(
                text=raw_text,
                language=self._language,
                source="a2a",
                custom=False,
            )
        )

    async def _publish_intent(self, updater: TaskUpdater, intent: ProcessedIntent) -> None:
        """Expose the normalized intent on the outer Task before the RAN hop starts."""
        metadata = self._oss_metadata("intent")
        # ``_pure_detail`` reads ``intentProcessed`` from the metadata top level, next to ``pure``.
        metadata["intentProcessed"] = intent.to_dict()
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=updater.new_agent_message(
                [Part(text=f"Business intent ready: {intent.region or intent.operation_type}")],
                metadata=metadata,
            ),
        )

    async def _run_ran_webhook(
        self,
        user_request: str,
        run_id: str | None,
        event_sink: Callable[[dict[str, object]], None],
        updater: TaskUpdater,
        call_context: ServerCallContext,
    ) -> ClientFlowResult:
        """Run one inbound request against the NMS over its shared callback port.

        Each inbound request gets its own router subscription: the NMS ignores push tokens, so
        routing by the downstream task/context identity is what keeps concurrent requests from
        consuming each other's events.

        In long-task mode the synchronous flow only stays attached for the settle window. When it
        ends with a live downstream Task, the subscription is transferred to a background pump and
        **not** unsubscribed; the outer Task keeps updating after the request returns.
        """
        router = self._webhook_router
        nms_client = self._nms_client
        if router is None or nms_client is None:
            raise RuntimeError("The NMS webhook transport is not started.")
        subscription = router.subscribe(run_id or "")
        handed_off = False
        try:
            long_mode = self._long_task_enabled()
            transport = NmsWebhookTransport(
                nms_client,
                queue=subscription.queue,
                push_url=self._webhook_push_url,
                push_token=self._webhook_push_token,
                push_auth_credentials=(
                    self._config.webhook_auth_credentials if self._config is not None else ""
                ),
                protocol=PROTOCOL_PURE,
                idle_timeout=self._long_task_settle_seconds if long_mode else self._webhook_idle_timeout,
                bind=subscription.bind,
                context_id=subscription.key,
            )
            result = await run_pure_client_flow(
                transport=transport,
                user_request=user_request,
                answer=self._answer_or_relay,
                on_answer=lambda event, text: self._publish_answer(updater, event, text),
                max_rounds=self._max_rounds,
                call_timeout_seconds=self._timeout_seconds,
                event_sink=event_sink,
                trace_sink=self._trace_sink,
                followup=self._followup,
                plain_questions=True,
            )
            if (
                long_mode
                and self._binding_store is not None
                and result.task_id
                and result.final_state == FINAL_STATE_INCOMPLETE
            ):
                binding = DownstreamBinding(
                    inner_task_id=result.task_id,
                    inner_context_id=result.context_id or "",
                    outer_task_id=updater.task_id,
                    run_id=run_id or "",
                    owner=call_context.user.user_name,
                    protocol=PROTOCOL_PURE,
                )
                await self._binding_store.save(binding)
                self._pending_handoffs[updater.task_id] = _PumpHandoff(
                    subscription=subscription,
                    transport=transport,
                    binding=binding,
                    outer_context_id=updater.context_id,
                    call_context=call_context,
                    user_request=user_request,
                )
                handed_off = True
            return result
        finally:
            if not handed_off:
                subscription.unsubscribe()

    def _set_decision_request(self, user_request: str) -> None:
        """Give the decision provider the natural-language request of the current task.

        The provider stores it in a context variable, so this must run in the same asyncio task as
        the decision call. A subsequent request (the operator answer) is a new task and sets it
        again from the pending question.
        """
        setter = getattr(self._decision_provider, "set_user_request", None)
        if callable(setter):
            setter(user_request)

    def _answer_for_request(
        self, user_request: str
    ) -> Callable[[dict[str, object]], str | Awaitable[str]]:
        """Wrap the answer callback so it always runs with the request of this run."""
        base = self._answer

        def answer(event: dict[str, object]) -> str | Awaitable[str]:
            self._set_decision_request(user_request)
            return base(event)

        return answer

    def _recommendation_for_request(self, user_request: str) -> RecommendationBuilder | None:
        """Wrap the recommendation callback so it always runs with the request of this run."""
        base = self._recommendation
        if base is None:
            return None

        async def recommend(event: dict[str, object]) -> dict[str, object] | None:
            self._set_decision_request(user_request)
            return await base(event)

        return recommend

    def _question_registrar(
        self, handoff: _PumpHandoff
    ) -> Callable[[Mapping[str, object], int], None]:
        """Build the hook recording a question the background pump relayed to the operator."""
        outer_task_id = handoff.binding.outer_task_id

        def register(event: Mapping[str, object], round_number: int) -> None:
            self._pending_questions[outer_task_id] = _PendingQuestion(
                outer_task_id=outer_task_id,
                outer_context_id=handoff.outer_context_id,
                run_id=handoff.binding.run_id,
                inner_task_id=str(event.get("task_id") or handoff.binding.inner_task_id),
                inner_context_id=str(
                    event.get("context_id") or handoff.binding.inner_context_id
                ),
                round_number=round_number,
                user_request=handoff.user_request,
            )

        return register

    async def _run_ran(
        self,
        user_request: str,
        run_id: str | None,
        event_sink: Callable[[dict[str, object]], None],
        updater: TaskUpdater,
        call_context: ServerCallContext,
    ) -> ClientFlowResult:
        """Run the standard A2A client flow against the configured downstream agent."""
        self._set_decision_request(user_request)
        self._bind_wire_log(run_id)
        if self._webhook_router is not None:
            return await self._run_ran_webhook(user_request, run_id, event_sink, updater, call_context)
        config = self._config
        if self._nms_client is not None and config is not None:
            # One transport per inbound request: its event sink and frame state must not be shared
            # by two concurrent engineer requests.
            return await run_pure_client_flow(
                transport=NmsSseTransport(
                    self._nms_client,
                    protocol=PROTOCOL_PURE,
                    request_timeout=config.nms_request_timeout,
                    read_after_send=config.nms_read_after_send,
                ),
                user_request=user_request,
                answer=self._answer_or_relay,
                on_answer=lambda event, text: self._publish_answer(updater, event, text),
                max_rounds=self._max_rounds,
                call_timeout_seconds=self._timeout_seconds,
                event_sink=event_sink,
                trace_sink=self._trace_sink,
                followup=self._followup,
                plain_questions=True,
            )
        discovered = await discover_agent(
            discovery=DISCOVERY_DIRECT,
            organization=AGENT_CARD_ORGANIZATION,
            name=AGENT_CARD_NAME,
            direct_base_url=self._ran_url,
            timeout_seconds=30.0,
        )
        httpx_client = httpx.AsyncClient(
            timeout=self._timeout_seconds,
            trust_env=False,
            event_hooks={"request": [inject_outgoing, self._wire_log.record_request]},
        )
        try:
            client = await create_client(
                discovered.agent_card,
                client_config=ClientConfig(
                    streaming=True,
                    httpx_client=httpx_client,
                    supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
                    use_client_preference=True,
                ),
            )
            try:
                return await run_pure_client_flow(
                    transport=SseClientTransport(client),
                    user_request=user_request,
                    answer=self._answer,
                    on_answer=lambda event, text: self._publish_answer(updater, event, text),
                    max_rounds=self._max_rounds,
                    call_timeout_seconds=self._timeout_seconds,
                    event_sink=event_sink,
                    trace_sink=self._trace_sink,
                    followup=self._followup,
                )
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    await close()
        finally:
            await httpx_client.aclose()

    async def _answer_or_relay(self, event: dict[str, object]) -> str:
        """Answer a structured negotiation, but relay a vendor question to the caller.

        The real NMS negotiates a pure request in text: a plain agent message with no
        ``negotiationContext``, or an ``INPUT_REQUIRED`` status whose body is the question. The OSS
        Agent does not own the business answer; it publishes the question on its own Task and
        resumes the downstream context with the caller's reply.

        A status ``INPUT_REQUIRED`` is attempted first because most vendor questions are
        information requests the caller may have already answered. When the automatic answer
        produces no text (the pure guard refused to invent a value), the question is relayed
        instead: sending nothing would leave the downstream Task waiting while the operator sees no
        decision surface.
        """
        if event.get("kind") == "message" and self._nms_mode():
            raise _QuestionPendingError(event)
        result = self._answer(event)
        if inspect.isawaitable(result):
            result = await result
        text = result if isinstance(result, str) else ""
        if not text.strip() and self._nms_mode():
            raise _QuestionPendingError(event)
        return text

    async def _draft_answer(
        self,
        event: Mapping[str, object],
        *,
        already_sent: tuple[str, ...] = (),
    ) -> tuple[dict[str, object] | str | None, str | None]:
        """Ask the decision callback for a suggested answer; an agent failure is not fatal.

        Returns the recommendation and, when it was suppressed, the sample-local reason. A
        recommendation that only restates a value this run already sent cannot answer the vendor:
        the vendor asked because that value was insufficient or rejected.
        """
        try:
            if self._recommendation is not None:
                value: object = await self._recommendation(dict(event))
            else:
                value = self._answer(dict(event))
                if inspect.isawaitable(value):
                    value = await value
        except Exception:  # noqa: BLE001 - the operator can still answer manually
            _LOGGER.warning("could not draft an answer to the downstream question", exc_info=True)
            return None, None
        if isinstance(value, Mapping):
            filled = value.get("filledParams")
            if (
                isinstance(filled, Mapping)
                and filled
                and already_sent
                and not ungrounded_answer_fields("\n".join(already_sent), dict(filled))
            ):
                _LOGGER.info("suppressed a recommendation that repeats an already-sent value")
                return None, "already_sent"
            return dict(value), None
        return (value if isinstance(value, str) and value.strip() else None), None

    async def _relay_question(
        self,
        updater: TaskUpdater,
        *,
        outer_task_id: str,
        outer_context_id: str,
        run_id: str,
        event: Mapping[str, object],
        round_number: int,
        user_request: str = "",
        sent_texts: tuple[str, ...] = (),
    ) -> None:
        """Publish one downstream text question as an outer ``INPUT_REQUIRED`` status."""
        text = str(event.get("text") or "").strip() or ("The downstream requested additional information.")
        recommendation, suppressed = await self._draft_answer(event, already_sent=sent_texts)
        inner_context_id = str(event.get("context_id") or "")
        inner_task_id = str(event.get("task_id") or "")
        hop = self._downstream_hop()
        pure: dict[str, object] = {
            "hop": hop,
            "phase": "question",
            "kind": "negotiation",
            # The outer phase describes the hop ("question"); the negotiation kind comes from the
            # downstream hop so the client can report it the way A2A-T reports a propose.
            "negotiationKind": pure_negotiation_kind(pure_metadata(event)),
            "round": round_number,
            "maxRounds": self._max_rounds,
            "recommendation": recommendation or None,
            "downstreamTaskId": inner_task_id or None,
            "downstreamContextId": inner_context_id or None,
        }
        if suppressed is not None:
            pure["recommendationSuppressed"] = suppressed
        metadata = {"pure": pure, "hop": hop}
        await updater.update_status(
            TaskState.TASK_STATE_INPUT_REQUIRED,
            message=updater.new_agent_message([Part(text=text)], metadata=metadata),
            metadata=metadata,
        )
        self._pending_questions[outer_task_id] = _PendingQuestion(
            outer_task_id=outer_task_id,
            outer_context_id=outer_context_id,
            run_id=run_id,
            inner_task_id=inner_task_id,
            inner_context_id=inner_context_id,
            round_number=round_number,
            user_request=user_request,
            sent_texts=sent_texts,
        )
        _LOGGER.info(
            "relayed downstream question: outer=%s inner_context=%s round=%s",
            outer_task_id,
            inner_context_id or "-",
            round_number,
        )

    async def _continue_running_task(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        current_task: Task,
    ) -> None:
        """Forward a follow-up message on a running outer Task to its bound downstream Task.

        Standard A2A lets a caller append a message to a non-terminal Task. In pure mode the outer
        Task belongs to this agent while the vendor Task owns the actual state, so echoing the
        outer Task back would tell the operator that a modification was accepted when nothing
        reached the downstream. The text is forwarded verbatim (the operator's own words), which
        is also what a Task-T Modify carries.

        A terminal Task, an empty message or a Task without a downstream binding keeps the old
        echo behavior: there is nothing to forward, and the A2A handler decides the outcome.
        """
        message = context.message
        text = _message_text(message) if message is not None else ""
        outer_task_id = context.task_id or current_task.id
        outer_context_id = context.context_id or current_task.context_id
        if not text.strip() or current_task.status.state in _TERMINAL_TASK_STATES:
            await event_queue.enqueue_event(current_task)
            return
        binding = (
            await self._binding_store.get_by_outer_task(outer_task_id)
            if self._binding_store is not None
            else None
        )
        if binding is None:
            # Never pretend the follow-up reached the downstream. The real failure mode is a
            # message addressed to a Task that has no downstream hop left to continue.
            updater = TaskUpdater(event_queue, outer_task_id, outer_context_id)
            await updater.failed(
                message=updater.new_agent_message(
                    [Part(text="No downstream Task is bound to this A2A Task; the follow-up was not sent.")],
                    metadata=self._oss_metadata("continuation-unbound"),
                )
            )
            return
        terminal_state = await self._terminal_inner_state(binding.inner_task_id)
        if terminal_state is not None:
            updater = TaskUpdater(event_queue, outer_task_id, outer_context_id)
            await updater.failed(
                message=updater.new_agent_message(
                    [
                        Part(
                            text=(
                                f"The target Task {binding.inner_task_id} has already ended "
                                f"({terminal_state}); the follow-up was not sent. Start a new "
                                "Task instead of modifying it."
                            )
                        )
                    ],
                    metadata=self._oss_metadata("continuation-terminal"),
                )
            )
            return
        await self._take_over_pump(outer_task_id)
        self._set_decision_request(text)
        updater = TaskUpdater(event_queue, outer_task_id, outer_context_id)
        metadata = self._oss_metadata("modify")
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=updater.new_agent_message(
                [Part(text=f"OSS Agent forwarded the follow-up to the {self._downstream_name()}.")],
                metadata=metadata,
            ),
            metadata={"pure": metadata["pure"]},
        )
        pending = _PendingQuestion(
            outer_task_id=outer_task_id,
            outer_context_id=outer_context_id,
            run_id=_run_id_of(message) or "",
            inner_task_id=binding.inner_task_id,
            inner_context_id=binding.inner_context_id,
            round_number=1,
            user_request=text,
            sent_texts=(text,),
        )
        await self._continue_downstream(updater, pending, text, context.call_context)

    async def _terminal_inner_state(self, task_id: str) -> str | None:
        """Return the vendor Task's terminal state, or ``None`` when it can still be continued.

        A failed read never blocks the follow-up: the send itself will surface the vendor error,
        and a transient quota/network failure must not turn into a client-side refusal.
        """
        client = self._nms_client
        if client is None or not task_id:
            return None
        try:
            task = await client.get_task(task_id)
        except Exception:  # noqa: BLE001 - the pre-check is best effort
            _LOGGER.warning("could not re-read target Task %s before forwarding", task_id, exc_info=True)
            return None
        status = task.get("status") if isinstance(task, Mapping) else None
        state = status.get("state") if isinstance(status, Mapping) else None
        if isinstance(state, str) and state in _TERMINAL_TASK_STATE_NAMES:
            return state
        return None

    async def _take_over_pump(self, outer_task_id: str) -> None:
        """Stop the background pump of one outer Task before a new continuation writes to it."""
        handle = self._pumps.pop(outer_task_id, None)
        if handle is None:
            return
        handle.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await handle.task
        handle.subscription.unsubscribe()

    async def _continue_downstream(
        self,
        updater: TaskUpdater,
        pending: _PendingQuestion,
        text: str,
        call_context: ServerCallContext,
    ) -> None:
        """Send one follow-up text downstream and publish what the next phase reports."""
        # Every continuation belongs to the run that sent it: scope its trace and its wire file
        # here. Without this the vendor call inherited the previous run's correlation (or none)
        # and the downstream exchange was written to the previous run's ``wire-oss.jsonl``.
        self._bind_wire_log(pending.run_id or None)
        events: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        hop = self._downstream_hop()
        with trace_scope(run_id=pending.run_id or None, role="oss"):
            consumer = asyncio.create_task(
                self._consume(events, updater),
                name=f"oss-continuation-consumer-{pending.outer_task_id}",
            )
            try:
                result = await self._run_question_continuation(
                    pending,
                    text,
                    events.put_nowait,
                    updater,
                    call_context,
                )
            except _QuestionPendingError as question:
                events.put_nowait(None)
                await consumer
                await self._relay_question(
                    updater,
                    outer_task_id=pending.outer_task_id,
                    outer_context_id=pending.outer_context_id,
                    run_id=pending.run_id,
                    event=question.event,
                    round_number=pending.round_number + 1,
                    user_request=pending.user_request,
                    sent_texts=(*pending.sent_texts, text),
                )
                return
            except Exception as exc:  # noqa: BLE001 - the outer Task must reach a terminal state
                terminal = _terminal_target_failure(exc)
                if terminal is not None:
                    # The vendor refused because the target Task is already terminal. This is not
                    # a transport failure: say what happened and what the operator should do.
                    _LOGGER.info("downstream continuation refused: %s", terminal)
                    events.put_nowait(None)
                    await consumer
                    await updater.failed(
                        message=updater.new_agent_message([Part(text=terminal)])
                    )
                    return
                _LOGGER.exception("OSS downstream continuation failed")
                events.put_nowait(None)
                await consumer
                await updater.failed(
                    message=updater.new_agent_message([Part(text=f"The {hop} hop failed: {exc}")])
                )
                return
            events.put_nowait(None)
            await consumer
            await self._publish_result(updater, result)

    async def _resume_question(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        pending: _PendingQuestion,
    ) -> None:
        """Forward the caller's answer to the downstream and continue the run."""
        updater = TaskUpdater(event_queue, pending.outer_task_id, pending.outer_context_id)
        answer_text = _message_text(context.message) if context.message is not None else ""
        if not answer_text.strip():
            # Keep the turn with the caller instead of ending it: the question is still open.
            await updater.update_status(
                TaskState.TASK_STATE_INPUT_REQUIRED,
                message=updater.new_agent_message(
                    [Part(text="The answer was empty; the OSS Agent is still waiting for it.")],
                    metadata={"pure": {"hop": "oss", "phase": "question"}, "hop": "oss"},
                ),
            )
            self._pending_questions[pending.outer_task_id] = pending
            return
        # The answer arrives in a new request task: re-establish the original request before the
        # next decision call, otherwise the provider (a context variable) sees no request at all.
        self._set_decision_request(pending.user_request)
        metadata = self._oss_metadata("answer")
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=updater.new_agent_message(
                [Part(text=f"OSS Agent answered the {self._downstream_name()}: {answer_text}")],
                metadata=metadata,
            ),
            metadata={"pure": metadata["pure"]},
        )
        await self._continue_downstream(updater, pending, answer_text, context.call_context)

    async def _run_question_continuation(
        self,
        pending: _PendingQuestion,
        answer_text: str,
        event_sink: Callable[[dict[str, object]], None],
        updater: TaskUpdater,
        call_context: ServerCallContext,
    ) -> ClientFlowResult:
        """Send the caller's answer on the downstream context and consume the next phase."""
        if self._webhook_router is not None and self._nms_client is not None:
            subscription = self._webhook_router.subscribe(pending.run_id)
            if pending.inner_context_id and pending.inner_task_id:
                subscription.bind(
                    task_id=pending.inner_task_id,
                    context_id=pending.inner_context_id,
                )
            elif pending.inner_context_id:
                subscription.bind(None, pending.inner_context_id)
            handed_off = False
            try:
                transport = NmsWebhookTransport(
                    self._nms_client,
                    queue=subscription.queue,
                    push_url=self._webhook_push_url,
                    push_token=self._webhook_push_token,
                    push_auth_credentials=(
                        self._config.webhook_auth_credentials if self._config is not None else ""
                    ),
                    protocol=PROTOCOL_PURE,
                    idle_timeout=(
                        self._long_task_settle_seconds
                        if self._long_task_enabled()
                        else self._webhook_idle_timeout
                    ),
                    bind=subscription.bind,
                    context_id=pending.inner_context_id,
                    # The original request already registered the callback for this context; a
                    # second registration is rejected by the real NMS.
                    register_push_config=False,
                )
                result = await continue_pure_client_flow(
                    transport=transport,
                    text=answer_text,
                    task_id=pending.inner_task_id or None,
                    context_id=pending.inner_context_id or None,
                    answer=self._answer_or_relay,
                    on_answer=lambda event, text: self._publish_answer(updater, event, text),
                    max_rounds=self._max_rounds,
                    call_timeout_seconds=self._timeout_seconds,
                    event_sink=event_sink,
                    trace_sink=self._trace_sink,
                    followup=self._followup,
                    plain_questions=True,
                )
                if (
                    self._long_task_enabled()
                    and self._binding_store is not None
                    and result.task_id
                    and result.final_state == FINAL_STATE_INCOMPLETE
                ):
                    # The answer did not end the vendor Task: hand it back to a fresh pump instead
                    # of releasing the run's only tracker.
                    binding = DownstreamBinding(
                        inner_task_id=result.task_id,
                        inner_context_id=result.context_id or "",
                        outer_task_id=updater.task_id,
                        run_id=pending.run_id,
                        owner=call_context.user.user_name,
                        protocol=PROTOCOL_PURE,
                    )
                    await self._binding_store.save(binding)
                    self._pending_handoffs[updater.task_id] = _PumpHandoff(
                        subscription=subscription,
                        transport=transport,
                        binding=binding,
                        outer_context_id=updater.context_id,
                        call_context=call_context,
                        user_request=pending.user_request,
                    )
                    handed_off = True
                elif self._binding_store is not None and result.task_id:
                    # The pump suspended when it relayed the question; the answer ended the Task,
                    # so retire the binding it left active.
                    try:
                        await self._binding_store.mark_terminal(
                            result.task_id,
                            result.context_id or "",
                        )
                    except Exception:  # noqa: BLE001 - tracking is best effort
                        _LOGGER.warning("could not retire the downstream binding", exc_info=True)
                return result
            finally:
                if not handed_off:
                    subscription.unsubscribe()
        if self._nms_client is not None and self._config is not None:
            return await continue_pure_client_flow(
                transport=NmsSseTransport(
                    self._nms_client,
                    protocol=PROTOCOL_PURE,
                    request_timeout=self._config.nms_request_timeout,
                    read_after_send=self._config.nms_read_after_send,
                ),
                text=answer_text,
                task_id=pending.inner_task_id or None,
                context_id=pending.inner_context_id or None,
                answer=self._answer_or_relay,
                on_answer=lambda event, text: self._publish_answer(updater, event, text),
                max_rounds=self._max_rounds,
                call_timeout_seconds=self._timeout_seconds,
                event_sink=event_sink,
                trace_sink=self._trace_sink,
                followup=self._followup,
                plain_questions=True,
            )
        raise RuntimeError("The NMS downstream is not started.")

    def _bind_wire_log(self, run_id: str | None) -> None:
        """Point both downstream captures at this run's ``wire-oss.jsonl``.

        The OSS hop writes its own file: the engineer-client process appends to ``wire.jsonl`` for
        the same run, and two processes appending to one file is not atomic on every platform.
        """
        config = self._config
        if config is None:
            return
        target = Path(config.runs_dir) / run_id / WIRE_OSS_FILENAME if run_id else None
        self._wire_log.set_run(config.runs_dir, run_id, filename=WIRE_OSS_FILENAME)
        if self._nms_client is not None:
            self._nms_client.set_wire_path(target)

    async def _consume(
        self,
        queue: asyncio.Queue[dict[str, object] | None],
        updater: TaskUpdater,
    ) -> None:
        """Map every RAN event to one outer Task status/artifact, in order."""
        while True:
            event = await queue.get()
            if event is None:
                return
            try:
                await self._publish(event, updater)
            except Exception:  # noqa: BLE001 - aggregation must not break the run
                _LOGGER.exception("failed to map one RAN event")

    async def _publish_answer(
        self,
        updater: TaskUpdater,
        event: dict[str, object],
        text: str,
    ) -> None:
        """Publish the OSS Agent's answer before the follow-up message reaches RAN."""
        pure = _pure_metadata(event)
        metadata = self._oss_metadata("answer")
        tags = metadata["pure"]
        assert isinstance(tags, dict)
        tags["kind"] = "answer"
        tags["round"] = pure.get("round")
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=updater.new_agent_message(
                [Part(text=f"OSS Agent answered the {self._downstream_name()}: {text}")],
                metadata=metadata,
            ),
            metadata=metadata,
        )

    async def _publish(self, event: dict[str, object], updater: TaskUpdater) -> None:
        """Publish one RAN event on the OSS Task with its hop tag."""
        kind = event.get("kind")
        if kind == "artifact":
            await self._publish_artifact(event, updater)
            return
        if kind != "status":
            return
        state = event.get("state")
        pure = _pure_metadata(event)
        hop = self._downstream_hop()
        metadata = {"pure": {**pure, "hop": hop}, "hop": hop}
        text = event.get("text")
        if state == "TASK_STATE_INPUT_REQUIRED":
            if pure.get("kind") == "approval":
                metadata = {
                    "pure": {**pure, "hop": "ran->mie", "phase": "approval"},
                    "hop": "ran->mie",
                }
                text = "RAN Agent is waiting for the MIE Agent's approval."
            else:
                text = str(text or "RAN Agent is waiting for the OSS Agent's answer.")
        elif state == "TASK_STATE_WORKING" and not text:
            text = "RAN Agent is working."
        if not isinstance(text, str) or not text:
            return
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=updater.new_agent_message([Part(text=text)], metadata=metadata),
            metadata=metadata,
        )

    async def _publish_artifact(self, event: dict[str, object], updater: TaskUpdater) -> None:
        """Forward one RAN artifact with a hop tag."""
        parts: list[Part] = []
        data = event.get("data")
        if isinstance(data, Mapping):
            part = Part()
            ParseDict(dict(data), part.data)
            parts.append(part)
        text = event.get("text")
        if isinstance(text, str) and text:
            parts.append(Part(text=text))
        if not parts:
            return
        metadata = event.get("metadata")
        merged = dict(metadata) if isinstance(metadata, Mapping) else {}
        merged["hop"] = self._downstream_hop()
        await updater.add_artifact(
            parts,
            name=str(event.get("name") or "ran artifact"),
            metadata=merged,
            last_chunk=True,
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancel the outer Task and stop its background pump."""
        task_id = context.task_id or ""
        self._pending_handoffs.pop(task_id, None)
        self._pending_questions.pop(task_id, None)
        handle = self._pumps.pop(task_id, None)
        if handle is not None:
            handle.task.cancel()
            handle.subscription.unsubscribe()
        updater = TaskUpdater(event_queue, task_id, context.context_id or "")
        await updater.cancel()


def build_oss_agent_card(*, host: str, port: int) -> AgentCard:
    """Build the AgentCard of the OSS Agent."""
    card = AgentCard(
        name=OSS_AGENT_NAME,
        description=OSS_AGENT_DESCRIPTION,
        version="1.0.0",
        provider=AgentProvider(organization=OSS_AGENT_ORGANIZATION, url=OSS_AGENT_PROVIDER_URL),
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json", "text/plain"],
        skills=[
            AgentSkill(
                id=OSS_AGENT_SKILL_ID,
                name="RAN energy saving request",
                description=OSS_AGENT_DESCRIPTION,
                tags=["energy saving", "request"],
                examples=["Create a RAN energy saving task in Region A."],
            )
        ],
    )
    card.supported_interfaces.add(
        url=f"http://{host}:{port}",
        protocol_binding=TransportProtocol.HTTP_JSON.value,
        protocol_version="1.0.0",
    )
    return card


def build_oss_app(
    *,
    config: SampleConfig | None = None,
    ran_url: str | None = None,
    answer: Callable[[dict[str, object]], str | Awaitable[str]] | None = None,
    recommendation: RecommendationBuilder | None = None,
    decision_provider: object | None = None,
    task_store: TaskStore | None = None,
    binding_store: DownstreamBindingStore | None = None,
    push_config_store: PushNotificationConfigStore | None = None,
    push_sender: PushNotificationSender | None = None,
    long_task_mode: str | None = None,
    long_task_settle_seconds: float | None = None,
    host: str | None = None,
    port: int | None = None,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    intent_processor: IntentProcessor | None = None,
    language: str = LANGUAGE_ZH,
    followup: str = PURE_FOLLOWUP_BOTH,
    trace_emitter: HttpTraceEmitter | FileTraceEmitter | None = None,
) -> Starlette:
    """Build the OSS A2A app.

    Args:
        config: sample configuration; selects the downstream hop.
        ran_url: local RAN Agent base URL (direct topology).
        answer: decision callback for a downstream ``INPUT_REQUIRED``.
        decision_provider: optional live decision provider.
        task_store: outer Task store; defaults to the in-memory store. The launcher passes the
            SQLite store so a task that runs for days survives a process restart.
        binding_store: outer↔downstream binding store; derived from a SQLite Task store when
            omitted, so the pump can persist and resume long-running tasks.
        push_config_store: client webhook registrations; defaults to the SDK in-memory store.
        push_sender: push-notification sender; defaults to the SDK sender over an owned httpx
            client. Tests inject one over a mock transport.
        long_task_mode: overrides ``SampleConfig.long_task_mode`` for tests.
        long_task_settle_seconds: overrides ``SampleConfig.long_task_settle_seconds`` for tests.
        host: advertised OSS host.
        port: advertised OSS port.
        timeout_seconds: A2A call budget of the downstream hop.
        intent_processor: business intent layer run before the downstream hop.
        language: process-level language used by the intent processor.
        followup: conversation fields carried by one pure follow-up.
        trace_emitter: cross-process Run Explorer sink.
    """
    resolved_host = host or (
        config.oss_host if config is not None else os.environ.get("A2AT_SAMPLE_OSS_HOST", "127.0.0.1")
    )
    resolved_port = port or (
        config.oss_port if config is not None else int(os.environ.get("A2AT_SAMPLE_OSS_PORT", str(DEFAULT_OSS_PORT)))
    )
    resolved_ran_url = ran_url or (
        config.ran_url if config is not None else os.environ.get("A2AT_SAMPLE_RAN_URL", f"http://127.0.0.1:{DEFAULT_RAN_PORT}")
    )
    resolved_answer = answer or (lambda event: _proposal_of(event))
    card = build_oss_agent_card(host=resolved_host, port=resolved_port)
    resolved_task_store = task_store or InMemoryTaskStore()
    resolved_binding_store = binding_store
    if resolved_binding_store is None and isinstance(resolved_task_store, SqliteTaskStore):
        # One database for the outer Tasks, the bindings and the client webhook registrations.
        resolved_binding_store = DownstreamBindingStore(resolved_task_store.path)
    # A caller-supplied webhook URL must not become an arbitrary outbound request target; the
    # gateway uses the same validating wrapper.
    resolved_push_store = ValidatingPushConfigStore(push_config_store or InMemoryPushNotificationConfigStore())
    owned_push_client: httpx.AsyncClient | None = None
    if push_sender is None:
        owned_push_client = httpx.AsyncClient(trust_env=False)
        resolved_push_sender: PushNotificationSender = BasePushNotificationSender(
            owned_push_client,
            resolved_push_store,
        )
    else:
        resolved_push_sender = push_sender
    executor = OSSPureExecutor(
        config=config,
        ran_url=resolved_ran_url,
        answer=resolved_answer,
        recommendation=recommendation,
        decision_provider=decision_provider,
        timeout_seconds=timeout_seconds,
        intent_processor=intent_processor,
        language=language,
        followup=followup,
        trace_sink=trace_emitter,
        task_store=resolved_task_store,
        binding_store=resolved_binding_store,
        push_sender=resolved_push_sender,
        long_task_mode=long_task_mode if long_task_mode is not None else (
            config.long_task_mode if config is not None else DEFAULT_LONG_TASK_MODE
        ),
        long_task_settle_seconds=(
            long_task_settle_seconds
            if long_task_settle_seconds is not None
            else (config.long_task_settle_seconds if config is not None else DEFAULT_LONG_TASK_SETTLE_SECONDS)
        ),
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        del app
        # The NMS client records its token/send/poll spans through the process-wide sink; the
        # per-run trace scope supplies the run id, so the Web UI shows the real downstream hop.
        set_global_sink(trace_emitter)
        await executor.start()
        try:
            yield
        finally:
            await executor.stop()
            set_global_sink(None)
            if trace_emitter is not None:
                trace_emitter.close()
            if owned_push_client is not None:
                await owned_push_client.aclose()

    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=resolved_task_store,
        agent_card=card,
        push_config_store=resolved_push_store,
        push_sender=resolved_push_sender,
    )
    app = Starlette(
        routes=[
            *create_agent_card_routes(card),
            *create_rest_routes(request_handler),
        ],
        lifespan=lifespan,
    )
    app.add_middleware(TracePropagationMiddleware)
    app.state.agent_card = card
    app.state.request_handler = request_handler
    app.state.task_store = resolved_task_store
    app.state.binding_store = resolved_binding_store
    app.state.push_config_store = resolved_push_store
    app.state.push_sender = resolved_push_sender
    return app


def main() -> None:
    """Entry point: serve the OSS Agent on the configured port."""
    configure_logging()
    env_path = Path.cwd() / ".env"
    config = load_config(env_path=env_path if env_path.exists() else None)
    # The launcher writes these keys into the per-run ``.env``; a manual start must read the same
    # file instead of falling back to ``os.environ`` and binding the default port.
    host = config.oss_host
    port = config.oss_port
    from demo.intent import build_demo_intent_processor

    decision_provider: object | None = None
    recommendation: RecommendationBuilder | None = None
    if config.discovery == DISCOVERY_NMS:
        if config.is_offline:
            # The simulator runs offline with the deterministic fake model; a scripted answer
            # keeps the pure round-trip testable without a live endpoint. The simulator serves
            # 00:00~06:00 and accepts a 30% rate, so the answer fills both missing values.
            def answer(event: dict[str, object]) -> str:
                del event
                return "{00:00~06:00,30Mbps}"

        else:
            from demo.agents.client_agent.agent import build_client_agent

            from protocol.decision_provider import AdkDecisionProvider

            policy = ClientPolicy.from_config(config)

            def agent_factory(model: object) -> object:
                """Build the decision agent with this process's client policy."""
                return build_client_agent(model, policy=policy)  # ty: ignore[invalid-argument-type]

            decision_provider = AdkDecisionProvider(
                agent_factory=agent_factory,
                offline=False,
            )
            answer = build_live_pure_answer(decision_provider, policy=policy)
            recommendation = build_live_pure_recommendation(decision_provider, policy=policy)
    else:
        from demo.pure_policy import pure_answer_from_event

        def answer(event: dict[str, object]) -> str:
            return pure_answer_from_event(event, config.language)

    task_store: TaskStore | None = None
    push_config_store: PushNotificationConfigStore | None = None
    if config.oss_task_db_path:
        # One database owns the outer Tasks, the client webhook registrations and the downstream
        # bindings, so a task that runs for days survives an OSS restart.
        task_store = SqliteTaskStore(config.oss_task_db_path)
        push_config_store = SqlitePushNotificationConfigStore(config.oss_task_db_path)
        _LOGGER.info("OSS task persistence: %s", config.oss_task_db_path)

    app = build_oss_app(
        config=config,
        host=host,
        port=port,
        answer=answer,
        recommendation=recommendation,
        decision_provider=decision_provider,
        task_store=task_store,
        push_config_store=push_config_store,
        intent_processor=build_demo_intent_processor(config),
        language=config.language,
        followup=config.pure_followup,
        trace_emitter=_build_trace_emitter(config),
    )
    _LOGGER.info("starting OSS Agent on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port)


def _build_trace_emitter(config: SampleConfig | None = None) -> HttpTraceEmitter | FileTraceEmitter | None:
    """Build the OSS trace emitter from the resolved configuration.

    ``A2AT_SAMPLE_TRACE_URL`` selects the Web UI endpoint (with ``A2AT_SAMPLE_TRACE_TOKEN``);
    ``A2AT_SAMPLE_TRACE_DIR`` selects the CLI JSONL file. Without either key the hop is untraced.
    A caller without a resolved config keeps the process-environment fallback (tests, embedding).
    """
    base_url = (config.trace_url if config is not None else "") or os.environ.get(
        "A2AT_SAMPLE_TRACE_URL", ""
    ).strip()
    if base_url:
        token = (config.trace_token if config is not None else "") or os.environ.get(
            "A2AT_SAMPLE_TRACE_TOKEN", ""
        ).strip()
        # The OSS Agent reports spans to the UI instance that owns the run. Say which UI and
        # whether the token is present: a stale pairing shows up later as a 404/403 on every
        # batch, and this line is the first place to look.
        if token:
            _LOGGER.info("OSS trace emitter: %s (token configured)", base_url)
        else:
            _LOGGER.warning(
                "OSS trace emitter: %s has no A2AT_SAMPLE_TRACE_TOKEN; the UI rejects every span",
                base_url,
            )
        return HttpTraceEmitter(base_url, token)
    directory = (config.trace_dir if config is not None else "") or os.environ.get(
        "A2AT_SAMPLE_TRACE_DIR", ""
    ).strip()
    if directory:
        _LOGGER.info("OSS trace emitter: file %s", directory)
        return FileTraceEmitter(Path(directory))
    _LOGGER.info("OSS trace emitter: disabled (no A2AT_SAMPLE_TRACE_URL/TRACE_DIR)")
    return None


def _pure_proposal(event: Mapping[str, object]) -> tuple[NegotiationProposeView, NegotiationContext]:
    """Build the provider-facing proposal of one pure downstream question."""
    text = str(event.get("text") or "").strip()
    pure = _pure_metadata(event)
    raw_metadata = event.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    negotiation_context = metadata.get("negotiationContext")
    nested = negotiation_context if isinstance(negotiation_context, Mapping) else {}

    def _int(value: object, default: int) -> int:
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    round_number = _int(pure.get("round", nested.get("round")), 1)
    max_rounds = _int(pure.get("maxRounds", nested.get("maxRounds")), 3)
    template_uri = metadata.get("templateUri")
    kind = "information"
    if isinstance(template_uri, str) and template_uri:
        from protocol.negotiation_policy import (
            negotiation_kind_for_propose_content,
            negotiation_kind_for_template_uri,
        )

        resolved = negotiation_kind_for_template_uri(template_uri)
        if resolved is None:
            resolved = negotiation_kind_for_propose_content(text)
        if resolved is not None:
            kind = resolved.value
    context_id = str(event.get("context_id") or event.get("task_id") or "pure-negotiation")
    propose = NegotiationProposeView(
        negotiation_id=context_id,
        kind=kind,
        round=round_number,
        max_rounds=max_rounds,
        content=text or "The NMS requested additional information.",
        template_uri=template_uri if isinstance(template_uri, str) else None,
        context_id=context_id,
    )
    session = NegotiationContext(
        id=context_id,
        round=round_number,
        max_rounds=max_rounds,
        performative=NegotiationPerformative.PROPOSE,
    )
    return propose, session


def build_live_pure_answer(
    provider: _PureDecisionProvider,
    policy: ClientPolicy | None = None,
) -> Callable[[dict[str, object]], Awaitable[str]]:
    """Build the live pure-A2A answer callback around an ADK decision provider.

    The vendor's ``INPUT_REQUIRED`` is free text, while the decision agent returns a structured
    :class:`~shared.models.NegotiationDecision`; :func:`~demo.pure_policy.render_pure_answer`
    renders it back to the text the vendor expects. The decision is sanitized first: a value the
    original request or the question does not state is never rendered into the answer, because the
    pure hop has no structured channel to correct it afterwards.
    """
    from demo.pure_policy import render_pure_answer

    async def answer(event: dict[str, object]) -> str:
        propose, session = _pure_proposal(event)
        decision = await provider.respond(propose, session)
        grounded, notes = sanitize_pure_decision(
            decision,
            request_text=_provider_request_text(provider),
            propose=propose,
            policy=policy or ClientPolicy(),
        )
        if notes:
            _LOGGER.info("pure answer sanitized: %s", "; ".join(notes))
        return render_pure_answer(grounded) if grounded is not None else ""

    return answer


def build_live_pure_recommendation(
    provider: _PureDecisionProvider,
    policy: ClientPolicy | None = None,
) -> Callable[[dict[str, object]], Awaitable[dict[str, object] | None]]:
    """Build the recommendation callback used when the OSS relays a downstream question.

    It returns a structured recommendation so the confirmation card can show the model's action
    (accept/counter) instead of flattening every answer into approval. The recommendation is
    sanitized exactly like the automatic answer: the operator may click "use", so the page must
    not offer a value the request and the question do not state. A rejection or abort is not an
    answer: the operator gets no recommendation and supplies the text manually.
    """
    from demo.pure_policy import render_pure_answer

    async def recommend(event: dict[str, object]) -> dict[str, object] | None:
        propose, session = _pure_proposal(event)
        decision = await provider.respond(propose, session)
        grounded, notes = sanitize_pure_decision(
            decision,
            request_text=_provider_request_text(provider),
            propose=propose,
            policy=policy or ClientPolicy(),
        )
        if notes:
            _LOGGER.info("pure recommendation sanitized: %s", "; ".join(notes))
        if grounded is None or grounded.action not in ("accept", "counter"):
            return None
        return {
            "action": grounded.action,
            "rationale": grounded.rationale,
            "text": render_pure_answer(grounded),
            "filledParams": dict(grounded.filled_params or {}),
            "counterProposal": grounded.counter_proposal,
        }

    return recommend


def _provider_request_text(provider: _PureDecisionProvider) -> str:
    """Return the operator's request the provider carries for the current hop."""
    getter = getattr(provider, "grounding_text", None)
    return getter() if callable(getter) else ""


def a2a_request_text(intent: ProcessedIntent) -> str:
    """Return the natural-language text of one standard A2A hop.

    The A2A-T hop uses ``normalized_text``; a standard A2A message must not carry the Task-T
    section layout, so it uses ``plain_text`` and falls back to the operator's raw request.
    """
    return intent.plain_text or intent.raw_request or intent.normalized_text


def _run_id_of(message: object) -> str | None:
    """Return the Run Explorer id of one A2A message.

    The current contract carries it on ``X-A2AT-Run-Id`` (exposed through the propagation
    context); the message metadata remains the fallback for an external caller.
    """
    metadata = getattr(message, "metadata", None)
    value = metadata_to_dict(metadata).get(SAMPLE_RUN_ID_METADATA_KEY)
    if isinstance(value, str) and value:
        return value
    return current_run_id() or None


def _message_text(message: object) -> str:
    """Return the first non-empty text part of one A2A message."""
    for part in getattr(message, "parts", ()) or ():
        text = getattr(part, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _terminal_target_failure(exc: BaseException) -> str | None:
    """Return the operator-facing message when the vendor refused a terminal Task.

    The vendor answers a follow-up to an already-ended Task with HTTP 400 and
    ``task is in terminal state TASK_STATE_*``. That is a target condition, not a transport
    failure, so it must not be reported as "the NMS hop failed".
    """
    if not isinstance(exc, NmsProtocolError) or exc.http_status != 400:
        return None
    if "terminal state" not in str(exc).lower():
        return None
    match = re.search(r"terminal state (TASK_STATE_[A-Z]+)", str(exc))
    state = match.group(1) if match is not None else "a terminal state"
    return (
        f"The target Task has already ended ({state}); the follow-up was not sent. "
        "Start a new Task instead of modifying it."
    )


def _last_status_text(events: list[dict[str, object]]) -> str:
    """Return the last non-empty status message of one finished downstream flow."""
    for event in reversed(events):
        if event.get("kind") != "status":
            continue
        text = event.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _pure_metadata(event: Mapping[str, object]) -> dict[str, object]:
    """Return the sample-local ``pure`` metadata of one normalized event."""
    metadata = event.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    raw = metadata.get("pure")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _proposal_of(event: Mapping[str, object]) -> str:
    """Fallback answer: echo the server's proposal when it has one."""
    proposal = _pure_metadata(event).get("proposal")
    return proposal if isinstance(proposal, str) else ""


def _rounds(events: list[dict[str, object]]) -> int:
    """Count the distinct negotiation rounds observed from RAN."""
    rounds = {
        int(pure.get("round", 0) or 0)
        for event in events
        if isinstance(event.get("metadata"), Mapping)
        for pure in [_pure_metadata(event)]
        if pure.get("kind") == "negotiation"
    }
    return len({round_number for round_number in rounds if round_number > 0})


__all__ = [
    "DEFAULT_OSS_PORT",
    "DEFAULT_RAN_PORT",
    "OSS_AGENT_NAME",
    "OSSPureExecutor",
    "a2a_request_text",
    "build_oss_agent_card",
    "build_oss_app",
    "main",
]


if __name__ == "__main__":
    main()
