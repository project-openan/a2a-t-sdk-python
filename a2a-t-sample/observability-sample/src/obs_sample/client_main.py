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
    if scenario == "a2at":
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
        # Real per-round timing (2026-09-30): negotiation rounds are BARE-Message
        # exchanges — NO task is created before agreement. Each round: the client
        # sends a Message, the server responds with a negotiation Message and
        # disconnects (the stream ends). After agreement the task is started by a
        # separate request (Task-T metadata) and runs its full lifecycle.
        #
        # Business-flow root (spec 6.3 mode B): one trace for the WHOLE flow. The
        # root's traceparent is injected into every request's service_parameters;
        # the client entry spans extract it as their parent (explicit, leak-free
        # propagation — the rounds + task all land on one Langfuse trace).
        from a2a_t.observability.propagation import inject_traceparent
        from opentelemetry import trace as otel_trace

        tracer = otel_trace.get_tracer("obs-sample")
        root_span = tracer.start_span("negotiation-flow")
        root_headers: dict[str, str] = {}
        inject_traceparent(root_headers, context=otel_trace.set_span_in_context(root_span))

        def flow_context(extension: str) -> ClientCallContext:
            params: dict[str, str] = {"A2A-Extensions": extension, **root_headers}
            return ClientCallContext(service_parameters=params)

        from google.protobuf.json_format import MessageToDict

        conversation_id = str(uuid.uuid4())
        rounds: list[tuple[str, dict[str, Any]]] = [
            (
                "round1-PROPOSE",
                {
                    _NEGOTIATION_T: "initial proposal: 松山湖区域资源配额 80%",
                    "negotiationContext": {
                        "id": "N-sample",
                        "round": 1,
                        "maxRounds": 5,
                        "performative": "PROPOSE",
                    },
                },
            ),
            (
                "round2-ACCEPT",
                {
                    _NEGOTIATION_T: "accept counter-offer: 松山湖区域资源配额 50% 生效",
                    "negotiationContext": {
                        "id": "N-sample",
                        "round": 3,
                        "maxRounds": 5,
                        "performative": "ACCEPT",
                    },
                },
            ),
        ]
        for label, round_metadata in rounds:
            request = SendMessageRequest()
            request.message.message_id = str(uuid.uuid4())
            request.message.context_id = conversation_id
            request.message.role = Role.ROLE_USER
            request.message.parts.add().text = "observability-sample"
            for key, value in round_metadata.items():
                request.message.metadata[key] = value
            round_context = flow_context(_NEGOTIATION_T)
            print(f"[client] {label}: sending")
            async for response in client.send_message(request, context=round_context):
                reply = MessageToDict(response.message)
                reply_ctx = reply.get("metadata", {}).get("negotiationContext", {})
                print(
                    f"[client] {label}: reply performative={reply_ctx.get('performative')} "
                    f"round={int(reply_ctx.get('round', 0))}"
                )
            # stream ends: the server disconnected after the negotiation Message
        print("[client] negotiation agreed (2 rounds) — no task was created during rounds")

        # Metric demo AFTER agreement: a2at.negotiation.total_rounds is auto-reported
        # when the A2ATClient facade constructs a TERMINAL negotiation message
        # (accept/reject/abort). The from-data accept path is deterministic (never
        # calls an LLM), so no mock is needed.
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

        # The task starts AFTER agreement: separate request, full task lifecycle.
        task_request = SendMessageRequest()
        task_request.message.message_id = str(uuid.uuid4())
        task_request.message.context_id = conversation_id
        task_request.message.role = Role.ROLE_USER
        task_request.message.parts.add().text = "observability-sample"
        task_request.message.metadata[_TASK_T] = _SAMPLE_INPUT
        task_request.configuration.task_push_notification_config.id = "push-sample"
        task_request.configuration.task_push_notification_config.url = f"http://127.0.0.1:{port}/push-sink"
        task_context = flow_context(_TASK_T)
        print("[client] task-start: sending (task created only after agreement)")
        async for response in client.send_message(task_request, context=task_context):
            print(f"[client] task event: {type(response).__name__}")
        root_span.end()
        await httpx_client.aclose()
        print("[client] done — ONE Langfuse trace covers rounds + task (negotiation-flow root)")
        return

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
