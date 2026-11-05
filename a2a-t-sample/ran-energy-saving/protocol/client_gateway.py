"""Inbound A2A gateway of the client agent.

The sample's client is a **client** of the RAN server, but it is also a **backend agent** to an
external caller such as Gemini Enterprise. This module exposes that second role:

```
external caller (Gemini Enterprise / console)
        -- A2A --> ClientGatewayExecutor
                       -> ClientAgentService (RunManager)
                            -> A2A-T client -> RAN server
                            -> ADK decision agent
                            -> ClientStore
                       <- final report artifact
```

One inbound message maps to one client run. The gateway deliberately supports only automatic
decisions (the ADK agent); manual decisions belong to the Web UI.

The inbound Task is not silent while the client negotiates: a mapper translates the run's internal
milestones into outer ``WORKING`` status updates, so both ``message:stream`` callers and callers
that registered a push-notification webhook receive negotiation and execution progress before the
final report artifact. Raw proposals, prompts and tool results are never forwarded.

The gateway is optional: the Web UI and the CLI work without it. ``run_demo.py --gateway`` starts it
on ``A2AT_SAMPLE_GATEWAY_PORT`` (default 8001).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Final

import httpx
import uvicorn
from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
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
    TaskState,
)
from a2a.utils.constants import TransportProtocol
from google.protobuf.json_format import MessageToDict, ParseDict
from shared.agent_card import AGENT_CARD_ORGANIZATION, AGENT_CARD_PROVIDER_URL
from shared.config import GATEWAY_SCENARIO_ENV_KEY, SampleConfig, load_config
from shared.http_security import (
    BearerAuthMiddleware,
    install_health_routes,
    install_metrics_route,
    sqlite_ready,
    validate_bind_security,
)
from shared.logging_setup import configure_logging, log_context
from shared.registry_client import register_agentcard
from starlette.applications import Starlette
from starlette.middleware import Middleware

from protocol.client_runtime import MODE_AUTO
from protocol.client_service import RunConflictError, RunManager, RunState
from protocol.client_store import SqliteClientStore
from protocol.gateway_progress import GatewayProgressMapper
from protocol.gateway_push import ValidatingPushConfigStore
from protocol.run_spec import RunCatalog
from protocol.sqlite_push_config_store import SqlitePushNotificationConfigStore
from protocol.sqlite_task_store import SqliteTaskStore
from protocol.transcript import DEFAULT_RUNS_DIR

_LOGGER = logging.getLogger("ran_energy_saving.client_gateway")

#: Inbound gateway identity.
GATEWAY_AGENT_NAME: Final[str] = "ran-energy-saving-client-gateway"
GATEWAY_AGENT_DESCRIPTION: Final[str] = (
    "Client-side RAN energy-saving agent. Accepts a natural-language request, negotiates the "
    "A2A-T Task-T with the RAN server, publishes progress, and returns the final energy-saving "
    "report."
)
GATEWAY_SKILL_ID: Final[str] = "ran-energy-saving-request"
GATEWAY_ARTIFACT_NAME: Final[str] = "energy saving intent report"
GATEWAY_FINAL_TEXT: Final[str] = "RAN energy-saving request completed."


def _message_text(message: Any) -> str:
    """Return the first non-empty text part of an inbound A2A message."""
    for part in getattr(message, "parts", ()) or ():
        text = getattr(part, "text", "")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def build_gateway_agent_card(*, host: str, port: int) -> AgentCard:
    """Build the inbound AgentCard of the client gateway.

    The card advertises a plain A2A agent: the A2A-T extensions describe the client's **outbound**
    call to the RAN server, not the inbound contract of this gateway.
    """
    card = AgentCard(
        name=GATEWAY_AGENT_NAME,
        description=GATEWAY_AGENT_DESCRIPTION,
        version="1.0.0",
        provider=AgentProvider(organization=AGENT_CARD_ORGANIZATION, url=AGENT_CARD_PROVIDER_URL),
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        skills=[
            AgentSkill(
                id=GATEWAY_SKILL_ID,
                name="RAN energy saving request",
                description=(
                    "Takes a natural-language RAN energy-saving request, negotiates the task "
                    "intent with the RAN server, and returns the final report."
                ),
                tags=["energy saving", "negotiation"],
                examples=[
                    "Maximize energy saving in Region A while "
                    "keeping the time-segmented guaranteed throughput target."
                ],
            )
        ],
    )
    card.supported_interfaces.add(
        url=f"http://{host}:{port}",
        protocol_binding=TransportProtocol.HTTP_JSON.value,
        protocol_version="1.0.0",
    )
    return card


class ClientGatewayExecutor(AgentExecutor):
    """Map one inbound A2A message to one client run and return the final report."""

    def __init__(self, *, service: RunManager, config: SampleConfig) -> None:
        """Create one gateway executor.

        Args:
            service: client application service owning runs and persistence.
            config: resolved sample configuration.
        """
        self._service = service
        self._config = config
        self._runs_by_task: dict[str, RunState] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Run one inbound request with its correlation fields bound."""
        with log_context(
            task_id=context.task_id,
            context_id=context.context_id,
            role="gateway",
        ):
            await self._execute(context, event_queue)

    async def _execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Create an inbound Task, run the client flow, and publish the final artifact."""
        if context.current_task is not None:
            # One inbound message is one run; a follow-up on the same Task is not supported here.
            await event_queue.enqueue_event(context.current_task)
            return
        message = context.message
        if message is None:
            raise ValueError("The gateway request carries no A2A message")

        task_id = context.task_id or str(uuid.uuid4())
        context_id = context.context_id or task_id
        task = new_task_from_user_message(message)
        task.id = task_id
        task.context_id = context_id
        await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        _LOGGER.info("gateway inbound message accepted; starting a client run")

        request_text = _message_text(message) or "Create a RAN energy saving task."
        conversation_id = f"a2a-{context_id}"
        try:
            run = await self._service.start(
                self._config.gateway_scenario,
                MODE_AUTO,
                self._config.transport,
                self._config.discovery,
                request_text,
                conversation_id=conversation_id,
                external_conversation_id=context.context_id or None,
                user_id="a2a-caller",
            )
        except RunConflictError as exc:
            _LOGGER.warning("gateway run rejected: %s", exc)
            await updater.failed(
                message=updater.new_agent_message([Part(text=str(exc))])
            )
            return

        self._runs_by_task[task.id] = run
        # Publish the negotiation/execution milestones on the outer Task while the run is still
        # in flight. The same status updates feed both message:stream and push notifications.
        snapshot, queue = run.subscribe()
        pump = asyncio.create_task(
            self._publish_progress(snapshot, queue, updater),
            name=f"gateway-progress-{task.id}",
        )
        try:
            if run.task is not None:
                await run.task
        except asyncio.CancelledError:
            await updater.cancel()
            raise
        finally:
            with suppress(asyncio.CancelledError, Exception):
                await pump
            run.unsubscribe(queue)
            self._runs_by_task.pop(task.id, None)

        result = run.result
        _LOGGER.info(
            "gateway run finished: status=%s final_state=%s",
            run.status,
            result.final_state if result is not None else "-",
        )
        if run.status == "finished" and result is not None:
            report = result.report or {}
            summary = str(
                report.get("summary")
                or f"Run completed with final state {result.final_state}."
            )
            parts: list[Part] = []
            if report:
                data_part = Part()
                ParseDict(report, data_part.data)
                parts.append(data_part)
            parts.append(Part(text=summary))
            await updater.add_artifact(
                parts,
                name=GATEWAY_ARTIFACT_NAME,
                metadata={
                    "clientRunId": run.run_id,
                    "clientConversationId": run.conversation_id,
                    "finalState": result.final_state,
                },
                last_chunk=True,
            )
            await updater.update_status(
                TaskState.TASK_STATE_COMPLETED,
                message=updater.new_agent_message([Part(text=GATEWAY_FINAL_TEXT)]),
            )
            return

        reason = run.error or f"The client run ended with status {run.status}."
        await updater.failed(
            message=updater.new_agent_message([Part(text=f"The request failed: {reason}")])
        )

    async def _publish_progress(
        self,
        snapshot: list[dict[str, object]],
        queue: asyncio.Queue[dict[str, object]],
        updater: TaskUpdater,
    ) -> None:
        """Translate the run's broadcast messages into outer Task progress until it finishes.

        Progress must never break the run: a mapping or transport error is logged and the pump
        stops publishing, while the final report path continues.
        """
        mapper = GatewayProgressMapper()
        try:
            for message in snapshot:
                await self._publish_progress_message(message, mapper, updater)
                if message.get("type") == "finished":
                    return
            while True:
                message = await queue.get()
                await self._publish_progress_message(message, mapper, updater)
                if message.get("type") == "finished":
                    return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - progress must not fail the inbound Task
            _LOGGER.exception("gateway progress publishing failed")

    async def _publish_progress_message(
        self,
        message: dict[str, object],
        mapper: GatewayProgressMapper,
        updater: TaskUpdater,
    ) -> None:
        """Publish one mapped milestone, when it produces one."""
        update = mapper.map(message)
        if update is None:
            return
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=updater.new_agent_message(
                [Part(text=update.text)],
                metadata=update.metadata,
            ),
        )
        _LOGGER.info("gateway progress: %s", update.text)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancel the client run backing one inbound Task."""
        del event_queue
        run = self._runs_by_task.get(context.task_id or "")
        if run is None or run.task is None or run.task.done():
            return
        run.task.cancel()

    @property
    def runs_by_task(self) -> dict[str, RunState]:
        """In-flight gateway runs by inbound Task id (test hook)."""
        return dict(self._runs_by_task)


def build_gateway_app(
    *,
    config: SampleConfig,
    service: RunManager,
    task_store: TaskStore | None = None,
    push_config_store: PushNotificationConfigStore | None = None,
    push_sender: PushNotificationSender | None = None,
    host: str | None = None,
    port: int | None = None,
) -> Starlette:
    """Build the inbound A2A Starlette app of the client gateway.

    Args:
        config: resolved sample configuration.
        service: client application service owning the runs.
        task_store: inbound Task store; defaults to the in-memory store. The demo launcher passes
            the SQLite store configured by ``A2AT_SAMPLE_GATEWAY_TASK_DB``.
        push_config_store: webhook registration store; defaults to SQLite in the gateway Task
            database, or memory when no database is configured.
        push_sender: push-notification sender; defaults to the SDK sender over an owned httpx
            client. Tests inject one over a mock transport.
        host: advertised host; defaults to the configured gateway host.
        port: advertised port; defaults to the configured gateway port.
    """
    resolved_host = host or config.gateway_host
    resolved_port = port or config.gateway_port
    validate_bind_security(resolved_host, config.auth_token)
    card = build_gateway_agent_card(host=resolved_host, port=resolved_port)
    executor = ClientGatewayExecutor(service=service, config=config)
    resolved_task_store = task_store or InMemoryTaskStore()
    inner_push_store = push_config_store or _build_push_config_store(config)
    resolved_push_store = ValidatingPushConfigStore(inner_push_store)
    owned_push_client: httpx.AsyncClient | None = None
    if push_sender is None:
        owned_push_client = httpx.AsyncClient(trust_env=False)
        resolved_push_sender: PushNotificationSender = BasePushNotificationSender(
            owned_push_client,
            resolved_push_store,
        )
    else:
        resolved_push_sender = push_sender
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=resolved_task_store,
        agent_card=card,
        push_config_store=resolved_push_store,
        push_sender=resolved_push_sender,
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None, None]:
        del app
        await service.mark_interrupted()
        # Apply the decision-session retention window before the first run of this process.
        await service.sweep_sessions()
        yield
        await service.shutdown()
        if owned_push_client is not None:
            await owned_push_client.aclose()

    app = Starlette(
        routes=[*create_agent_card_routes(card), *create_rest_routes(request_handler)],
        lifespan=lifespan,
        middleware=(
            [
                Middleware(
                    BearerAuthMiddleware,
                    token=config.auth_token,
                    protected_prefixes=("/message", "/tasks", "/metrics"),
                )
            ]
            if config.auth_token
            else []
        ),
    )
    install_health_routes(
        app,
        service="ran-energy-saving-gateway",
        checks={
            "client_store": lambda: sqlite_ready(config.client_db_path),
            "gateway_task_store": lambda: sqlite_ready(config.gateway_task_db_path),
        },
    )
    install_metrics_route(app)
    app.state.agent_card = card
    app.state.gateway_task_store = resolved_task_store
    app.state.push_config_store = resolved_push_store
    app.state.push_sender = resolved_push_sender
    app.state.gateway_executor = executor
    app.state.client_service = service
    app.state.config = config
    return app


def _build_push_config_store(config: SampleConfig) -> PushNotificationConfigStore:
    """Build the default webhook registration store of the gateway."""
    if config.gateway_task_db_path:
        return SqlitePushNotificationConfigStore(config.gateway_task_db_path)
    return InMemoryPushNotificationConfigStore()


def build_gateway_service(
    *,
    config: SampleConfig,
    env_path: Path | None = None,
    catalog: RunCatalog | None = None,
) -> RunManager:
    """Build the client service used by the gateway (and optionally by the Web UI)."""
    if catalog is None:
        from demo.catalog import DemoRunCatalog

        catalog = DemoRunCatalog.from_config(config)
    if config.gateway_scenario not in catalog.names:
        raise ValueError(
            f"Unsupported {GATEWAY_SCENARIO_ENV_KEY}: {config.gateway_scenario}; "
            f"expected one of {catalog.names}"
        )
    store = (
        SqliteClientStore(config.client_db_path) if config.client_db_path is not None else None
    )

    async def runtime_factory(
        resolved_config: SampleConfig,
        scenario: str,
        mode: str,
        transport: str,
        discovery: str,
    ) -> Any:
        from demo.composition import build_demo_runtime

        return await build_demo_runtime(
            resolved_config,
            scenario,
            mode,
            transport,
            discovery,
            env_path=env_path,
        )

    return RunManager(
        config=config,
        catalog=catalog,
        runtime_factory=runtime_factory,
        runs_dir=DEFAULT_RUNS_DIR,
        store=store,
        memory_turns=config.client_memory_turns,
    )


async def register_gateway_card(
    card: AgentCard,
    *,
    config: SampleConfig,
    env_path: Path | None = None,
) -> dict[str, Any]:
    """Register the gateway AgentCard, tolerating a registry-center failure."""
    del config
    try:
        return await register_agentcard(
            registration_payload={"agentCards": [MessageToDict(card)]},
            env_path=env_path,
        )
    except Exception as exc:  # noqa: BLE001 - a registry outage must not stop the gateway
        _LOGGER.warning("gateway AgentCard registration failed: %s", exc)
        return {"error": str(exc)}


def run_gateway(
    *,
    config: SampleConfig,
    env_path: Path | None = None,
) -> int:
    """Serve the inbound A2A gateway until interrupted."""
    configure_logging()
    service = build_gateway_service(config=config, env_path=env_path)
    task_store = (
        SqliteTaskStore(config.gateway_task_db_path)
        if config.gateway_task_db_path is not None
        else None
    )
    app = build_gateway_app(config=config, service=service, task_store=task_store)
    card = app.state.agent_card
    if config.gateway_register:
        asyncio.run(register_gateway_card(card, config=config, env_path=env_path))
    print(
        f"[gateway] A2A gateway on http://{config.gateway_host}:{config.gateway_port} "
        f"(scenario={config.gateway_scenario}, discovery={config.discovery}, "
        f"client_db={config.client_db_path or 'memory'})"
    )
    try:
        uvicorn.run(app, host=config.gateway_host, port=config.gateway_port, log_level="info")
    except KeyboardInterrupt:
        print("\n[gateway] stopped")
        return 130
    return 0


def main() -> int:
    """CLI entry point (``python -m protocol.client_gateway``)."""
    parser = argparse.ArgumentParser(description="RAN energy-saving client A2A gateway")
    parser.add_argument(
        "--env",
        type=Path,
        default=None,
        help="sample .env path; defaults to $CWD/.env",
    )
    args = parser.parse_args()
    env_path = args.env or (Path.cwd() / ".env")
    config = load_config(env_path=env_path)
    return run_gateway(config=config, env_path=env_path)


if __name__ == "__main__":
    raise SystemExit(main())
