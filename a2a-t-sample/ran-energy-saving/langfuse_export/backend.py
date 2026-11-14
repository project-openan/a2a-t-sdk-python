"""Backend interface and version detection."""

from __future__ import annotations

import re
from typing import Any

from .client import ApiError, AuthError, ExportError, LangfuseClient, NetworkError, NotFoundError


class Backend:
    kind = "v3"
    api_version = "v3"

    def __init__(self, client: LangfuseClient) -> None:
        self.client = client
        self.warnings: list[str] = []

    def fetch_observations(
        self,
        trace_id: str,
        from_time: str | None = None,
        to_time: str | None = None,
        expand_metadata: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def fetch_scores(self, trace_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def fetch_project(self) -> tuple[str | None, str | None]:
        return None, None

    def fetch_media(self, media_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def fetch_session_observations(self, session_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def fetch_legacy_trace(self, trace_id: str) -> dict[str, Any] | None:
        return None


def _major(version: str) -> int | None:
    match = re.search(r"(\d+)\.(\d+)", version or "")
    return int(match.group(1)) if match else None


def _best_effort_version(client: LangfuseClient) -> str:
    """Read the health version without ever making backend selection fatal."""
    try:
        health = client.get_json("/api/public/health")
    except ExportError:
        return ""
    try:
        return str(health.get("version") or "")
    except AttributeError:  # defensive: unexpected health payload shape
        return ""


def select_backend(client: LangfuseClient, forced: str = "auto") -> Backend:
    from .backend_v3 import V3Backend
    from .backend_v4 import V4Backend

    if forced == "v4":
        return V4Backend(client)
    if forced == "v3":
        # Keep the detected minor version so 3.179+ instances use /v3/scores instead
        # of silently falling back to the legacy page-based endpoint. Any failure is
        # swallowed: forcing v3 must still work against instances with no /health.
        return V3Backend(client, version=_best_effort_version(client))

    version = ""
    try:
        health = client.get_json("/api/public/health")
        version = str(health.get("version") or "")
    except NetworkError:
        raise
    except ExportError:
        version = ""

    major = _major(version)
    if major is not None:
        return V4Backend(client, version=version) if major >= 4 else V3Backend(client, version=version)

    # Health unavailable: probe the v2 endpoint. A 404/405 means "v3 instance"; anything else
    # (200, 401, 403, 400) means the endpoint is routed, so prefer v4.
    try:
        client.get_json("/api/public/v2/observations", {"limit": 1})
        return V4Backend(client, version="")
    except NotFoundError:
        return V3Backend(client, version="")
    except AuthError:
        return V4Backend(client, version="")
    except ApiError as exc:
        return V3Backend(client, version="") if exc.status == 405 else V4Backend(client, version="")
    except ExportError:
        return V4Backend(client, version="")
