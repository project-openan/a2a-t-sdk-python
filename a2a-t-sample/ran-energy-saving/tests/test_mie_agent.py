"""Tests of the MIE Agent approval hop and its presentation bridge."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager

import httpx
import uvicorn
from demo.composition import build_demo_runtime
from demo.pure_policy import build_demo_pure_policy
from protocol.messages import format_rate_percent
from protocol.mie_agent import MIEApprovalRegistry, build_mie_app
from shared.config import DISCOVERY_DIRECT, TRANSPORT_SSE
from shared.run_constants import FINAL_STATE_TASK_COMPLETED, LANGUAGE_EN, PROTOCOL_PURE
from tests.integration_helpers import (
    free_tcp_port,
    offline_direct_config,
    running_server,
)


async def test_the_registry_broadcasts_and_resolves_one_approval() -> None:
    registry = MIEApprovalRegistry()
    queue = registry.subscribe()
    task = asyncio.create_task(
        registry.request("task-1", {"cellId": "cell-001"}, 1.0)
    )

    requested = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert requested["type"] == "approval_requested"
    assert requested["taskId"] == "task-1"
    assert requested["plan"] == {"cellId": "cell-001"}

    assert registry.decide("task-1", True) is True
    approved, reason = await asyncio.wait_for(task, timeout=1.0)
    assert approved is True
    assert reason is None

    resolved = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert resolved == {
        "type": "approval_resolved",
        "taskId": "task-1",
        "approved": True,
        "reason": None,
    }


async def test_the_registry_times_out_to_a_rejection() -> None:
    registry = MIEApprovalRegistry()

    approved, reason = await registry.request("task-2", {}, 0.01)

    assert approved is False
    assert reason == "timeout"


@contextmanager
def _running_mie(port: int, timeout_seconds: float) -> Generator[str, None, None]:
    """Serve one MIE app on a known port (the AgentCard must advertise it)."""
    app = build_mie_app(
        host="127.0.0.1", port=port, timeout_seconds=timeout_seconds
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started:
        if not thread.is_alive() or time.monotonic() >= deadline:
            raise RuntimeError("MIE server did not start")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


async def _approve_next_request(mie_url: str) -> str:
    """Listen to the MIE SSE stream and approve the first pending plan."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        async with client.stream("GET", f"{mie_url}/approvals/events") as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                if event.get("type") != "approval_requested":
                    continue
                task_id = str(event["taskId"])
                decision = await client.post(
                    f"{mie_url}/approvals/{task_id}", json={"approved": True}
                )
                decision.raise_for_status()
                return task_id
    raise AssertionError("MIE received no approval request")


async def test_a_pure_run_waits_for_and_accepts_the_mie_decision() -> None:
    mie_port = free_tcp_port()
    server_port = free_tcp_port()
    config = offline_direct_config(server_port)

    with _running_mie(mie_port, timeout_seconds=10.0) as mie_url:
        approver = asyncio.create_task(_approve_next_request(mie_url))
        with running_server(
            config,
            protocol_mode=PROTOCOL_PURE,
            pure_policy=build_demo_pure_policy(LANGUAGE_EN),
            mie_url=mie_url,
            approval_gate=True,
            approval_timeout_seconds=10.0,
        ):
            runtime = await build_demo_runtime(
                config,
                "night",
                "auto",
                TRANSPORT_SSE,
                DISCOVERY_DIRECT,
                protocol=PROTOCOL_PURE,
            )
            try:
                result = await runtime.run()
            finally:
                await runtime.aclose()
        approval_task = await asyncio.wait_for(approver, timeout=15.0)

    assert approval_task
    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    approval_states = [
        event
        for event in result.events
        if isinstance(event.get("metadata"), dict)
        and isinstance(event["metadata"].get("pure"), dict)
        and event["metadata"]["pure"].get("kind") == "approval"
    ]
    assert approval_states


def test_the_rate_format_does_not_double_the_percent_sign() -> None:
    # The live planner may return 35 or 35%; both must render as 35% on the MIE card.
    assert format_rate_percent("35") == "35%"
    assert format_rate_percent("35%") == "35%"
    assert format_rate_percent(30) == "30%"
