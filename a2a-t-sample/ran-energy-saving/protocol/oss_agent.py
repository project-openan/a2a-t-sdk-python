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
import logging
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from pathlib import Path
from typing import Final

import httpx
import uvicorn
from a2a.client import ClientConfig, create_client
from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_rest_routes
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_store import TaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
    Part,
    TaskState,
)
from a2a.utils.constants import TransportProtocol
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from google.protobuf.json_format import ParseDict
from shared.agent_card import AGENT_CARD_NAME, AGENT_CARD_ORGANIZATION
from shared.config import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DISCOVERY_NMS,
    PURE_FOLLOWUP_BOTH,
    TRANSPORT_WEBHOOK,
    SampleConfig,
    load_config,
)
from shared.logging_setup import configure_logging
from shared.models import NegotiationProposeView
from shared.run_constants import LANGUAGE_ZH, PROTOCOL_PURE
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
from protocol.discovery import DISCOVERY_DIRECT, discover_agent
from protocol.intent import IntentProcessor, OperatorRequest, ProcessedIntent
from protocol.messages import SAMPLE_RUN_ID_METADATA_KEY, metadata_to_dict
from protocol.nms_client import NmsClient, NmsConfig, NmsSseTransport, NmsWebhookTransport
from protocol.pure_client_flow import run_pure_client_flow
from protocol.shared_webhook import WebhookRouter
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


class OSSPureExecutor(AgentExecutor):
    """Forward one engineer request to RAN and aggregate the hop events."""

    def __init__(
        self,
        *,
        config: SampleConfig | None = None,
        ran_url: str | None = None,
        answer: Callable[[dict[str, object]], str | Awaitable[str]],
        decision_provider: object | None = None,
        max_rounds: int = 3,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        intent_processor: IntentProcessor | None = None,
        language: str = LANGUAGE_ZH,
        followup: str = PURE_FOLLOWUP_BOTH,
        trace_sink: Callable[[dict[str, object]], None] | None = None,
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
        """
        self._config = config
        self._ran_url = (ran_url or "").rstrip("/")
        self._answer = answer
        self._decision_provider = decision_provider
        self._max_rounds = max_rounds
        self._timeout_seconds = timeout_seconds
        self._intent_processor = intent_processor
        self._language = language
        self._followup = followup
        self._trace_sink = trace_sink
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
        if self._config is None or self._config.discovery != DISCOVERY_NMS:
            return
        config = self._config
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
        # SSE mode needs no process resource: the per-run transport is built in ``_run_ran``.

    async def stop(self) -> None:
        """Stop the webhook receiver and close the NMS client."""
        receiver = self._webhook_receiver
        if receiver is not None:
            await receiver.stop()
            self._webhook_receiver = None
        self._webhook_router = None
        if self._nms_client is not None:
            await self._nms_client.aclose()
            self._nms_client = None

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Create the outer Task, run the RAN hop, and publish the aggregated result."""
        if context.current_task is not None:
            await event_queue.enqueue_event(context.current_task)
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
        downstream = self._downstream_name()
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=updater.new_agent_message(
                [Part(text=f"OSS Agent received the request and is forwarding it to the {downstream}.")],
                metadata={"pure": {"hop": "oss", "phase": "receive"}, "hop": "oss"},
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
                result = await self._run_ran(
                    a2a_request_text(intent),
                    run_id,
                    events.put_nowait,
                    updater,
                )
        except Exception as exc:  # noqa: BLE001 - the outer Task must reach a terminal state
            _LOGGER.exception("OSS->RAN hop failed")
            events.put_nowait(None)
            await consumer
            await updater.failed(message=updater.new_agent_message([Part(text=f"The RAN hop failed: {exc}")]))
            return
        events.put_nowait(None)
        await consumer

        if result.final_state == "task_completed":
            await updater.update_status(
                TaskState.TASK_STATE_COMPLETED,
                message=updater.new_agent_message(
                    [Part(text="OSS Agent completed the request.")],
                    metadata={
                        "pure": {
                            "hop": "oss",
                            "phase": "report",
                            "rounds": _rounds(result.events),
                            "messages": len(result.events),
                            "hops": self._hop_count(),
                        },
                        "hop": "oss",
                    },
                ),
                metadata={
                    "pure": {
                        "hop": "oss",
                        "rounds": _rounds(result.events),
                        "messages": len(result.events),
                        "hops": self._hop_count(),
                    }
                },
            )
            return
        if result.final_state == "task_rejected":
            await updater.update_status(
                TaskState.TASK_STATE_REJECTED,
                message=updater.new_agent_message(
                    [Part(text="The RAN Agent rejected the request.")],
                    metadata={"pure": {"hop": "ran"}, "hop": "ran"},
                ),
            )
            return
        await updater.failed(
            message=updater.new_agent_message([Part(text=f"The RAN hop ended with state {result.final_state}.")])
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
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=updater.new_agent_message(
                [Part(text=f"Business intent ready: {intent.region or intent.operation_type}")],
                metadata={
                    "pure": {"hop": "oss", "phase": "intent"},
                    "intentProcessed": intent.to_dict(),
                },
            ),
        )

    async def _run_ran_webhook(
        self,
        user_request: str,
        run_id: str | None,
        event_sink: Callable[[dict[str, object]], None],
        updater: TaskUpdater,
    ) -> ClientFlowResult:
        """Run one inbound request against the NMS over its shared callback port.

        Each inbound request gets its own router subscription: the NMS ignores push tokens, so
        routing by the downstream task/context identity is what keeps concurrent requests from
        consuming each other's events.
        """
        router = self._webhook_router
        nms_client = self._nms_client
        if router is None or nms_client is None:
            raise RuntimeError("The NMS webhook transport is not started.")
        subscription = router.subscribe(run_id or "")
        try:
            transport = NmsWebhookTransport(
                nms_client,
                queue=subscription.queue,
                push_url=self._webhook_push_url,
                push_token=self._webhook_push_token,
                protocol=PROTOCOL_PURE,
                idle_timeout=self._webhook_idle_timeout,
                bind=subscription.bind,
                context_id=subscription.key,
            )
            return await run_pure_client_flow(
                transport=transport,
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
            subscription.unsubscribe()

    async def _run_ran(
        self,
        user_request: str,
        run_id: str | None,
        event_sink: Callable[[dict[str, object]], None],
        updater: TaskUpdater,
    ) -> ClientFlowResult:
        """Run the standard A2A client flow against the configured downstream agent."""
        setter = getattr(self._decision_provider, "set_user_request", None)
        if callable(setter):
            setter(user_request)
        self._bind_wire_log(run_id)
        if self._webhook_router is not None:
            return await self._run_ran_webhook(user_request, run_id, event_sink, updater)
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
                answer=self._answer,
                on_answer=lambda event, text: self._publish_answer(updater, event, text),
                max_rounds=self._max_rounds,
                call_timeout_seconds=self._timeout_seconds,
                event_sink=event_sink,
                trace_sink=self._trace_sink,
                followup=self._followup,
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
        metadata = {
            "pure": {
                "hop": "oss",
                "phase": "answer",
                "kind": "answer",
                "round": pure.get("round"),
            },
            "hop": "oss",
        }
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
        """Cancel the outer Task."""
        updater = TaskUpdater(event_queue, context.task_id or "", context.context_id or "")
        await updater.cancel()


def build_oss_agent_card(*, host: str, port: int) -> AgentCard:
    """Build the AgentCard of the OSS Agent."""
    card = AgentCard(
        name=OSS_AGENT_NAME,
        description=OSS_AGENT_DESCRIPTION,
        version="1.0.0",
        provider=AgentProvider(organization=OSS_AGENT_ORGANIZATION, url=OSS_AGENT_PROVIDER_URL),
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
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
    decision_provider: object | None = None,
    task_store: TaskStore | None = None,
    host: str | None = None,
    port: int | None = None,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    intent_processor: IntentProcessor | None = None,
    language: str = LANGUAGE_ZH,
    followup: str = PURE_FOLLOWUP_BOTH,
    trace_emitter: HttpTraceEmitter | FileTraceEmitter | None = None,
) -> Starlette:
    """Build the OSS A2A app."""
    resolved_host = host or os.environ.get("A2AT_SAMPLE_OSS_HOST", "127.0.0.1")
    resolved_port = port or int(os.environ.get("A2AT_SAMPLE_OSS_PORT", str(DEFAULT_OSS_PORT)))
    resolved_ran_url = ran_url or os.environ.get("A2AT_SAMPLE_RAN_URL", f"http://127.0.0.1:{DEFAULT_RAN_PORT}")
    resolved_answer = answer or (lambda event: _proposal_of(event))
    card = build_oss_agent_card(host=resolved_host, port=resolved_port)
    executor = OSSPureExecutor(
        config=config,
        ran_url=resolved_ran_url,
        answer=resolved_answer,
        decision_provider=decision_provider,
        timeout_seconds=timeout_seconds,
        intent_processor=intent_processor,
        language=language,
        followup=followup,
        trace_sink=trace_emitter,
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

    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store or InMemoryTaskStore(),
        agent_card=card,
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
    return app


def main() -> None:
    """Entry point: serve the OSS Agent on the configured port."""
    configure_logging()
    env_path = Path.cwd() / ".env"
    config = load_config(env_path=env_path if env_path.exists() else None)
    host = os.environ.get("A2AT_SAMPLE_OSS_HOST", "127.0.0.1")
    port = int(os.environ.get("A2AT_SAMPLE_OSS_PORT", str(DEFAULT_OSS_PORT)))
    from demo.intent import build_demo_intent_processor

    decision_provider: object | None = None
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

            decision_provider = AdkDecisionProvider(
                agent_factory=build_client_agent,
                offline=False,
                default_region=config.nms_region or None,
            )
            answer = build_live_pure_answer(decision_provider)
    else:
        from demo.pure_policy import pure_answer_from_event

        def answer(event: dict[str, object]) -> str:
            return pure_answer_from_event(event, config.language)

    app = build_oss_app(
        config=config,
        host=host,
        port=port,
        answer=answer,
        decision_provider=decision_provider,
        intent_processor=build_demo_intent_processor(config),
        language=config.language,
        followup=config.pure_followup,
        trace_emitter=_build_trace_emitter(),
    )
    _LOGGER.info("starting OSS Agent on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port)


def _build_trace_emitter() -> HttpTraceEmitter | FileTraceEmitter | None:
    """Build the OSS trace emitter configured by the process environment.

    ``A2AT_SAMPLE_TRACE_URL`` selects the Web UI endpoint (with ``A2AT_SAMPLE_TRACE_TOKEN``);
    ``A2AT_SAMPLE_TRACE_DIR`` selects the CLI JSONL file. Without either key the hop is untraced.
    """
    base_url = os.environ.get("A2AT_SAMPLE_TRACE_URL", "").strip()
    if base_url:
        return HttpTraceEmitter(base_url, os.environ.get("A2AT_SAMPLE_TRACE_TOKEN", "").strip())
    directory = os.environ.get("A2AT_SAMPLE_TRACE_DIR", "").strip()
    if directory:
        return FileTraceEmitter(Path(directory))
    return None


def build_live_pure_answer(
    provider: object,
) -> Callable[[dict[str, object]], Awaitable[str]]:
    """Build the live pure-A2A answer callback around an ADK decision provider.

    The vendor's ``INPUT_REQUIRED`` is free text, while the decision agent returns a structured
    :class:`~shared.models.NegotiationDecision`; :func:`~demo.pure_policy.render_pure_answer`
    renders it back to the text the vendor expects. The executor sets the original normalized
    request on the provider before every downstream hop.
    """
    from demo.pure_policy import render_pure_answer

    async def answer(event: dict[str, object]) -> str:
        text = str(event.get("text") or "").strip()
        pure = _pure_metadata(event)
        round_value = pure.get("round", 1)
        try:
            round_number = max(1, int(round_value or 1))
        except (TypeError, ValueError):
            round_number = 1
        context_id = str(event.get("context_id") or event.get("task_id") or "pure-negotiation")
        propose = NegotiationProposeView(
            negotiation_id=context_id,
            kind="information",
            round=round_number,
            max_rounds=3,
            content=text or "The NMS requested additional information.",
            template_uri=None,
            context_id=context_id,
        )
        session = NegotiationContext(
            id=context_id,
            round=round_number,
            max_rounds=3,
            performative=NegotiationPerformative.PROPOSE,
        )
        decision = await provider.respond(propose, session)  # type: ignore[attr-defined]
        return render_pure_answer(decision)

    return answer


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
