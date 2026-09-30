"""Observability sample client (v3.0 decorator architecture).

``register_client_factory(factory)`` decorates every client produced by the factory:
``factory.create(card)`` returns a client whose transport is wrapped automatically —
CLIENT entry spans, per-event spans and traceparent injection into the request headers
(via ``service_parameters``). No interceptors and no manual OTel setup.

Scenarios: ``--scenario=native`` (pure a2a-python flow) / ``--scenario=a2at``
(A2ATClient template generation + ``trace_facade`` L4 span, opt-in).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from a2a.client.client import ClientCallContext, ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.types import Role, SendMessageRequest
from a2a.utils.constants import TransportProtocol
from a2a_t.observability import register_client_factory, setup

_TASK_T = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1"
_NOTIFICATION_T_NL = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Notification-T/NL/v1"
_NEGOTIATION_T = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1"

_SAMPLE_INPUT = "请生成一个Incident事件订阅任务：通知主题为Incident，订阅条件为critical的ETH-LOS故障"


async def run(scenario: str) -> None:
    # Langfuse backend: activated when LANGFUSE_PUBLIC_KEY/SECRET_KEY are set;
    # sets the global TracerProvider so all A2A-T spans flow into Langfuse cloud.
    from obs_sample.langfuse_setup import setup_langfuse_if_configured

    setup_langfuse_if_configured()

    port = int(os.environ.get("A2AT_OBS_SAMPLE_PORT", "8100"))
    httpx_client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False)
    factory = ClientFactory(
        ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
            use_client_preference=True,
        )
    )
    card_dict = (await httpx_client.get("/.well-known/agent-card.json")).json()
    from a2a.types import AgentCard
    from google.protobuf.json_format import ParseDict

    agent_card = ParseDict(card_dict, AgentCard())
    register_client_factory(factory)  # v3.0: one line — all produced clients are observed
    client = factory.create(agent_card)

    metadata: dict[str, Any] = {_TASK_T: "sample-prompt-text"}
    prompt_text = "sample-prompt-text"
    if scenario == "negotiation":
        metadata = {
            _NEGOTIATION_T: "propose the resource allocation for the slice",
            "negotiationContext": {
                "id": "N-sample",
                "round": 3,
                "maxRounds": 5,
                "performative": "PROPOSE",
            },
        }
    elif scenario == "a2at":
        from obs_sample.mock_llm import install_mock_llm_if_needed

        install_mock_llm_if_needed(env_path=Path.cwd() / ".env")
        from a2a_t.client.a2at_client import A2ATClient
        from a2a_t.observability import trace_facade

        # Idempotent auto-config (Console/OTLP from env): needed here because the L4
        # facade span below runs BEFORE the first A2A call would auto-configure OTel.
        setup()
        prompt_client = trace_facade(A2ATClient(env_path=Path.cwd() / ".env"))  # L4 Span, opt-in
        result = prompt_client.generate_task_prompt(_SAMPLE_INPUT)
        if not result.success:
            raise SystemExit(f"prompt generation failed: {result.failure}")
        prompt_text = str(result.prompt_text)
        metadata = {_NOTIFICATION_T_NL: prompt_text}
        print(f"[client] generated prompt ({len(prompt_text)} chars) via A2ATClient")

    if scenario == "negotiation":
        # Metric demo: a2at.negotiation.total_rounds is auto-reported when the A2ATClient
        # facade constructs a TERMINAL negotiation message (accept/reject/abort). The
        # from-data accept path is deterministic (never calls an LLM), so no mock is needed.
        from a2a_t.client.a2at_client import A2ATClient
        from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
        from a2a_t.core.standard_templates import INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI
        from a2a_t.negotiation.content.enums import NegotiationConclusion
        from a2a_t.negotiation.content.models import (
            InformationEndingContent,
            NegotiationEndingData,
            NegotiationItem,
        )

        # Idempotent auto-config (Console/OTLP from env): needed here because the
        # metric report runs BEFORE the first A2A call would auto-configure OTel.
        setup()
        from obs_sample.mock_llm import install_mock_llm_if_needed

        # The accept-from-data path is deterministic (never calls an LLM), but the
        # facade constructor still requires a non-empty API key in .env — the mock
        # installer patches DotEnvConfigSource.load to inject one when empty.
        install_mock_llm_if_needed(env_path=Path.cwd() / ".env")
        facade = A2ATClient(env_path=Path.cwd() / ".env")
        data = NegotiationEndingData(
            NegotiationContext("N-sample", 3, 5, NegotiationPerformative.ACCEPT),
            InformationEndingContent(
                NegotiationConclusion.ACCEPT, [NegotiationItem("节能区域", "松山湖")]
            ),
        )
        facade.generate_negotiation_accept_prompt_from_data(data, INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI)
        print("[client] reported a2at.negotiation.total_rounds=3 (outcome=accept) via A2ATClient")

    request = SendMessageRequest()
    request.message.message_id = str(uuid.uuid4())
    # context_id anchors the server-side entry-span registry: without a client-provided
    # context_id the executor decorator cannot find the SERVER entry span, and the
    # server-side per-event spans are skipped (server-generated ids are registered only
    # at enqueue time, after the executor has already been looked up).
    request.message.context_id = str(uuid.uuid4())
    request.message.role = Role.ROLE_USER
    request.message.parts.add().text = "observability-sample"
    for key, value in metadata.items():
        request.message.metadata[key] = value
    # Push notification config: the server echoes notifications back to /push-sink,
    # so the run demonstrates SendMessage-pushNotification spans end to end.
    request.configuration.task_push_notification_config.id = "push-sample"
    request.configuration.task_push_notification_config.url = f"http://127.0.0.1:{port}/push-sink"
    header_value = next(iter(metadata))
    context = ClientCallContext(service_parameters={"A2A-Extensions": header_value})

    async for response in client.send_message(request, context=context):
        print(f"[client] event: {type(response).__name__}")
    await httpx_client.aclose()
    print("[client] done — inspect the Console span output above")


def main() -> None:
    # Set service.name BEFORE any OTel/Langfuse init so the Resource is correct
    os.environ.setdefault("OTEL_SERVICE_NAME", "a2a-t-oss-client")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        choices=["native", "a2at", "negotiation"],
        default="native",
        help="native=Task-T streaming, a2at=A2ATClient prompt generation, negotiation=Negotiation-T terminal Message",
    )
    args = parser.parse_args()
    asyncio.run(run(args.scenario))


if __name__ == "__main__":
    main()
