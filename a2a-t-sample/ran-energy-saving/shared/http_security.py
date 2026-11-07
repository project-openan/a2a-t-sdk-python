"""Bind-address policy, bearer authentication and readiness checks for the HTTP surfaces.

The sample is loopback-first: a process may bind a non-loopback address only when a bearer token is
configured, and the token then guards the API routes of the Web UI and the inbound gateway. The A2A
server uses a separate service token (`A2AT_SAMPLE_SERVER_AUTH_TOKEN`) that the client presents;
when it is unset the server stays open, which is only appropriate behind a trusted deployment
gateway (see the production checklist).
"""

from __future__ import annotations

import hmac
import re
import sqlite3
from collections.abc import Callable, Mapping
from typing import Any, Final
from urllib.parse import unquote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from shared.metrics import render_prometheus
from shared.sqlite import readonly_connection

#: Hosts treated as local (no token required to bind them).
LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset(
    {"127.0.0.1", "::1", "localhost", "0:0:0:0:0:0:0:1"}
)

#: Routes that stay public even when a token is configured.
PUBLIC_PATHS: Final[frozenset[str]] = frozenset({"/health", "/ready", "/"})

#: Cookie the presentation page sets from ``?token=…`` so same-origin fetches and SSE carry it.
UI_TOKEN_COOKIE: Final[str] = "a2at_ui_token"


def is_loopback(host: str) -> bool:
    """Return whether ``host`` is a loopback bind address."""
    return host.strip().lower() in LOOPBACK_HOSTS


def validate_bind_security(
    host: str,
    token: str,
    *,
    token_env_key: str = "A2AT_SAMPLE_AUTH_TOKEN",
    allow_insecure: bool = False,
) -> None:
    """Reject a non-loopback bind without a bearer token.

    Args:
        host: bind address.
        token: configured bearer token (empty when unset).
        token_env_key: environment key named in the error.
        allow_insecure: explicit opt-in for a trusted-LAN demo without a token. The Web UI passes
            this only when the launcher's ``--allow-insecure-bind`` flag is set.

    Raises:
        ValueError: when ``host`` is not loopback, ``token`` is empty and ``allow_insecure`` is
            false.
    """
    if allow_insecure or is_loopback(host) or token.strip():
        return
    raise ValueError(
        f"Refusing to bind {host!r} without {token_env_key}. "
        "Bind a loopback address for a local demo, pass --allow-insecure-bind for a trusted "
        "LAN demo, or configure a bearer token."
    )


class BearerAuthMiddleware:
    """Require ``Authorization: Bearer <token>`` on the protected route prefixes.

    Health, readiness and the static page stay public so a container probe and a browser can reach
    them; the API routes they call are protected.
    """

    def __init__(
        self,
        app: Any,
        *,
        token: str,
        protected_prefixes: tuple[str, ...] = ("/api/",),
        public_paths: frozenset[str] | None = None,
        public_path_patterns: tuple[re.Pattern[str], ...] = (),
        cookie_name: str | None = None,
    ) -> None:
        self._app = app
        self._token = token.strip()
        self._protected_prefixes = protected_prefixes
        self._public_paths = PUBLIC_PATHS if public_paths is None else public_paths
        self._public_path_patterns = public_path_patterns
        self._cookie_name = cookie_name

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        """Authenticate one HTTP request before delegating to the wrapped app."""
        if scope.get("type") != "http" or not self._token:
            await self._app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        if self._is_public(path) or not self._is_protected(path):
            await self._app(scope, receive, send)
            return
        headers = scope.get("headers", ())
        supplied = _bearer_token(headers)
        if not supplied and self._cookie_name:
            supplied = _cookie_token(headers, self._cookie_name)
        if supplied and hmac.compare_digest(supplied, self._token):
            await self._app(scope, receive, send)
            return
        response = JSONResponse(
            {"error": "A bearer token is required.", "code": "unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)

    def _is_public(self, path: str) -> bool:
        """Return whether ``path`` is exempt from the bearer token."""
        return path in self._public_paths or any(
            pattern.fullmatch(path) for pattern in self._public_path_patterns
        )

    def _is_protected(self, path: str) -> bool:
        """Return whether ``path`` belongs to a protected prefix."""
        return any(path.startswith(prefix) for prefix in self._protected_prefixes)


def _bearer_token(headers: tuple[tuple[bytes, bytes], ...]) -> str:
    """Extract the bearer token of one ASGI header list."""
    for name, value in headers:
        if name.lower() != b"authorization":
            continue
        decoded = value.decode("latin-1").strip()
        scheme, _, token = decoded.partition(" ")
        if scheme.lower() == "bearer":
            return token.strip()
    return ""


def _cookie_token(headers: tuple[tuple[bytes, bytes], ...], name: str) -> str:
    """Extract one URL-decoded cookie value, or an empty string."""
    for key, value in headers:
        if key.lower() != b"cookie":
            continue
        for part in value.decode("latin-1").split(";"):
            cookie_name, _, cookie_value = part.strip().partition("=")
            if cookie_name == name and cookie_value:
                return unquote(cookie_value)
    return ""


def sqlite_ready(path: str | None) -> bool:
    """Return whether the configured SQLite database can be opened and queried."""
    if not path:
        return True
    connection = readonly_connection(path)
    if connection is None:
        return False
    try:
        connection.execute("SELECT 1").fetchone()
    except sqlite3.Error:
        return False
    finally:
        connection.close()
    return True


def install_health_routes(
    app: Starlette,
    *,
    service: str,
    checks: Mapping[str, Callable[[], bool]],
) -> None:
    """Register ``/health`` and ``/ready`` routes and their readiness checks on a Starlette app."""
    from starlette.routing import Route

    app.state.service_name = service
    app.state.readiness_checks = dict(checks)
    app.router.routes.insert(0, Route("/health", health))
    app.router.routes.insert(1, Route("/ready", ready))


def install_metrics_route(app: Starlette) -> None:
    """Register ``GET /metrics`` with the Prometheus text exposition of this process."""
    from starlette.routing import Route

    async def metrics(request: Request) -> Response:
        del request
        return Response(
            render_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    app.router.routes.insert(0, Route("/metrics", metrics))


async def health(request: Request) -> JSONResponse:
    """Liveness: the process is serving requests."""
    return JSONResponse({"status": "ok", "service": request.app.state.service_name})


async def ready(request: Request) -> JSONResponse:
    """Readiness: every configured dependency is reachable."""
    checks: Mapping[str, Callable[[], bool]] = request.app.state.readiness_checks
    results = {name: bool(check()) for name, check in checks.items()}
    ready_now = all(results.values())
    return JSONResponse(
        {"status": "ready" if ready_now else "not_ready", "checks": results},
        status_code=200 if ready_now else 503,
    )
