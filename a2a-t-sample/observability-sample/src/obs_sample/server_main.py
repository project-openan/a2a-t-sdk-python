"""Observability sample server (v3.0 decorator architecture, no LLM needed).

One-line wiring: ``A2ATRequestHandlerDecorator(LegacyRequestHandler(...))`` creates the
SERVER entry spans, wraps the executor for per-event spans and wraps the push sender —
no ASGI middleware and no manual OTel setup (the SDK auto-configures Console/OTLP
exporters on first use when ``A2AT_OBSERVABILITY_ENABLED=true``).

Uses ``LegacyRequestHandler``: a2a-sdk 1.1.2's default ``DefaultRequestHandler`` (V2)
captures raw ``agent_executor``/``_push_sender`` references at construction time, so the
decorator's post-construction wraps would be bypassed (see README "a2a-sdk 1.1.x handler
caveat").
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import uvicorn
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers.default_request_handler import LegacyRequestHandler
from a2a.server.routes import create_agent_card_routes, create_rest_routes
from a2a.server.tasks.base_push_notification_sender import BasePushNotificationSender
from a2a.server.tasks.inmemory_push_notification_config_store import InMemoryPushNotificationConfigStore
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import Artifact, TaskArtifactUpdateEvent, TaskState, TaskStatus, TaskStatusUpdateEvent
from a2a.utils.constants import TransportProtocol
from a2a_t.observability import A2ATRequestHandlerDecorator
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Value
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def _sample_port() -> int:
    return int(os.environ.get("A2AT_OBS_SAMPLE_PORT", "8100"))


class EchoExecutor(AgentExecutor):
    """Emits submitted -> working -> artifact -> completed; mirrors a Task-T streaming flow."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or "T-sample"
        context_id = context.context_id or "C-sample"
        for state in (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING):
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(task_id=task_id, context_id=context_id, status=TaskStatus(state=state))
            )
        artifact = Artifact(artifact_id="A1", name="sample.artifact")
        artifact.parts.add(data=Value(string_value="sample-result"))
        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(task_id=task_id, context_id=context_id, artifact=artifact)
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None: ...


_NEGOTIATION_T_URI = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1"


class NegotiationExecutor(AgentExecutor):
    """Detects a Negotiation-T request and runs a negotiate-over-task flow.

    Emits the full task status lifecycle (submitted -> working -> completed) with an
    artifact, then finishes with a terminal negotiation Message. The Message carries
    ``metadata[Negotiation-T URI]`` plus a structured ``negotiationContext`` — the
    decorators create the ``SendStreamingMessage-event`` spans (status/artifact,
    LINK on the client / PARENT on the server) AND the
    ``SendStreamingMessage-negotiation`` span (PARENT -> entry span) automatically.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or "T-negotiation"
        context_id = context.context_id or "C-negotiation"
        # 1. Task status lifecycle (per-event spans: status)
        for state in (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING):
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(task_id=task_id, context_id=context_id, status=TaskStatus(state=state))
            )
        # 2. Artifact update (per-event span: artifact)
        artifact = Artifact(artifact_id="A1", name="negotiation-result")
        artifact.parts.add(data=Value(string_value="agreed-allocation"))
        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(task_id=task_id, context_id=context_id, artifact=artifact)
        )
        # 3. Terminal negotiation Message (negotiation span: PARENT -> entry).
        #    NOTE: the Message is a stream-end signal (spec §7.3) — it MUST come
        #    before the terminal status event, otherwise the client decorator
        #    breaks on the terminal state and never sees the Message.
        from a2a.types import Message as A2AMessage
        from a2a.types import Role

        message = A2AMessage(
            message_id=f"negotiation-response-{task_id}",
            role=Role.ROLE_AGENT,
            task_id=task_id,
            context_id=context_id,
            parts=[],
        )
        message.metadata[_NEGOTIATION_T_URI] = "agree to the proposed allocation"
        message.metadata["negotiationContext"] = {
            "id": context_id,
            "round": 3,
            "maxRounds": 5,
            "performative": "ACCEPT",
        }
        await event_queue.enqueue_event(message)
        # 4. Task completed (server-side per-event span only; the client stream
        #    has already ended at the Message — server persists the final state)
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None: ...


def build_agent_card_payload(port: int) -> dict[str, Any]:
    return {
        "name": "obs-sample-server",
        "description": "observability sample server",
        "version": "1.0.0",
        "supportedInterfaces": [
            {
                "protocolBinding": TransportProtocol.HTTP_JSON.value,
                "protocolVersion": "1.0.0",
                "url": f"http://127.0.0.1:{port}",
            }
        ],
        "capabilities": {"streaming": True, "pushNotifications": True},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [],
    }


def build_app(*, scenario: str = "native") -> Starlette:
    from a2a.types import AgentCard

    port = _sample_port()
    agent_card = ParseDict(build_agent_card_payload(port), AgentCard())
    executor: AgentExecutor = NegotiationExecutor() if scenario == "negotiation" else EchoExecutor()
    # Push sender is wired into the handler so A2ATRequestHandlerDecorator wraps it
    # (SendMessage-pushNotification spans); notifications loop back to /push-sink below.
    push_store = InMemoryPushNotificationConfigStore()
    push_http = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False)
    handler = A2ATRequestHandlerDecorator(
        LegacyRequestHandler(
            agent_executor=executor,
            task_store=InMemoryTaskStore(),
            agent_card=agent_card,
            push_config_store=push_store,
            push_sender=BasePushNotificationSender(push_http, push_store),
        )
    )

    async def push_sink(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    return Starlette(
        routes=[
            *create_agent_card_routes(agent_card),
            *create_rest_routes(handler),
            Route("/push-sink", push_sink, methods=["POST"]),
        ],
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        choices=["native", "negotiation"],
        default="native",
        help="executor: native=Task-T streaming, negotiation=Negotiation-T terminal Message",
    )
    args = parser.parse_args()
    # Set service.name BEFORE any OTel/Langfuse init so the Resource is correct
    os.environ.setdefault("OTEL_SERVICE_NAME", "a2a-t-ems-server")
    # Langfuse backend: activated when LANGFUSE_PUBLIC_KEY/SECRET_KEY are set;
    # sets the global TracerProvider so all A2A-T spans flow into Langfuse cloud.
    from obs_sample.langfuse_setup import setup_langfuse_if_configured

    setup_langfuse_if_configured()
    uvicorn.run(build_app(scenario=args.scenario), host="127.0.0.1", port=_sample_port())


if __name__ == "__main__":
    main()
