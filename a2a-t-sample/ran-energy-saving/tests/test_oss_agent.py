"""End-to-end test of the pure A2A three-hop OSS->RAN->MIE chain."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace

import httpx
import uvicorn
from a2a_t.core.metadata import TASK_T_EXTENSION_URI
from demo.composition import build_demo_runtime
from demo.pure_policy import build_demo_pure_policy, pure_answer_from_event
from protocol.intent import ProcessedIntent
from protocol.mie_agent import build_mie_app
from protocol.oss_agent import a2a_request_text, build_oss_app
from shared.config import DISCOVERY_DIRECT, TRANSPORT_SSE
from shared.run_constants import FINAL_STATE_TASK_COMPLETED, LANGUAGE_EN, PROTOCOL_PURE
from tests.integration_helpers import (
    free_tcp_port,
    offline_direct_config,
    running_app_on_port,
    running_server,
)


def test_a2a_request_text_prefers_the_plain_request() -> None:
    """The OSS->RAN hop is standard A2A: it must not forward the Task-T section layout."""
    intent = ProcessedIntent(
        raw_request="Start energy saving in Region A.",
        normalized_text="## Operation Type\nCreate\n\n## Task Object\nThe area is Region A.",
        plain_text="Create a RAN energy saving task in Region A.",
        operation_type="Create",
        region="Region A",
    )

    assert a2a_request_text(intent) == "Create a RAN energy saving task in Region A."
    assert "##" not in a2a_request_text(intent)


def test_a2a_request_text_falls_back_to_the_raw_request() -> None:
    """An old or passthrough intent has no plain renderer output; the raw text still works."""
    intent = ProcessedIntent(
        raw_request="Start energy saving in Region A.",
        normalized_text="## Operation Type\nCreate",
    )

    assert a2a_request_text(intent) == "Start energy saving in Region A."


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
                decision = await client.post(f"{mie_url}/approvals/{task_id}", json={"approved": True})
                decision.raise_for_status()
                return task_id
    raise AssertionError("MIE received no approval request")


async def test_the_three_hop_chain_completes_and_tags_every_hop() -> None:
    mie_port = free_tcp_port()
    ran_port = free_tcp_port()
    oss_port = free_tcp_port()
    config = offline_direct_config(ran_port)

    with _running_mie(mie_port, timeout_seconds=10.0) as mie_url:
        with running_server(
            config,
            protocol_mode=PROTOCOL_PURE,
            pure_policy=build_demo_pure_policy(LANGUAGE_EN),
            mie_url=mie_url,
            approval_gate=True,
            approval_timeout_seconds=10.0,
        ):
            oss_app = build_oss_app(
                host="127.0.0.1",
                port=oss_port,
                ran_url=f"http://127.0.0.1:{ran_port}",
                answer=lambda event: pure_answer_from_event(event, LANGUAGE_EN),
            )
            with running_app_on_port(oss_app, oss_port, label="oss"):
                approver = asyncio.create_task(_approve_next_request(mie_url))
                client_config = replace(config, port=oss_port)
                runtime = await build_demo_runtime(
                    client_config,
                    "information",
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

    hops = {event["metadata"].get("hop") for event in result.events if isinstance(event.get("metadata"), dict)}
    assert "oss" in hops
    assert "ran" in hops or "ran->mie" in hops

    assert any(
        isinstance(event.get("metadata"), dict)
        and isinstance(event["metadata"].get("pure"), dict)
        and event["metadata"]["pure"].get("kind") == "approval"
        for event in result.events
    )

    for event in result.events:
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            continue
        assert TASK_T_EXTENSION_URI not in metadata
        assert "templateUri" not in metadata
        assert "negotiationContext" not in metadata


@contextmanager
def _running_mie(port: int, timeout_seconds: float) -> Generator[str, None, None]:
    """Serve one MIE app on a known port (the AgentCard must advertise it)."""
    app = build_mie_app(host="127.0.0.1", port=port, timeout_seconds=timeout_seconds)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
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
