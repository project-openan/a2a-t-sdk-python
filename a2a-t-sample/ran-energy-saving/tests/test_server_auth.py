"""Tests of the client-to-server bearer token and the secured AgentCard."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import httpx
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from protocol.client_runtime import _server_auth_headers
from protocol.server_app import build_agent_card, build_rest_app
from shared.config import SampleConfig


class _NoopExecutor(AgentExecutor):
    """Executor double that satisfies the request handler contract."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        del context, event_queue

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        del context, event_queue


def _app(token: str) -> Any:
    card = build_agent_card(host="127.0.0.1", port=8000, secured=bool(token))
    return build_rest_app(
        agent_card=card,
        executor=cast(Any, _NoopExecutor()),
        auth_token=token,
    )


async def test_a2a_routes_require_the_server_bearer() -> None:
    """Discovery and health stay public; every message/task route needs the token."""
    app = _app("server-secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        card = await client.get("/.well-known/agent-card.json")
        health = await client.get("/health")
        metrics = await client.get("/metrics")
        anonymous = await client.post("/message:send", json={})
        tenant = await client.post("/tenant-a/message:send", json={})
        authorized = await client.post(
            "/message:send",
            json={},
            headers={"Authorization": "Bearer server-secret"},
        )

    assert card.status_code == 200
    assert health.status_code == 200
    assert metrics.status_code == 401
    assert anonymous.status_code == 401
    assert tenant.status_code == 401
    assert authorized.status_code != 401


def test_the_card_declares_bearer_only_when_secured() -> None:
    secured = build_agent_card(host="127.0.0.1", port=8000, secured=True)
    open_card = build_agent_card(host="127.0.0.1", port=8000)

    assert "bearer" in secured.security_schemes
    assert secured.security_requirements
    assert not open_card.security_schemes
    assert not open_card.security_requirements


def test_the_client_header_follows_the_configured_token(
    offline_config: SampleConfig,
) -> None:
    assert _server_auth_headers(offline_config) == {}

    secured = replace(offline_config, server_auth_token="server-secret")
    assert _server_auth_headers(secured) == {"Authorization": "Bearer server-secret"}
