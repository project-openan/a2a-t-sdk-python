"""A2A client used by the RAN Agent to ask the MIE Agent for approval.

This is the third hop of the pure A2A mode. It is a standard A2A client: it discovers the MIE
AgentCard, sends a plain message carrying the plan, and reads the ``approval decision`` artifact.
No A2A-T extension is involved.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

import httpx
from a2a.client import ClientConfig, create_client
from a2a.client.client import ClientCallContext
from a2a.types import Role, SendMessageRequest
from a2a.utils.constants import TransportProtocol
from shared.config import DEFAULT_HTTP_TIMEOUT_SECONDS
from shared.sse_event_consumer import normalize_event
from shared.trace_propagation import inject_outgoing

from protocol.discovery import DISCOVERY_DIRECT, discover_agent
from protocol.messages import format_rate_percent
from protocol.mie_agent import (
    MIE_AGENT_NAME,
    MIE_AGENT_ORGANIZATION,
    MIE_DECISION_ARTIFACT_NAME,
)

_LOGGER = logging.getLogger("ran_energy_saving.mie_client")

#: Metadata key carrying the plan in the RAN->MIE message.
PLAN_METADATA_KEY: str = "plan"


@dataclass(frozen=True)
class ApprovalDecision:
    """One human decision returned by the MIE Agent."""

    approved: bool
    reason: str | None = None


class MIEApprovalClient:
    """Request one plan approval from a running MIE A2A server."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        discovery_timeout_seconds: float = 30.0,
    ) -> None:
        """Create one client.

        Args:
            base_url: MIE server base URL (direct discovery).
            timeout_seconds: budget of the approval A2A call; must exceed the human gate.
            discovery_timeout_seconds: budget of the AgentCard fetch.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._discovery_timeout_seconds = discovery_timeout_seconds

    async def request_approval(
        self,
        *,
        cell_id: str,
        window: str,
        target_rate: str,
        evaluation: Mapping[str, object] | None,
        run_id: str | None,
        ran_task_id: str,
    ) -> ApprovalDecision:
        """Send the plan to MIE and return its decision."""
        plan: dict[str, object] = {
            "cellId": cell_id,
            "window": window,
            "rate": format_rate_percent(target_rate),
            "savedPercent": (
                evaluation.get("estimated_saving_percent")
                if isinstance(evaluation, Mapping)
                else None
            ),
            "ranTaskId": ran_task_id,
        }
        request = _approval_request(plan, run_id=run_id)
        try:
            discovered = await discover_agent(
                discovery=DISCOVERY_DIRECT,
                organization=MIE_AGENT_ORGANIZATION,
                name=MIE_AGENT_NAME,
                direct_base_url=self._base_url,
                timeout_seconds=self._discovery_timeout_seconds,
            )
            httpx_client = httpx.AsyncClient(
                timeout=self._timeout_seconds,
                trust_env=False,
                event_hooks={"request": [inject_outgoing]},
            )
            try:
                client = await create_client(
                    discovered.agent_card,
                    client_config=ClientConfig(
                        streaming=True,
                        httpx_client=httpx_client,
                        supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
                        use_client_preference=True,
                    ),
                )
                try:
                    events = await _send_events(
                        client,
                        request,
                        ClientCallContext(
                            timeout=self._timeout_seconds,
                            service_parameters={"A2A-Extensions": ""},
                        ),
                    )
                finally:
                    close = getattr(client, "close", None)
                    if callable(close):
                        await close()
            finally:
                await httpx_client.aclose()
        except Exception as exc:  # noqa: BLE001 - the gate must return a decision
            _LOGGER.exception("MIE approval call failed")
            return ApprovalDecision(
                approved=False, reason=f"MIE approval call failed: {exc}"
            )
        decision = _decision_from_events(events)
        if decision is None:
            return ApprovalDecision(
                approved=False, reason="MIE returned no approval decision"
            )
        return decision


_TERMINAL_STATES = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_REJECTED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
}


async def _send_events(
    client: object,
    request: SendMessageRequest,
    call_context: ClientCallContext,
) -> list[dict[str, object]]:
    """Send one message and collect normalized events up to the first stop point.

    This local helper keeps :mod:`protocol.mie_client` free of the client-flow module, which
    imports the ADK decision stack; the RAN server must remain importable without Google ADK.
    """
    events: list[dict[str, object]] = []
    send_message = getattr(client, "send_message")
    async for stream_response in send_message(request, context=call_context):
        event = normalize_event(stream_response)
        events.append(event)
        if event.get("kind") == "message":
            break
        if event.get("kind") == "status" and event.get("state") in _TERMINAL_STATES:
            break
    return events


def _approval_request(
    plan: Mapping[str, object], *, run_id: str | None
) -> SendMessageRequest:
    """Build the plain A2A approval request of the RAN->MIE hop."""
    request = SendMessageRequest()
    request.message.message_id = str(uuid.uuid4())
    request.message.role = Role.ROLE_USER
    request.message.parts.add().text = (
        "Review this RAN energy-saving plan and approve or reject it: "
        f"{plan.get('cellId')}, {plan.get('window')}, {plan.get('rate')}."
    )
    request.message.metadata[PLAN_METADATA_KEY] = dict(plan)
    if run_id:
        request.message.metadata["sampleRunId"] = run_id
    return request


def _decision_from_events(events: list[dict[str, object]]) -> ApprovalDecision | None:
    """Find the MIE decision in the received artifacts/status metadata."""
    for event in reversed(events):
        if event.get("kind") == "artifact" and event.get("name") == MIE_DECISION_ARTIFACT_NAME:
            data = event.get("data")
            if isinstance(data, Mapping) and isinstance(data.get("approved"), bool):
                reason = data.get("reason")
                return ApprovalDecision(
                    approved=bool(data["approved"]),
                    reason=reason if isinstance(reason, str) else None,
                )
        if event.get("kind") != "status":
            continue
        metadata = event.get("metadata")
        if isinstance(metadata, Mapping) and isinstance(metadata.get("approved"), bool):
            reason = metadata.get("reason")
            return ApprovalDecision(
                approved=bool(metadata["approved"]),
                reason=reason if isinstance(reason, str) else None,
            )
    return None


__all__ = ["ApprovalDecision", "MIEApprovalClient", "PLAN_METADATA_KEY"]
