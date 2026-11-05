"""Application assembly of the RAN energy-saving A2A-T server.

The module builds the AgentCard (declaring the Task-T and Negotiation-T extensions), the A2A
request handler, the Starlette app and the registry registration. The server always runs the
pre-task negotiation handler in front of the deterministic RAN NMS executor, so all negotiation
happens before a
Task is created and the server uses no ADK agent.

Offline mode registers the traced scripted SDK provider and points the SDK at a temporary ``.env``
carrying ``A2AT_LLM_PROVIDER=scripted_traced`` / ``A2AT_LLM_MODEL=scripted`` /
``A2AT_LLM_API_KEY=not-needed``; the SDK reads its configuration from a file, not from process
environment variables, so the temporary copy is the non-destructive way to honor the sample's
offline switch. Live mode uses ``A2AT_LLM_PROVIDER=openai_traced``.

Run Explorer: when ``A2AT_SAMPLE_TRACE_URL`` (Web UI) or ``A2AT_SAMPLE_TRACE_DIR`` (CLI file) is
set, the runtime attaches the matching trace emitter as the process-wide span sink, so the server
side of a cross-process run reaches the client timeline (design §4).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import httpx
import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_rest_routes
from a2a.server.tasks import BasePushNotificationSender, InMemoryPushNotificationConfigStore
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_store import TaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentProvider,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, TransportProtocol
from a2a_t.core.metadata import NEGOTIATION_T_EXTENSION_URI, TASK_T_EXTENSION_URI
from a2a_t.server.a2at_server import A2ATServer
from google.protobuf.json_format import MessageToDict
from shared.agent_card import (
    AGENT_CARD_DESCRIPTION,
    AGENT_CARD_EXAMPLE,
    AGENT_CARD_NAME,
    AGENT_CARD_ORGANIZATION,
    AGENT_CARD_PROVIDER_URL,
    AGENT_CARD_SKILL_ID,
)
from shared.config import SERVER_AUTH_TOKEN_ENV_KEY, SampleConfig, load_config
from shared.http_security import (
    BearerAuthMiddleware,
    install_health_routes,
    install_metrics_route,
    sqlite_ready,
    validate_bind_security,
)
from shared.logging_setup import configure_logging
from shared.registry_client import register_agentcard
from shared.run_constants import MAX_ROUNDS, PROTOCOL_A2A_T, PROTOCOL_PURE
from shared.sdk_env import prepare_sdk_env
from shared.trace import FileTraceEmitter, HttpTraceEmitter, set_global_sink
from shared.trace_propagation import TracePropagationMiddleware
from shared.traced_llm import register_traced_providers
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from protocol.approval_gate import DEFAULT_APPROVAL_TIMEOUT_SECONDS, ApprovalRegistry
from protocol.mie_client import MIEApprovalClient
from protocol.negotiation_policy import NegotiationState, PolicyDecision
from protocol.plain_a2a import PlainTaskHandler, ProtocolDispatchHandler
from protocol.pre_task_negotiation import PreTaskNegotiationHandler, TaskTPrecheck
from protocol.pure_a2a import PureTargetPolicy, PureTaskHandler
from protocol.server_agent_spec import ServerAgentFactory
from protocol.server_executor import (
    NegotiationAgentExecutor,
    TaskRequestHandler,
    build_negotiation_executor,
)
from protocol.sqlite_task_store import SqliteTaskStore

#: Temporary SDK ``.env`` directory prefix of the server runtime.
SERVER_ENV_PREFIX: Final[str] = "a2at-ran-energy-saving-env-"

_LOGGER = logging.getLogger("ran_energy_saving.server_app")

#: Routes that stay public when the server requires a bearer token.
SERVER_PUBLIC_PATHS: Final[frozenset[str]] = frozenset(
    {"/", "/health", "/ready", AGENT_CARD_WELL_KNOWN_PATH}
)


@dataclass(frozen=True)
class ServerRuntime:
    """Assembled server runtime exposed to ``main`` and tests.

    Attributes:
        config: resolved sample configuration.
        agent_card: card served at ``/.well-known/agent-card.json`` and registered.
        prompt_server: A2A-T SDK server facade.
        pre_task_handler: pre-task negotiation handler.
        executor: outer executor used by the request handler.
        task_store: Task store backing the A2A Task lifecycle (SQLite when configured).
        app: Starlette application.
        trace_emitter: Run Explorer emitter attached to this process, when configured.
    """

    config: SampleConfig
    agent_card: AgentCard
    prompt_server: A2ATServer
    pre_task_handler: TaskRequestHandler
    executor: NegotiationAgentExecutor
    task_store: TaskStore
    app: Starlette
    trace_emitter: HttpTraceEmitter | FileTraceEmitter | None = None


def build_trace_emitter() -> HttpTraceEmitter | FileTraceEmitter | None:
    """Build the server trace emitter configured by the process environment.

    ``A2AT_SAMPLE_TRACE_URL`` selects the cross-process HTTP transport to the Web UI (with
    ``A2AT_SAMPLE_TRACE_TOKEN``); otherwise ``A2AT_SAMPLE_TRACE_DIR`` selects the CLI JSONL file
    ``<dir>/<runId>/server-trace.jsonl``. Without either key the server emits no trace.
    """
    base_url = os.environ.get("A2AT_SAMPLE_TRACE_URL", "").strip()
    if base_url:
        return HttpTraceEmitter(base_url, os.environ.get("A2AT_SAMPLE_TRACE_TOKEN", "").strip())
    directory = os.environ.get("A2AT_SAMPLE_TRACE_DIR", "").strip()
    if directory:
        return FileTraceEmitter(Path(directory))
    return None


def build_agent_card(*, host: str, port: int, secured: bool = False) -> AgentCard:
    """Build the AgentCard with the Task-T and Negotiation-T extension declarations.

    Args:
        host: bind host advertised to clients.
        port: bind port advertised to clients.
        secured: declare HTTP bearer authentication so a client discovers the requirement.
    """
    card = AgentCard(
        name=AGENT_CARD_NAME,
        description=AGENT_CARD_DESCRIPTION,
        version="1.0.0",
        provider=AgentProvider(organization=AGENT_CARD_ORGANIZATION, url=AGENT_CARD_PROVIDER_URL),
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=True,
            extensions=[
                AgentExtension(
                    uri=TASK_T_EXTENSION_URI,
                    description="Extension of structured prompt TASK-T requests.",
                    required=False,
                ),
                AgentExtension(
                    uri=NEGOTIATION_T_EXTENSION_URI,
                    description="Extension of structured prompt Negotiation-T requests.",
                    required=False,
                ),
            ],
        ),
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json", "text/plain"],
        skills=[
            AgentSkill(
                id=AGENT_CARD_SKILL_ID,
                name="RAN energy saving",
                description=(
                    "Creates an energy-saving task based on the energy-saving intent, and "
                    "periodically reports energy-saving intent reports."
                ),
                tags=["energy saving"],
                examples=[AGENT_CARD_EXAMPLE],
            )
        ],
        security_schemes=(
            {
                "bearer": SecurityScheme(
                    http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="bearer")
                )
            }
            if secured
            else {}
        ),
        security_requirements=(
            [SecurityRequirement(schemes={"bearer": StringList()})] if secured else []
        ),
    )
    card.supported_interfaces.add(
        url=f"http://{host}:{port}",
        protocol_binding=TransportProtocol.HTTP_JSON.value,
        protocol_version="1.0.0",
    )
    return card


def build_registration_payload(agent_card: AgentCard) -> dict[str, Any]:
    """Build the registry-center registration payload for one AgentCard."""
    return {"agentCards": [MessageToDict(agent_card)]}


def prepare_sdk_env_path(config: SampleConfig, env_path: Path | None = None) -> Path:
    """Return the ``.env`` file the A2A-T SDK should read.

    The caller-provided file (defaults to ``$CWD/.env``) is never modified: both modes write a
    temporary copy. In live mode the copy keeps the SDK endpoint values of the source file and
    normalizes empty or missing ``A2AT_LLM_*`` keys from :class:`SampleConfig`, so an empty
    ``A2AT_LLM_API_KEY`` becomes the local-proxy placeholder instead of failing the SDK's eager
    configuration validation. In offline mode the copy pins the scripted provider keys.

    Args:
        config: resolved sample configuration.
        env_path: optional source ``.env`` path.

    Raises:
        FileNotFoundError: in live mode when the resolved file does not exist.
    """
    base_path = env_path or Path.cwd() / ".env"
    if not config.is_offline and not base_path.exists():
        raise FileNotFoundError(f"Live mode requires an SDK .env file at {base_path}")
    return prepare_sdk_env(config, base_path, prefix=SERVER_ENV_PREFIX).path


def build_rest_app(
    *,
    agent_card: AgentCard,
    executor: NegotiationAgentExecutor,
    task_store: TaskStore | None = None,
    task_db_path: str | None = None,
    trace_emitter: HttpTraceEmitter | FileTraceEmitter | None = None,
    auth_token: str = "",
    approval_registry: ApprovalRegistry | None = None,
) -> Starlette:
    """Build the Starlette app with the AgentCard routes and the A2A REST routes.

    The push-notification machinery is assembled unconditionally: it idles in SSE mode and is used
    when a client registers a webhook. The sender's HTTP client is closed by the app lifespan; the
    optional Run Explorer emitter is flushed and stopped there too.

    Args:
        agent_card: card served at the well-known A2A route.
        executor: negotiation-aware AgentExecutor.
        task_store: A2A Task store; defaults to an in-memory store (tests/direct runs).
        task_db_path: SQLite file backing the task store, used by the readiness check.
        trace_emitter: optional Run Explorer span emitter.
        auth_token: bearer token every A2A route requires; the AgentCard and health routes stay
            public so discovery and probes work.
        approval_registry: optional human approval gate resolved by ``POST /approvals``.
    """
    resolved_task_store: TaskStore = task_store if task_store is not None else InMemoryTaskStore()
    push_config_store = InMemoryPushNotificationConfigStore()
    push_httpx_client = httpx.AsyncClient(trust_env=False)
    push_sender = BasePushNotificationSender(push_httpx_client, push_config_store)

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None, None]:
        del app
        yield
        await push_httpx_client.aclose()
        if trace_emitter is not None:
            trace_emitter.close()

    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=resolved_task_store,
        agent_card=agent_card,
        push_config_store=push_config_store,
        push_sender=push_sender,
    )
    async def approve(request: Request) -> Response:
        """Resolve one pending human approval of the change gate."""
        if approval_registry is None:
            return JSONResponse(
                {"error": "The approval gate is disabled.", "code": "approval_gate_disabled"},
                status_code=404,
            )
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(
                {"error": "The body must be a JSON object.", "code": "body_not_object"},
                status_code=400,
            )
        if not isinstance(body, dict):
            return JSONResponse(
                {"error": "The body must be a JSON object.", "code": "body_not_object"},
                status_code=400,
            )
        run_id = body.get("runId")
        approved = body.get("approved")
        if not isinstance(run_id, str) or not run_id.strip():
            return JSONResponse(
                {"error": "The runId field must be a non-empty string.", "code": "run_id_invalid"},
                status_code=400,
            )
        if not isinstance(approved, bool):
            return JSONResponse(
                {"error": "The approved field must be a boolean.", "code": "approved_invalid"},
                status_code=400,
            )
        if not approval_registry.resolve(run_id.strip(), approved):
            return JSONResponse(
                {"error": "No approval is pending for this run.", "code": "approval_not_pending"},
                status_code=409,
            )
        return JSONResponse({"resolved": True, "approved": approved})

    routes = [
        *create_agent_card_routes(agent_card),
        *create_rest_routes(request_handler),
        Route("/approvals", approve, methods=["POST"]),
    ]
    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[
            *(
                [
                    Middleware(
                        BearerAuthMiddleware,
                        token=auth_token,
                        # Default-deny: the SDK also mounts a ``/{tenant}/...`` alias, so a prefix
                        # allowlist would leave that copy of every route unprotected.
                        protected_prefixes=("/",),
                        public_paths=SERVER_PUBLIC_PATHS,
                    )
                ]
                if auth_token
                else []
            ),
            # Attach the client's traceparent to an OTel context: the a2a-sdk request-handler
            # spans then join the client's Langfuse trace instead of starting a separate one.
            Middleware(TracePropagationMiddleware),
        ],
    )
    install_health_routes(
        app,
        service="ran-energy-saving-server",
        checks={"task_store": lambda: sqlite_ready(task_db_path)},
    )
    install_metrics_route(app)
    app.state.request_handler = request_handler
    app.state.task_store = resolved_task_store
    return app


def build_server_runtime(
    *,
    agent_factory: ServerAgentFactory,
    policy: Callable[[NegotiationState], PolicyDecision] | None = None,
    precheck: TaskTPrecheck | None = None,
    config: SampleConfig | None = None,
    env_path: Path | None = None,
    logger: logging.Logger | None = None,
    install_offline_provider: Callable[[], None] | None = None,
    protocol_mode: str = PROTOCOL_A2A_T,
    pure_policy: PureTargetPolicy | None = None,
    mie_url: str | None = None,
    mie_client: MIEApprovalClient | None = None,
    approval_gate: bool | None = None,
    approval_timeout_seconds: float | None = None,
) -> ServerRuntime:
    """Assemble the full server runtime.

    Args:
        agent_factory: builder of the NMS planner graph for one accepted task.
        policy: negotiation decision function bound to the deployment's plan evaluator;
            defaults to the unbound state machine.
        precheck: deployment check of a semantically valid Task-T; defaults to trusting the SDK
            verdict.
        config: resolved sample configuration; defaults to :func:`shared.config.load_config`.
        env_path: optional ``.env`` source path.
        logger: optional logger shared by the gate and the runtime.
        install_offline_provider: installer of the deterministic SDK LLM routing; required in
            offline mode.
        protocol_mode: ``a2a-t`` keeps the existing pre-task gate; ``pure`` installs the
            extension-free INPUT_REQUIRED handler.
        pure_policy: business policy of the pure target negotiation; when omitted, a policy with
            no configured baseline target is used (tests may inject one).
        mie_url: MIE Agent base URL of the pure approval hop; defaults to
            ``A2AT_SAMPLE_MIE_URL`` or ``http://127.0.0.1:8002``.
        mie_client: optional pre-built MIE approval client (tests).
        approval_gate: override of the ``A2AT_SAMPLE_APPROVAL_GATE`` switch (tests); ``None``
            keeps the environment-driven behavior.
        approval_timeout_seconds: override of the approval timeout (tests).

    Returns:
        The assembled runtime, including the Starlette app.

    Raises:
        ValueError: when offline mode has no deterministic provider installer, or when
            ``protocol_mode`` is unknown.
    """
    if protocol_mode not in (PROTOCOL_A2A_T, PROTOCOL_PURE):
        raise ValueError(
            f"Unsupported server protocol mode: {protocol_mode}; expected a2a-t or pure"
        )
    resolved_logger = logger or _LOGGER
    resolved_config = config or load_config(env_path=env_path)
    validate_bind_security(
        resolved_config.host,
        resolved_config.server_auth_token,
        token_env_key=SERVER_AUTH_TOKEN_ENV_KEY,
    )
    register_traced_providers()
    if resolved_config.is_offline:
        if install_offline_provider is None:
            raise ValueError(
                "Offline mode needs install_offline_provider; the demo entry point supplies it."
            )
        install_offline_provider()
    trace_emitter = build_trace_emitter()
    if trace_emitter is not None:
        set_global_sink(trace_emitter)
    sdk_env_path = prepare_sdk_env_path(resolved_config, env_path)
    prompt_server = A2ATServer(env_path=sdk_env_path, logger=resolved_logger)
    if protocol_mode == PROTOCOL_PURE:
        from protocol.pure_a2a import RegexPureTargetPolicy

        pre_task_handler: TaskRequestHandler = PureTaskHandler(
            policy=pure_policy or RegexPureTargetPolicy(),
            language=resolved_config.language,
            max_rounds=MAX_ROUNDS,
            logger=resolved_logger,
        )
    else:
        pre_task_handler = ProtocolDispatchHandler(
            PreTaskNegotiationHandler(
                prompt_server,
                max_rounds=MAX_ROUNDS,
                logger=resolved_logger,
                policy=policy,
                precheck=precheck,
            ),
            PlainTaskHandler(logger=resolved_logger),
        )
    if approval_gate is None:
        approval_enabled = os.environ.get(
            "A2AT_SAMPLE_APPROVAL_GATE", ""
        ).strip().lower() in {
            "1",
            "true",
            "yes",
        }
    else:
        approval_enabled = approval_gate
    approval_timeout = (
        approval_timeout_seconds
        if approval_timeout_seconds is not None
        else float(
            os.environ.get(
                "A2AT_SAMPLE_APPROVAL_TIMEOUT_SECONDS",
                str(DEFAULT_APPROVAL_TIMEOUT_SECONDS),
            )
            or str(DEFAULT_APPROVAL_TIMEOUT_SECONDS)
        )
    )
    approval_registry = ApprovalRegistry() if approval_enabled else None
    if protocol_mode == PROTOCOL_PURE and approval_enabled and mie_client is None:
        resolved_mie_url = (
            mie_url
            or os.environ.get("A2AT_SAMPLE_MIE_URL", "http://127.0.0.1:8002")
        ).strip()
        if resolved_mie_url:
            mie_client = MIEApprovalClient(
                base_url=resolved_mie_url,
                timeout_seconds=resolved_config.http_timeout_seconds,
            )
    executor = build_negotiation_executor(
        handler=pre_task_handler,
        agent_factory=agent_factory,
        config=resolved_config,
        approval_registry=approval_registry,
        approval_timeout_seconds=approval_timeout,
        pure=protocol_mode == PROTOCOL_PURE,
        mie_client=mie_client,
    )
    task_store: TaskStore = (
        SqliteTaskStore(resolved_config.task_db_path)
        if resolved_config.task_db_path
        else InMemoryTaskStore()
    )
    secured = bool(resolved_config.server_auth_token)
    agent_card = build_agent_card(
        host=resolved_config.host,
        port=resolved_config.port,
        secured=secured,
    )
    app = build_rest_app(
        agent_card=agent_card,
        executor=executor,
        task_store=task_store,
        task_db_path=resolved_config.task_db_path,
        trace_emitter=trace_emitter,
        auth_token=resolved_config.server_auth_token,
        approval_registry=approval_registry,
    )
    app.state.config = resolved_config
    app.state.prompt_server = prompt_server
    app.state.pre_task_handler = pre_task_handler
    app.state.trace_emitter = trace_emitter
    app.state.approval_registry = approval_registry
    app.state.mie_client = mie_client
    return ServerRuntime(
        config=resolved_config,
        agent_card=agent_card,
        prompt_server=prompt_server,
        pre_task_handler=pre_task_handler,
        executor=executor,
        task_store=task_store,
        app=app,
        trace_emitter=trace_emitter,
    )


def build_server_app(
    *,
    agent_factory: ServerAgentFactory,
    policy: Callable[[NegotiationState], PolicyDecision] | None = None,
    precheck: TaskTPrecheck | None = None,
    config: SampleConfig | None = None,
    env_path: Path | None = None,
    logger: logging.Logger | None = None,
    install_offline_provider: Callable[[], None] | None = None,
    protocol_mode: str = PROTOCOL_A2A_T,
    pure_policy: PureTargetPolicy | None = None,
    mie_url: str | None = None,
    mie_client: MIEApprovalClient | None = None,
    approval_gate: bool | None = None,
    approval_timeout_seconds: float | None = None,
) -> Starlette:
    """Build only the Starlette app (see :func:`build_server_runtime` for the full runtime)."""
    return build_server_runtime(
        agent_factory=agent_factory,
        policy=policy,
        precheck=precheck,
        config=config,
        env_path=env_path,
        logger=logger,
        install_offline_provider=install_offline_provider,
        protocol_mode=protocol_mode,
        pure_policy=pure_policy,
        mie_url=mie_url,
        mie_client=mie_client,
        approval_gate=approval_gate,
        approval_timeout_seconds=approval_timeout_seconds,
    ).app


async def register_server_agent_card(
    agent_card: AgentCard,
    *,
    config: SampleConfig,
    env_path: Path | None = None,
    tolerate_failure: bool = False,
) -> dict[str, Any]:
    """Register the AgentCard with the registry center.

    Args:
        agent_card: card to register.
        config: sample configuration carrying the registry URL.
        env_path: optional ``.env`` path used by the registry client.
        tolerate_failure: when ``True``, a failed registration is logged and returned; otherwise it
            raises.

    Raises:
        RuntimeError: when registration fails and ``tolerate_failure`` is ``False``.
    """
    result = await register_agentcard(
        registration_payload=build_registration_payload(agent_card),
        env_path=env_path,
        registry_center_url=config.registry_url,
    )
    if result.get("status") != "success":
        message = str(result.get("message", "unknown error"))
        if tolerate_failure:
            _LOGGER.warning("agent-card registration failed (tolerated): %s", message)
            return result
        raise RuntimeError(f"Agent card registration failed: {message}")
    _LOGGER.info("agent-card registration succeeded: %s", config.registry_url)
    return result


def main() -> None:
    """Entry point: assemble the runtime, register the card, and start uvicorn."""
    configure_logging()
    env_path = Path.cwd() / ".env"
    resolved_env_path = env_path if env_path.exists() else None
    from demo.agents.server_agent.factory import build_server_agent
    from demo.composition import (
        build_demo_negotiation_policy,
        build_demo_task_t_precheck,
        install_offline_provider,
    )
    from demo.pure_policy import build_demo_pure_policy

    resolved_config = load_config(env_path=resolved_env_path)
    protocol_mode = (
        os.environ.get("A2AT_SAMPLE_PROTOCOL_MODE", PROTOCOL_A2A_T).strip()
        or PROTOCOL_A2A_T
    )
    pure_policy = (
        build_demo_pure_policy(resolved_config.language)
        if protocol_mode == PROTOCOL_PURE
        else None
    )
    runtime = build_server_runtime(
        agent_factory=build_server_agent,
        policy=build_demo_negotiation_policy(),
        precheck=build_demo_task_t_precheck(),
        config=resolved_config,
        env_path=resolved_env_path,
        install_offline_provider=install_offline_provider,
        protocol_mode=protocol_mode,
        pure_policy=pure_policy,
    )
    asyncio.run(
        register_server_agent_card(
            runtime.agent_card,
            config=runtime.config,
            env_path=resolved_env_path,
        )
    )
    _LOGGER.info(
        "starting RAN energy-saving server on %s:%s (model_mode=%s, model=%s, language=%s, task_db=%s)",
        runtime.config.host,
        runtime.config.port,
        runtime.config.model_mode,
        runtime.config.model_label,
        runtime.config.language,
        runtime.config.task_db_path or "in-memory",
    )
    uvicorn.run(runtime.app, host=runtime.config.host, port=runtime.config.port)


if __name__ == "__main__":
    main()
