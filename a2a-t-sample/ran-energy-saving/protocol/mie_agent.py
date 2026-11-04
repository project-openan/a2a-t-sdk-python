"""MIE Agent: the human approval hop of the pure A2A three-hop mode.

The RAN Agent calls this A2A server before applying a plan. The server creates a standard A2A Task,
publishes an approval request to the presentation UI over SSE, waits for the engineer's decision,
and completes the Task with an ``approval decision`` artifact. The A2A hop itself is standard: no
A2A-T metadata is exchanged.

The agent is intentionally not an LLM: it wraps a human decision. Keeping it separate from the RAN
server makes the third hop observable in the demo and keeps the approval transport independent from
the RAN planner.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import uvicorn
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
from google.protobuf.json_format import ParseDict
from shared.config import SampleConfig, load_config
from shared.logging_setup import configure_logging
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from protocol.approval_gate import DEFAULT_APPROVAL_TIMEOUT_SECONDS
from protocol.messages import metadata_to_dict

#: Agent identity and skill.
MIE_AGENT_NAME: Final[str] = "MIE Agent"
MIE_AGENT_ORGANIZATION: Final[str] = "Huawei"
MIE_AGENT_PROVIDER_URL: Final[str] = "https://www.huawei.com"
MIE_AGENT_SKILL_ID: Final[str] = "ran-energy-saving-approval"
MIE_AGENT_DESCRIPTION: Final[str] = (
    "Reviews a RAN energy-saving plan and asks the engineer to approve or reject it before it is "
    "applied."
)

#: Artifact carrying the human decision.
MIE_DECISION_ARTIFACT_NAME: Final[str] = "approval decision"

#: Default port of the MIE A2A server.
DEFAULT_MIE_PORT: Final[int] = 8002

_LOGGER = logging.getLogger("ran_energy_saving.mie_agent")


@dataclass
class PendingApproval:
    """One plan awaiting the engineer's decision."""

    task_id: str
    plan: dict[str, object]
    timeout_seconds: float
    event: asyncio.Event = field(default_factory=asyncio.Event)
    approved: bool | None = None
    reason: str | None = None


class MIEApprovalRegistry:
    """In-memory bridge between the A2A approval Task and the presentation UI."""

    def __init__(self) -> None:
        """Create one empty registry."""
        self._pending: dict[str, PendingApproval] = {}
        self._subscribers: list[asyncio.Queue[dict[str, object]]] = []

    @property
    def pending(self) -> dict[str, PendingApproval]:
        """Return the pending approvals by MIE Task id (test hook)."""
        return dict(self._pending)

    def snapshot(self) -> list[dict[str, object]]:
        """Return the approval-requested events of every pending approval."""
        return [
            self._requested_event(pending) for pending in self._pending.values()
        ]

    def subscribe(self) -> asyncio.Queue[dict[str, object]]:
        """Register one SSE subscriber and return its queue."""
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, object]]) -> None:
        """Remove one SSE subscriber."""
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def decide(self, task_id: str, approved: bool) -> bool:
        """Resolve one pending approval; return ``False`` when it is not pending."""
        pending = self._pending.get(task_id)
        if pending is None:
            return False
        pending.approved = approved
        pending.reason = None
        pending.event.set()
        return True

    async def request(
        self,
        task_id: str,
        plan: Mapping[str, object],
        timeout_seconds: float,
    ) -> tuple[bool, str | None]:
        """Publish one approval request, wait for the human, and return the decision."""
        pending = PendingApproval(
            task_id=task_id,
            plan=dict(plan),
            timeout_seconds=timeout_seconds,
        )
        self._pending[task_id] = pending
        self._broadcast(self._requested_event(pending))
        try:
            await asyncio.wait_for(pending.event.wait(), timeout_seconds)
        except TimeoutError:
            pending.approved = False
            pending.reason = "timeout"
        finally:
            self._pending.pop(task_id, None)
        approved = bool(pending.approved)
        self._broadcast(
            {
                "type": "approval_resolved",
                "taskId": task_id,
                "approved": approved,
                "reason": pending.reason,
            }
        )
        return approved, pending.reason

    def _requested_event(self, pending: PendingApproval) -> dict[str, object]:
        """Build the SSE payload of one approval request."""
        return {
            "type": "approval_requested",
            "taskId": pending.task_id,
            "plan": dict(pending.plan),
            "timeoutSeconds": pending.timeout_seconds,
        }

    def _broadcast(self, event: dict[str, object]) -> None:
        """Fan out one event to every current SSE subscriber."""
        for queue in list(self._subscribers):
            queue.put_nowait(event)


class MIEAgentExecutor(AgentExecutor):
    """Run one approval Task and map the human decision to A2A events."""

    def __init__(
        self,
        *,
        registry: MIEApprovalRegistry,
        timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    ) -> None:
        """Create one MIE executor.

        Args:
            registry: bridge to the presentation UI.
            timeout_seconds: seconds to wait before treating silence as a rejection.
        """
        self._registry = registry
        self._timeout_seconds = timeout_seconds

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Create the approval Task, wait for the engineer, and publish the decision."""
        queue = event_queue
        if context.current_task is not None:
            await queue.enqueue_event(context.current_task)
            return
        message = context.message
        if message is None:
            raise ValueError("The MIE request carries no A2A message")

        task = new_task_from_user_message(message)
        task.id = context.task_id or str(uuid.uuid4())
        task.context_id = context.context_id or task.id
        await queue.enqueue_event(task)

        updater = TaskUpdater(queue, task.id, task.context_id)
        await updater.start_work()
        plan = _plan_from_message(message)
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=updater.new_agent_message(
                [Part(text="MIE review started: waiting for the engineer's decision.")],
                metadata={"plan": plan},
            ),
        )
        approved, reason = await self._registry.request(
            task.id, plan, self._timeout_seconds
        )

        decision = {"approved": approved, "reason": reason}
        decision_part = Part()
        ParseDict(decision, decision_part.data)
        await updater.add_artifact(
            [decision_part, Part(text=_decision_text(approved, reason))],
            name=MIE_DECISION_ARTIFACT_NAME,
            metadata=decision,
            last_chunk=True,
        )
        _LOGGER.info(
            "MIE decision: task=%s approved=%s reason=%s", task.id, approved, reason
        )
        await updater.update_status(
            TaskState.TASK_STATE_COMPLETED,
            message=updater.new_agent_message(
                [Part(text=_decision_text(approved, reason))],
                metadata=decision,
            ),
            metadata=decision,
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancel one approval Task."""
        updater = TaskUpdater(event_queue, context.task_id or "", context.context_id or "")
        await updater.cancel()


def build_mie_agent_card(
    *, host: str, port: int, secured: bool = False
) -> AgentCard:
    """Build the AgentCard of the MIE approval Agent."""
    card = AgentCard(
        name=MIE_AGENT_NAME,
        description=MIE_AGENT_DESCRIPTION,
        version="1.0.0",
        provider=AgentProvider(
            organization=MIE_AGENT_ORGANIZATION, url=MIE_AGENT_PROVIDER_URL
        ),
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json", "text/plain"],
        skills=[
            AgentSkill(
                id=MIE_AGENT_SKILL_ID,
                name="RAN energy-saving plan approval",
                description=MIE_AGENT_DESCRIPTION,
                tags=["approval", "energy saving"],
                examples=["Review and approve the proposed energy-saving plan."],
            )
        ],
    )
    card.supported_interfaces.add(
        url=f"http://{host}:{port}",
        protocol_binding=TransportProtocol.HTTP_JSON.value,
        protocol_version="1.0.0",
    )
    return card


def build_mie_app(
    *,
    config: SampleConfig | None = None,
    registry: MIEApprovalRegistry | None = None,
    task_store: TaskStore | None = None,
    host: str | None = None,
    port: int | None = None,
    timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
) -> Starlette:
    """Build the MIE A2A app and its approval SSE/POST routes."""
    del config
    resolved_host = host or os.environ.get("A2AT_SAMPLE_MIE_HOST", "127.0.0.1")
    resolved_port = port or int(os.environ.get("A2AT_SAMPLE_MIE_PORT", str(DEFAULT_MIE_PORT)))
    resolved_registry = registry or MIEApprovalRegistry()
    card = build_mie_agent_card(host=resolved_host, port=resolved_port)
    executor = MIEAgentExecutor(
        registry=resolved_registry, timeout_seconds=timeout_seconds
    )
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store or InMemoryTaskStore(),
        agent_card=card,
    )

    async def approval_events(request: Request) -> StreamingResponse:
        """Stream pending and new approval requests to the presentation UI."""
        queue = resolved_registry.subscribe()
        snapshot = resolved_registry.snapshot()

        async def stream() -> AsyncGenerator[bytes, None]:
            try:
                for event in snapshot:
                    yield _sse(event)
                while True:
                    event = await queue.get()
                    yield _sse(event)
            finally:
                resolved_registry.unsubscribe(queue)

        del request
        return StreamingResponse(stream(), media_type="text/event-stream")

    async def decide(request: Request) -> Response:
        """Resolve one pending approval by its MIE Task id."""
        task_id = request.path_params.get("task_id", "")
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(
                {"error": "The body must be a JSON object.", "code": "body_not_object"},
                status_code=400,
            )
        if not isinstance(body, dict) or not isinstance(body.get("approved"), bool):
            return JSONResponse(
                {"error": "The approved field must be a boolean.", "code": "approved_invalid"},
                status_code=400,
            )
        if not resolved_registry.decide(task_id, body["approved"]):
            return JSONResponse(
                {"error": "No approval is pending for this task.", "code": "approval_not_pending"},
                status_code=409,
            )
        return JSONResponse({"resolved": True, "approved": body["approved"]})

    app = Starlette(
        routes=[
            # The A2A REST bundle mounts a ``/{tenant}`` alias; the approval routes must be
            # registered first or ``/approvals/events`` is swallowed by that mount.
            Route("/approvals/events", approval_events, methods=["GET"]),
            Route("/approvals/{task_id}", decide, methods=["POST"]),
            *create_agent_card_routes(card),
            *create_rest_routes(request_handler),
        ]
    )
    app.state.agent_card = card
    app.state.approval_registry = resolved_registry
    app.state.request_handler = request_handler
    return app


def main() -> None:
    """Entry point: serve the MIE Agent on the configured port."""
    configure_logging()
    env_path = Path.cwd() / ".env"
    config = load_config(env_path=env_path if env_path.exists() else None)
    host = os.environ.get("A2AT_SAMPLE_MIE_HOST", "127.0.0.1")
    port = int(os.environ.get("A2AT_SAMPLE_MIE_PORT", str(DEFAULT_MIE_PORT)))
    timeout = float(
        os.environ.get(
            "A2AT_SAMPLE_APPROVAL_TIMEOUT_SECONDS", str(int(DEFAULT_APPROVAL_TIMEOUT_SECONDS))
        )
        or str(int(DEFAULT_APPROVAL_TIMEOUT_SECONDS))
    )
    app = build_mie_app(config=config, host=host, port=port, timeout_seconds=timeout)
    _LOGGER.info("starting MIE Agent on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port)


def _plan_from_message(message: object) -> dict[str, object]:
    """Return the plan carried in one A2A message metadata."""
    metadata = getattr(message, "metadata", None)
    raw = metadata_to_dict(metadata).get("plan")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _decision_text(approved: bool, reason: str | None) -> str:
    """Render the human decision as one status/artifact text."""
    if approved:
        return "The engineer approved the plan."
    if reason == "timeout":
        return "The approval window expired; nothing was applied."
    return "The engineer rejected the change; nothing was applied."


def _sse(event: Mapping[str, object]) -> bytes:
    """Encode one SSE data frame."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")


__all__ = [
    "DEFAULT_MIE_PORT",
    "MIE_AGENT_NAME",
    "MIE_DECISION_ARTIFACT_NAME",
    "MIEAgentExecutor",
    "MIEApprovalRegistry",
    "build_mie_agent_card",
    "build_mie_app",
    "main",
]


if __name__ == "__main__":
    main()
