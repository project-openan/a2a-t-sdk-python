from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
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
from common.webhook_receiver import WebhookReceiver
from dotenv import dotenv_values
from google.protobuf.json_format import ParseDict

from client.client_flow import run_client_flow
from client.negotiation_response import NegotiationResponseProvider, ScriptedNegotiationResponseProvider
from client.scenario_data import (
    CHANNEL_DATA,
    NEGOTIATION_KIND_FEASIBILITY,
    NEGOTIATION_KIND_TARGET,
    SCENARIO_NEGOTIATION,
    TRANSPORT_SSE,
    TRANSPORT_WEBHOOK,
    build_prompt_input,
    build_task_request,
    negotiation_answers,
    negotiation_filled_params,
    resolve_channel,
    resolve_negotiation_kind,
    resolve_scenario,
    resolve_transport,
    resolve_webhook_idle_timeout,
    resolve_webhook_port,
    resolve_webhook_token,
)

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
            continue

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
    input_text: str,
    bootstrap: object = build_client_runtime,
    flow: object = run_client_flow,
    logger: Any | None = None,
    log_sink: Callable[[str], None] | None = None,
    debug_enabled: bool = False,
    max_artifacts: int | None = None,
    registry_query_fn: object = query_by_name_org,
    negotiation_params: dict[str, object] | None = None,
    negotiation_answers: dict[str, str] | None = None,
    negotiation_kind: str | None = None,
    negotiation_response_provider: NegotiationResponseProvider | None = None,
    channel: str = CHANNEL_DATA,
    transport: str = TRANSPORT_SSE,
    webhook_port: int | None = None,
    webhook_token: str | None = None,
    webhook_idle_timeout: float | None = None,
) -> object:
    """Run the full client flow: bootstrap runtime, discover agent, generate prompt, consume events.

    The negotiation arguments are supplied by the caller for the negotiation kinds: when the server
    answers the round-1 prompt with an INPUT_REQUIRED propose, the flow answers it with the filled
    Task-T prompt and the Negotiation-T accept of the resolved type (round 2). The ``negotiation_kind``
    flag selects the accept type directly; ``None`` dispatches on the propose's ``templateUri``.
    ``negotiation_response_provider`` is the client-side INTEGRATION POINT: when supplied, the flow
    derives the answer from it and the scripted answers are bypassed (``main`` assembles the demo
    ``ScriptedNegotiationResponseProvider``).

    Transport (plan v3): ``sse`` (default) consumes the streaming response unchanged; ``webhook``
    starts the embedded ``WebhookReceiver`` on THIS asyncio loop before the flow runs (lifecycle:
    stopped in the ``finally`` after the flow ends or Ctrl+C unwinds), feeds its events through an
    ``asyncio.Queue`` into the flow, and builds the a2a client with ``ClientConfig(streaming=False)``
    (R2 — required to select non-blocking ``message:send``). The receiver's port/token/idle-timeout
    resolve from the run ``.env`` unless explicitly supplied.
    """
    resolved_logger = logger
    if resolved_logger is None and log_sink is not None and debug_enabled:
        resolved_logger = build_sample_logger(sink=log_sink, debug_enabled=True)
    runtime = await bootstrap(env_path=env_path, logger=resolved_logger)

    # Webhook-mode runtime (v3): receiver on this loop + event queue + registered push params.
    webhook_queue: asyncio.Queue[dict[str, object]] | None = None
    receiver: WebhookReceiver | None = None
    push_url: str | None = None
    push_token: str | None = None
    resolved_idle_timeout: float = 30.0
    if transport == TRANSPORT_WEBHOOK:
        resolved_token = webhook_token if webhook_token is not None else resolve_webhook_token(env_path=env_path)
        resolved_port = webhook_port if webhook_port is not None else resolve_webhook_port(env_path=env_path)
        resolved_idle_timeout = (
            webhook_idle_timeout
            if webhook_idle_timeout is not None
            else resolve_webhook_idle_timeout(env_path=env_path)
        )
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        receiver = WebhookReceiver(
            token=resolved_token,
            on_event=queue.put_nowait,
            port=resolved_port,
            log_sink=log_sink,
        )
        await receiver.start()
        webhook_queue = queue
        push_url = receiver.url
        push_token = resolved_token
        if log_sink is not None:
            log_sink(
                f"[client] webhook-mode: push_url={push_url} idle_timeout={resolved_idle_timeout}s "
                "(client streaming=false, events via push notifications)"
            )

    try:
        agent_card_query = initial_input.get("agent_card_query")
        query_name = str(agent_card_query.get("name", "")) if isinstance(agent_card_query, dict) else ""
        query_org = str(agent_card_query.get("organization", "")) if isinstance(agent_card_query, dict) else ""
        agent_card_response = await registry_query_fn(
            organization=query_org,
            name=query_name,
            env_path=env_path,
            log_sink=log_sink,
        )
        agent_cards = agent_card_response.get("agentCards") or []
        if not agent_cards:
            raise ValueError(
                f"agent discovery failed: no AgentCard found for name='{query_name}' organization='{query_org}'"
            )
        agent_card_dict = agent_cards[0]
        endpoint = resolve_preferred_interface(agent_card_dict)
        if log_sink is not None:
            log_sink(f"[client] agent-discovered: url={endpoint.url} binding={endpoint.protocol_binding}")

        client_config = ClientConfig(
            httpx_client=runtime["httpx_client"],
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
            use_client_preference=True,
        )
        if transport == TRANSPORT_WEBHOOK:
            # R2 (builder-check V2): the high-level client selects non-blocking message:send ONLY
            # when the config carries streaming=False — return_immediately on the request alone
            # does not switch the path.
            client_config.streaming = False
        client_factory = ClientFactory(client_config)
        agent_card = ParseDict(agent_card_dict, AgentCard())
        a2a_client = client_factory.create(agent_card)

        if log_sink is not None:
            log_sink(f"[client] bootstrap-ready: server_url={endpoint.url}")
        flow_kwargs: dict[str, object] = {
            "prompt_client": runtime["prompt_client"],
            "a2a_client": a2a_client,
            "initial_input": initial_input,
            "input_text": input_text,
            "max_artifacts": max_artifacts,
            "log_sink": log_sink,
            "negotiation_params": negotiation_params,
            "negotiation_answers": negotiation_answers,
            "negotiation_kind": negotiation_kind,
            "negotiation_response_provider": negotiation_response_provider,
            "channel": channel,
            "env_path": env_path,
        }
        if transport == TRANSPORT_WEBHOOK:
            flow_kwargs.update(
                transport=transport,
                webhook_queue=webhook_queue,
                push_url=push_url,
                push_token=push_token,
                webhook_idle_timeout=resolved_idle_timeout,
            )
        return await flow(**flow_kwargs)
    finally:
        # Receiver lifecycle follows the flow (and Ctrl+C: the KeyboardInterrupt unwinds through
        # here exactly as in SSE mode — the receiver installs no signal handlers of its own).
        if receiver is not None:
            await receiver.stop()


def main(
    *,
    env_path: Path | None = None,
    run_async: object = run_main,
    async_runner: object = asyncio.run,
) -> object:
    """Entry point for the client: reads .env, installs mock/logger, and runs the async flow."""
    resolved_env_path = env_path or Path.cwd() / ".env"
    sample_env_path = resolved_env_path if resolved_env_path.exists() else None
    with _without_proxy_env():
        set_llm_log_sink(print)
        debug_enabled = resolve_sample_debug(env_path=resolved_env_path)
        raw_max_artifacts = int(os.environ.get("A2AT_SAMPLE_MAX_ARTIFACTS", "0") or "0")
        max_artifacts = raw_max_artifacts if raw_max_artifacts > 0 else None
        scenario = resolve_scenario(env_path=resolved_env_path)
        channel = resolve_channel(env_path=sample_env_path)
        negotiation_kind = resolve_negotiation_kind(env_path=sample_env_path)
        transport = resolve_transport(env_path=sample_env_path)
        print(
            f"[client] startup: server_url={resolve_server_url(env_path=resolved_env_path)} "
            f"debug={'true' if debug_enabled else 'false'} scenario={scenario} channel={channel} "
            f"transport={transport}"
        )
        # The target/feasibility kinds select their own (complete) scenario input regardless of the
        # scenario switch, so they engage the negotiation round trip like the negotiation scenario.
        negotiation_engaged = scenario == SCENARIO_NEGOTIATION or negotiation_kind in (
            NEGOTIATION_KIND_TARGET,
            NEGOTIATION_KIND_FEASIBILITY,
        )
        negotiation_params = negotiation_filled_params(env_path=sample_env_path) if negotiation_engaged else None
        negotiation_answers_map = negotiation_answers(env_path=sample_env_path) if negotiation_engaged else None
        # INTEGRATION POINT: replace ScriptedNegotiationResponseProvider with your negotiation
        # response provider (human confirmation, policy engine, upstream system...)
        negotiation_response_provider = (
            ScriptedNegotiationResponseProvider(
                negotiation_params=negotiation_params,
                negotiation_answers=negotiation_answers_map,
                negotiation_kind=negotiation_kind,
                env_path=sample_env_path,
            )
            if negotiation_engaged
            else None
        )
        return async_runner(
            run_async(
                env_path=sample_env_path,
                initial_input=build_task_request(),
                input_text=build_prompt_input(env_path=sample_env_path, scenario=scenario),
                log_sink=print,
                debug_enabled=debug_enabled,
                max_artifacts=max_artifacts,
                negotiation_params=negotiation_params,
                negotiation_answers=negotiation_answers_map,
                negotiation_kind=negotiation_kind,
                negotiation_response_provider=negotiation_response_provider,
                channel=channel,
                transport=transport,
            )
        )


if __name__ == "__main__":
    main()
