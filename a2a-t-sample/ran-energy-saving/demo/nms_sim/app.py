"""Starlette application of the NMS simulator."""

from __future__ import annotations

import httpx
from starlette.applications import Starlette

from demo.nms_sim.a2a import SimRuntime, build_routes, build_runtime
from demo.nms_sim.config import SimConfig


def build_sim_app(
    config: SimConfig | None = None,
    *,
    push_transport: httpx.AsyncBaseTransport | None = None,
) -> Starlette:
    """Build the simulator app with the token, A2A and task routes.

    Args:
        config: simulator settings; read from the environment when omitted.
        push_transport: optional httpx transport used by webhook pushes (tests inject a mock).
    """
    resolved = config or SimConfig.from_env()
    runtime = build_runtime(resolved, push_transport=push_transport)
    routes, _ = build_routes(resolved, runtime=runtime)
    app = Starlette(routes=routes)
    app.state.sim = runtime
    return app


__all__ = ["SimRuntime", "build_sim_app"]
