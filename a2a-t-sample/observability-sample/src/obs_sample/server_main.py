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
import uuid
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
from google.protobuf.json_format import MessageToDict, ParseDict
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
    """Negotiation-T flow with real per-round timing (2026-09-30):

    - **Negotiation rounds happen over bare Messages — NO task is created before
      agreement.** Each round: the client sends a Message carrying the
      Negotiation-T metadata + ``negotiationContext``; the server responds with a
      single negotiation Message (counter-offer, or ACCEPT confirmation for the
      accepting round) and the stream ends — the server disconnects after the
      Message. No task lifecycle events during rounds.
    - **The task starts only AFTER agreement**: a separate task request (Task-T
      metadata) triggers the full task lifecycle (submitted -> working ->
      artifact -> completed).

    The decorators create the ``SendStreamingMessage-negotiation`` spans
    (PARENT -> entry, both ends) during rounds, and the regular per-event spans
    only for the post-agreement task request. Each round is an independent
    trace; backends aggregate rounds by ``negotiation.id`` (spec §6.3 mode A).
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        metadata: dict[str, Any] = {}
        if context.message is not None:
            metadata = MessageToDict(context.message).get("metadata") or {}
        if _NEGOTIATION_T_URI in metadata or "negotiationContext" in metadata:
            await self._negotiation_round(context, event_queue, metadata.get("negotiationContext") or {})
        else:
            await self._run_agreed_task(context, event_queue)

    async def _negotiation_round(
        self, context: RequestContext, event_queue: EventQueue, negotiation_context: dict[str, Any]
    ) -> None:
        """One negotiation round: respond with a single negotiation Message — no task."""
        incoming_round = int(negotiation_context.get("round", 1))
        performative = str(negotiation_context.get("performative", "PROPOSE"))
        if performative == "PROPOSE":
            round_no = incoming_round + 1
            offer = "counter-offer: 松山湖区域资源配额下调至 50%"
        else:  # client accepted our counter-offer -> confirm agreement
            round_no = max(incoming_round, 3)
            offer = "agreed: 松山湖区域资源配额 50% 生效"
        response_context = {
            "id": str(negotiation_context.get("id", "N-sample")),
            "round": round_no,
            "maxRounds": int(negotiation_context.get("maxRounds", 5)),
            "performative": "PROPOSE" if performative == "PROPOSE" else "ACCEPT",
        }
        from a2a.types import Message as A2AMessage
        from a2a.types import Role

        message = A2AMessage(
            message_id=str(uuid.uuid4()),
            role=Role.ROLE_AGENT,
            context_id=context.context_id or "C-negotiation",
            parts=[],
        )
        message.metadata[_NEGOTIATION_T_URI] = offer
        message.metadata["negotiationContext"] = response_context
        await event_queue.enqueue_event(message)
        # Stream ends here on purpose: no task events, no completed — the server
        # disconnects after the negotiation Message. The task is started later by
        # a separate request, only after agreement.

    async def _run_agreed_task(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Post-agreement task execution: the task is created and runs to completion."""
        task_id = context.task_id or "T-negotiation-agreed"
        context_id = context.context_id or "C-negotiation"
        for state in (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING):
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(task_id=task_id, context_id=context_id, status=TaskStatus(state=state))
            )
        artifact = Artifact(artifact_id="A1", name="negotiation-result")
        artifact.parts.add(data=Value(string_value="agreed-allocation"))
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
