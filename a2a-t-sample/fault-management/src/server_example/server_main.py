from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import uvicorn
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_rest_routes
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.utils.constants import TransportProtocol
from a2a_t.server.a2at_server import A2ATServer
from common.llm_logger import install_llm_logger, set_llm_log_sink
from common.logging_utils import build_sample_logger, resolve_sample_debug
from common.mock_llm import install_mock_llm_if_needed
from common.registry_client import register_agentcard
from dotenv import dotenv_values
from starlette.applications import Starlette

from server_example.constants_data import get_public_agent_card
from server_example.server_flow import execute_server_flow

install_mock_llm_if_needed()
install_llm_logger(role="server")

_PROXY_ENV_VAR_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


class SampleAgentExecutor(AgentExecutor):
    """AgentExecutor that delegates to execute_server_flow with stored runtime context."""

    def __init__(
        self,
        *,
        prompt_server: object,
        log_sink: object | None = None,
        execute_flow: object,
    ) -> None:
        self._prompt_server = prompt_server
        self._log_sink = log_sink
        self._execute_flow = execute_flow

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        kwargs = {
            "request_context": context,
            "event_queue": event_queue,
            "prompt_server": self._prompt_server,
            "log_sink": self._log_sink,
        }
        await self._execute_flow(**kwargs)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None


@contextmanager
def _without_proxy_env() -> object:
    original_values = {name: os.environ.get(name) for name in _PROXY_ENV_VAR_NAMES}
    try:
        for name in _PROXY_ENV_VAR_NAMES:
            os.environ.pop(name, None)
        yield
    finally:
        for name, value in original_values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def build_agent_card_payload(*, host: str, port: int) -> dict[str, Any]:
    """Build the public AgentCard dict with a supportedInterfaces entry pointing to the given host:port."""
    card = get_public_agent_card()
    card["supportedInterfaces"] = [
        {
            "protocolBinding": TransportProtocol.HTTP_JSON.value,
            "protocolVersion": "1.0.0",
            "url": f"http://{host}:{port}",
        }
    ]
    return card


def build_registration_payload(*, host: str, port: int) -> dict[str, Any]:
    return {"agentCards": [build_agent_card_payload(host=host, port=port)]}


def build_agent_card(*, host: str, port: int) -> object:
    from a2a.types import AgentCard
    from google.protobuf.json_format import ParseDict
    return ParseDict(build_agent_card_payload(host=host, port=port), AgentCard())


def build_server_runtime(
    *,
    agent_card: object,
    env_path: Path | None = None,
    logger: Any | None = None,
    log_sink: object | None = None,
    debug_enabled: bool = False,
) -> dict[str, object]:
    """Build the server runtime: an A2ATServer prompt server and a SampleAgentExecutor."""
    resolved_logger = logger
    if resolved_logger is None and log_sink is not None and debug_enabled:
        resolved_logger = build_sample_logger(sink=log_sink, debug_enabled=True)
    prompt_server = A2ATServer(env_path=env_path, logger=resolved_logger)
    executor = SampleAgentExecutor(
        prompt_server=prompt_server,
        log_sink=log_sink,
        execute_flow=execute_server_flow,
    )
    return {
        "prompt_server": prompt_server,
        "executor": executor,
    }


def build_rest_app(
    *,
    agent_card: object,
    executor: object,
) -> object:
    """Build the Starlette REST app with agent card routes and A2A task/message routes."""
    task_store = InMemoryTaskStore()
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
        agent_card=agent_card,
    )
    routes = [
        *create_agent_card_routes(agent_card),
        *create_rest_routes(request_handler),
    ]
    return Starlette(routes=routes)


async def register_agent_card_if_possible(
    *,
    host: str,
    port: int,
    env_path: Path | None = None,
    log_sink: object | None = print,
) -> dict[str, Any]:
    result = await register_agentcard(
        registration_payload=build_registration_payload(host=host, port=port),
        env_path=env_path,
        log_sink=log_sink,
    )
    if log_sink is not None:
        if result.get("status") == "success":
            log_sink("[server] agent-card registration: success")
        else:
            log_sink(
                "[server] agent-card registration failed, continuing startup: "
                f"{result.get('message', 'unknown error')}"
            )
    return result


def resolve_server_bind(*, env_path: Path | None = None) -> tuple[str, int]:
    resolved_env_path = env_path or Path.cwd() / ".env"
    env_values = dotenv_values(resolved_env_path) if resolved_env_path.exists() else {}
    host = str(env_values.get("A2AT_SAMPLE_HOST") or os.environ.get("A2AT_SAMPLE_HOST") or "127.0.0.1")
    port = int(env_values.get("A2AT_SAMPLE_PORT") or os.environ.get("A2AT_SAMPLE_PORT") or "8000")
    return host, port


def main() -> None:
    """Entry point for the server: registers agent card, builds runtime, and starts uvicorn."""
    env_path = Path.cwd() / ".env"
    with _without_proxy_env():
        set_llm_log_sink(print)
        host, port = resolve_server_bind(env_path=env_path)
        debug_enabled = resolve_sample_debug(env_path=env_path)
        print(f"[server] startup: host={host} port={port} debug={'true' if debug_enabled else 'false'}")
        agent_card = build_agent_card(host=host, port=port)
        asyncio.run(
            register_agent_card_if_possible(
                host=host,
                port=port,
                env_path=env_path if env_path.exists() else None,
                log_sink=print,
            )
        )
        runtime = build_server_runtime(
            agent_card=agent_card,
            env_path=env_path if env_path.exists() else None,
            log_sink=print,
            debug_enabled=debug_enabled,
        )
        app = build_rest_app(
            agent_card=agent_card,
            executor=runtime["executor"],
        )
        uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
