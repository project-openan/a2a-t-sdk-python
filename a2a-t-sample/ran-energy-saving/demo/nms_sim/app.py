"""Starlette application of the NMS simulator."""

from __future__ import annotations

from starlette.applications import Starlette

from demo.nms_sim.a2a import build_routes
from demo.nms_sim.config import SimConfig


def build_sim_app(config: SimConfig | None = None) -> Starlette:
    """Build the simulator app with the token, A2A and task routes."""
    resolved = config or SimConfig.from_env()
    routes, _, _ = build_routes(resolved)
    return Starlette(routes=routes)
