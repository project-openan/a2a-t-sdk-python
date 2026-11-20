"""Tests of the bind policy, bearer authentication and health endpoints."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from shared.config import SampleConfig
from shared.http_security import (
    UI_TOKEN_COOKIE,
    BearerAuthMiddleware,
    install_health_routes,
    is_loopback,
    sqlite_ready,
    validate_bind_security,
)
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route


def _app(
    *,
    token: str = "",
    ready: bool = True,
    cookie_name: str | None = None,
) -> Starlette:
    async def api(request: object) -> JSONResponse:
        del request
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[Route("/api/value", api)],
        middleware=(
            [
                Middleware(
                    BearerAuthMiddleware,
                    token=token,
                    protected_prefixes=("/api/",),
                    cookie_name=cookie_name,
                )
            ]
            if token
            else []
        ),
    )
    install_health_routes(
        app,
        service="test-service",
        checks={"dependency": lambda: ready},
    )
    return app


def test_insecure_bind_needs_an_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="Refusing to bind"):
        validate_bind_security("0.0.0.0", "")

    validate_bind_security("0.0.0.0", "", allow_insecure=True)


async def test_a_same_origin_cookie_carries_the_bearer_token() -> None:
    app = _app(token="secret", cookie_name=UI_TOKEN_COOKIE)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        accepted = await client.get(
            "/api/value", headers={"cookie": f"{UI_TOKEN_COOKIE}=secret"}
        )
        rejected = await client.get(
            "/api/value", headers={"cookie": f"{UI_TOKEN_COOKIE}=wrong"}
        )

    assert accepted.status_code == 200
    assert rejected.status_code == 401


def test_loopback_detection_and_bind_policy() -> None:
    assert is_loopback("127.0.0.1")
    assert is_loopback("localhost")
    assert not is_loopback("0.0.0.0")

    validate_bind_security("127.0.0.1", "")
    validate_bind_security("0.0.0.0", "secret")
    with pytest.raises(ValueError, match="A2AT_SAMPLE_AUTH_TOKEN"):
        validate_bind_security("0.0.0.0", "")


async def test_protected_routes_require_the_bearer_token() -> None:
    app = _app(token="secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.get("/api/value")
        wrong = await client.get("/api/value", headers={"Authorization": "Bearer nope"})
        authorized = await client.get("/api/value", headers={"Authorization": "Bearer secret"})
        health = await client.get("/health")

    assert unauthorized.status_code == 401
    assert unauthorized.json()["code"] == "unauthorized"
    assert wrong.status_code == 401
    assert authorized.status_code == 200
    assert health.status_code == 200


async def test_public_path_patterns_bypass_the_bearer_token() -> None:
    async def endpoint(request: object) -> JSONResponse:
        del request
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[
            Route("/api/runs/{run_id}/trace", endpoint, methods=["POST"]),
            Route("/api/other", endpoint),
        ],
        middleware=[
            Middleware(
                BearerAuthMiddleware,
                token="secret",
                protected_prefixes=("/api/",),
                public_path_patterns=(re.compile(r"/api/runs/[^/]+/trace"),),
            )
        ],
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        trace = await client.post("/api/runs/run-1/trace", json={})
        other = await client.get("/api/other")

    assert trace.status_code == 200
    assert other.status_code == 401


async def test_health_and_readiness_reflect_the_dependency() -> None:
    app = _app(ready=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        ready = await client.get("/ready")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "test-service"}
    assert ready.status_code == 503
    assert ready.json()["checks"] == {"dependency": False}


def test_sqlite_ready_checks_the_configured_database(tmp_path: Path) -> None:
    assert sqlite_ready(None) is True
    assert sqlite_ready(str(tmp_path / "missing.sqlite3")) is False

    from shared.sqlite import connect

    database = tmp_path / "store.sqlite3"
    with connect(database) as connection:
        connection.execute("CREATE TABLE items (id TEXT)")

    assert sqlite_ready(str(database)) is True


async def test_web_app_rejects_api_requests_without_the_token(
    offline_config: SampleConfig,
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from demo.web.app import create_app

    config = replace(offline_config, auth_token="secret")
    app = create_app(config, runs_dir=tmp_path, finished_grace_seconds=0.0, bind_host="0.0.0.0")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.get("/api/meta")
        authorized = await client.get("/api/meta", headers={"Authorization": "Bearer secret"})
        health = await client.get("/health")

    assert (unauthorized.status_code, authorized.status_code, health.status_code) == (401, 200, 200)
