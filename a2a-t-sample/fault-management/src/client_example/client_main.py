from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.types import AgentCard
from a2a.utils.constants import TransportProtocol
from a2a_t.client.a2at_client import A2ATClient
from common.llm_logger import install_llm_logger, set_llm_log_sink
from common.logging_utils import build_sample_logger, resolve_sample_debug
from common.mock_llm import install_mock_llm_if_needed
from common.registry_client import query_by_name_org
from dotenv import dotenv_values
from google.protobuf.json_format import ParseDict

from client_example.client_flow import run_client_flow
from client_example.scenario_data import build_subscription_request

install_mock_llm_if_needed()
install_llm_logger(role="client")

_PROXY_ENV_VAR_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


@dataclass(frozen=True, slots=True)
class ResolvedAgentEndpoint:
    """Resolved HTTP endpoint information extracted from an AgentCard's supportedInterfaces."""

    agent_name: str
    protocol_binding: str
    protocol_version: str
    url: str


def resolve_preferred_interface(agent_card: dict[str, object]) -> ResolvedAgentEndpoint:
    """Extract the first valid HTTP interface from an AgentCard's supportedInterfaces list."""
    supported_interfaces = agent_card.get("supportedInterfaces")
    if not isinstance(supported_interfaces, list):
        raise ValueError("AgentCard.supportedInterfaces is required")

    for item in supported_interfaces:
        if not isinstance(item, dict):
            continue

        protocol_binding = str(item.get("protocolBinding", ""))
        url = str(item.get("url", ""))
        if not _is_valid_http_url(url):
            raise ValueError(f"Invalid supportedInterfaces url: {url}")

        return ResolvedAgentEndpoint(
            agent_name=str(agent_card.get("name", "")),
            protocol_binding=protocol_binding,
            protocol_version=str(item.get("protocolVersion", "")),
            url=url,
        )

    raise ValueError("No supported HTTP interface found in AgentCard.supportedInterfaces")


def _is_valid_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def resolve_server_url(*, env_path: Path | None = None) -> str:
    """Resolve the sample server URL from .env (host + port)."""
    resolved_env_path = env_path or Path.cwd() / ".env"
    env_values = dotenv_values(resolved_env_path) if resolved_env_path.exists() else {}
    host = str(env_values.get("A2AT_SAMPLE_HOST") or os.environ.get("A2AT_SAMPLE_HOST") or "127.0.0.1")
    port = int(env_values.get("A2AT_SAMPLE_PORT") or os.environ.get("A2AT_SAMPLE_PORT") or "8000")
    return f"http://{host}:{port}"


def _resolve_http_timeout_seconds(*, env_path: Path | None = None) -> float:
    resolved_env_path = env_path or Path.cwd() / ".env"
    env_values = dotenv_values(resolved_env_path) if resolved_env_path.exists() else {}
    raw_timeout = env_values.get("A2AT_LLM_TIMEOUT_SECONDS")
    if raw_timeout is None:
        return 60.0
    return float(raw_timeout)


async def build_client_runtime(
    *,
    env_path: Path | None = None,
    logger: Any | None = None,
    prompt_client_cls: type = A2ATClient,
) -> dict[str, object]:
    """Build the client runtime: an httpx async client and an A2ATClient prompt client."""
    httpx_client = httpx.AsyncClient(
        timeout=_resolve_http_timeout_seconds(env_path=env_path),
        trust_env=False,
    )
    prompt_client = prompt_client_cls(env_path=env_path, logger=logger)
    return {
        "prompt_client": prompt_client,
        "httpx_client": httpx_client,
    }


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


async def run_main(
    *,
    env_path: Path | None,
    initial_input: dict[str, object],
    bootstrap: object = build_client_runtime,
    flow: object = run_client_flow,
    logger: Any | None = None,
    log_sink: object | None = None,
    debug_enabled: bool = False,
    max_artifacts: int | None = None,
    registry_query_fn: object = query_by_name_org,
) -> object:
    """Run the full client flow: bootstrap runtime, discover agent, generate prompt, consume stream."""
    resolved_logger = logger
    if resolved_logger is None and log_sink is not None and debug_enabled:
        resolved_logger = build_sample_logger(sink=log_sink, debug_enabled=True)
    runtime = await bootstrap(env_path=env_path, logger=resolved_logger)

    agent_card_query = initial_input.get("agent_card_query")
    query_name = str(agent_card_query.get("name", "")) if isinstance(agent_card_query, dict) else ""
    query_org = str(agent_card_query.get("organization", "")) if isinstance(agent_card_query, dict) else ""
    agent_card_response = await registry_query_fn(
        organization=query_org,
        name=query_name,
        env_path=env_path,
        log_sink=log_sink,
    )
    agent_card_dict = agent_card_response["agentCards"][0]
    endpoint = resolve_preferred_interface(agent_card_dict)
    if log_sink is not None:
        log_sink(f"[client] agent-discovered: url={endpoint.url} binding={endpoint.protocol_binding}")

    client_factory = ClientFactory(
        ClientConfig(
            httpx_client=runtime["httpx_client"],
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
            use_client_preference=True,
        )
    )
    agent_card = ParseDict(agent_card_dict, AgentCard())
    a2a_client = client_factory.create(agent_card)

    if log_sink is not None:
        log_sink(f"[client] bootstrap-ready: server_url={endpoint.url}")
    return await flow(
        prompt_client=runtime["prompt_client"],
        a2a_client=a2a_client,
        initial_input=initial_input,
        max_artifacts=max_artifacts,
        log_sink=log_sink,
    )


def main(
    *,
    env_path: Path | None = None,
    run_async: object = run_main,
    async_runner: object = asyncio.run,
) -> object:
    """Entry point for the client: reads .env, installs mock/logger, and runs the async flow."""
    resolved_env_path = env_path or Path.cwd() / ".env"
    with _without_proxy_env():
        set_llm_log_sink(print)
        debug_enabled = resolve_sample_debug(env_path=resolved_env_path)
        max_artifacts = int(os.environ.get("A2AT_SAMPLE_MAX_ARTIFACTS", "0")) or None
        print(
            f"[client] startup: server_url={resolve_server_url(env_path=resolved_env_path)} "
            f"debug={'true' if debug_enabled else 'false'}"
        )
        return async_runner(
            run_async(
                env_path=resolved_env_path if resolved_env_path.exists() else None,
                initial_input=build_subscription_request(),
                log_sink=print,
                debug_enabled=debug_enabled,
                max_artifacts=max_artifacts,
            )
        )


if __name__ == "__main__":
    main()
