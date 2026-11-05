"""HTTP client for registering and querying AgentCards via the registry center."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Protocol

import httpx
from dotenv import dotenv_values

_LOGGER = logging.getLogger("ran_energy_saving.registry_client")


class _ResponseLike(Protocol):
    """Read-only response surface shared by ``httpx.Response`` and the test doubles."""

    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...

    def json(self) -> Any: ...


class _HttpClientLike(Protocol):
    async def post(self, url: str, json: dict[str, Any]) -> _ResponseLike: ...
    async def get(self, url: str) -> _ResponseLike: ...
    async def aclose(self) -> None: ...


def resolve_registry_center_url(*, env_path: Path | None = None) -> str:
    resolved_env_path = env_path or Path.cwd() / ".env"
    env_values = dotenv_values(resolved_env_path) if resolved_env_path.exists() else {}
    host = str(
        os.environ.get("REGISTRY_CENTER_HOST")
        or env_values.get("REGISTRY_CENTER_HOST")
        or "127.0.0.1"
    )
    port = int(
        os.environ.get("REGISTRY_CENTER_PORT")
        or env_values.get("REGISTRY_CENTER_PORT")
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
            _LOGGER.info("registry registration succeeded: %s", normalized_url)
            return {"status": "success", "message": "Agent card registered successfully"}
        _LOGGER.warning("registry registration failed: status=%s", response.status_code)
        return {
            "status": "failed",
            "message": f"Unexpected status code: {response.status_code}",
            "error": _safe_error_body(response),
        }
    except Exception as exc:
        _LOGGER.warning("registry registration failed: %s", exc)
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
            _LOGGER.debug("registry lookup: found %d card(s)", len(data.get("agentCards", [])))
            return data
        if response.status_code == 404:
            _LOGGER.info("registry lookup: no card for %s/%s", organization, name)
            return {"agentCards": []}
        _LOGGER.warning("registry lookup failed: status=%s", response.status_code)
        return {"agentCards": []}
    except Exception as exc:
        _LOGGER.warning("registry lookup failed: %s", exc)
        return {"agentCards": []}
    finally:
        if owns_client:
            await client.aclose()
