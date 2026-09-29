"""HTTP client for registering and querying AgentCards via the registry center."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import httpx
from dotenv import dotenv_values


class _ResponseLike(Protocol):
    status_code: int
    text: str

    def json(self) -> Any: ...


class _HttpClientLike(Protocol):
    async def post(self, url: str, json: dict[str, Any]) -> _ResponseLike: ...
    async def get(self, url: str) -> _ResponseLike: ...
    async def aclose(self) -> None: ...


def resolve_registry_center_url(*, env_path: Path | None = None) -> str:
    resolved_env_path = env_path or Path.cwd() / ".env"
    env_values = dotenv_values(resolved_env_path) if resolved_env_path.exists() else {}
    host = str(
        env_values.get("REGISTRY_CENTER_HOST")
        or os.environ.get("REGISTRY_CENTER_HOST")
        or "127.0.0.1"
    )
    port = int(
        env_values.get("REGISTRY_CENTER_PORT")
        or os.environ.get("REGISTRY_CENTER_PORT")
        or "5001"
    )
    return f"http://{host}:{port}"


def _safe_error_body(response: _ResponseLike) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text or None


async def register_agentcard(
    *,
    registration_payload: dict[str, Any],
    env_path: Path | None = None,
    registry_center_url: str | None = None,
    timeout_seconds: float = 30.0,
    http_client: _HttpClientLike | None = None,
    log_sink: Callable[[str], object] | None = None,
) -> dict[str, Any]:
    """POST an AgentCard registration payload to the registry center; returns success/failed status dict."""
    resolved_url = registry_center_url or resolve_registry_center_url(env_path=env_path)
    normalized_url = resolved_url.rstrip("/")
    uri = f"{normalized_url}/rest/v1/registry-center/agent-cards"
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=timeout_seconds, trust_env=False)

    try:
        response = await client.post(uri, json=registration_payload)
        if response.status_code == 201:
            if log_sink is not None:
                log_sink("[registry-client] register: success")
            return {"status": "success", "message": "Agent card registered successfully"}
        if log_sink is not None:
            log_sink(f"[registry-client] register failed: status_code={response.status_code}")
        return {
            "status": "failed",
            "message": f"Unexpected status code: {response.status_code}",
            "error": _safe_error_body(response),
        }
    except Exception as exc:
        if log_sink is not None:
            log_sink(f"[registry-client] register failed: {exc}")
        return {"status": "failed", "message": str(exc)}
    finally:
        if owns_client:
            await client.aclose()


async def query_by_name_org(
    *,
    organization: str,
    name: str,
    env_path: Path | None = None,
    registry_center_url: str | None = None,
    timeout_seconds: float = 30.0,
    http_client: _HttpClientLike | None = None,
    log_sink: Callable[[str], object] | None = None,
) -> dict[str, Any]:
    """GET an AgentCard from the registry center by organization and name; returns agentCards list."""
    resolved_url = registry_center_url or resolve_registry_center_url(env_path=env_path)
    normalized_url = resolved_url.rstrip("/")
    encoded_name = name.replace(" ", "%20")
    uri = f"{normalized_url}/rest/v1/registry-center/agent-cards/{organization}/{encoded_name}"
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=timeout_seconds, trust_env=False)

    try:
        response = await client.get(uri)
        if response.status_code == 200:
            data = response.json()
            if log_sink is not None:
                count = len(data.get("agentCards", []))
                log_sink(f"[registry-client] query-by-name: found {count} agent cards")
            return data
        if response.status_code == 404:
            if log_sink is not None:
                log_sink(f"[registry-client] query-by-name: not found (name={name}, org={organization})")
            return {"agentCards": []}
        if log_sink is not None:
            log_sink(f"[registry-client] query-by-name failed: status_code={response.status_code}")
        return {"agentCards": []}
    except Exception as exc:
        if log_sink is not None:
            log_sink(f"[registry-client] query-by-name failed: {exc}")
        return {"agentCards": []}
    finally:
        if owns_client:
            await client.aclose()
