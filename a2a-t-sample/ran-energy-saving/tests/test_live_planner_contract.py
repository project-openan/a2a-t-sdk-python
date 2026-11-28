"""Contract test of the live planner against an OpenAI-compatible endpoint.

The offline planner is a scripted model; the demo's live path goes through ``ChatOpenAI``. This
test stands up a tiny fake ``/v1/chat/completions`` server and asserts what the live planner
actually puts on the wire, in particular the Modify continuation block. It needs no model
credentials and no network access.
"""

from __future__ import annotations

import dataclasses
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from demo.agents.server_agent.langgraph_tools import PlanGuard, build_execution_tools
from demo.agents.server_agent.planner import (
    build_planner_input,
    build_planner_model,
    build_system_prompt,
)
from langchain_core.messages import HumanMessage, SystemMessage
from shared.config import MODEL_MODE_LIVE, SampleConfig

#: Baseline plan the Modify continuation is expected to carry.
PLAN: dict[str, str] = {
    "plan_id": "plan-cell-002-001",
    "cell_id": "cell-002",
    "window": "01:00~05:00",
    "target_rate": "10",
}


class _FakeOpenAIServer(ThreadingHTTPServer):
    """HTTP server that records chat-completion requests and returns one tool call."""

    requests: list[dict[str, Any]]


class _ChatCompletionsHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible chat-completions handler."""

    server: _FakeOpenAIServer

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or "0")
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("authorization", ""),
                "body": body,
            }
        )
        payload = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": str(body.get("model") or "test-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "get_cell_energy_profile",
                                    "arguments": json.dumps({"cell_id": "cell-002", "date": "2026-11-03"}),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        """Keep the test output free of per-request access logs."""


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[_FakeOpenAIServer, str]]:
    """Serve a fake OpenAI-compatible endpoint and return its base URL."""
    server = _FakeOpenAIServer(("127.0.0.1", 0), _ChatCompletionsHandler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # A local proxy would otherwise intercept the loopback call on some developer machines.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    try:
        host, port = server.server_address[:2]
        yield server, f"http://{host}:{port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _live_config(config: SampleConfig, base_url: str) -> SampleConfig:
    """Return the offline fixture pointed at the fake live endpoint."""
    return dataclasses.replace(
        config,
        model_mode=MODEL_MODE_LIVE,
        planner_model="test-model",
        planner_api_base=base_url,
        planner_api_key="test-key",
    )


def test_live_planner_sends_the_modify_contract(
    fake_openai: tuple[_FakeOpenAIServer, str],
    offline_config: SampleConfig,
) -> None:
    server, base_url = fake_openai
    model = build_planner_model(
        _live_config(offline_config, base_url),
        operation="Modify",
        existing_plan_id=PLAN["plan_id"],
    )
    planner_input = build_planner_input(
        "Accepted Task-T: adjust the planned outage window.",
        None,
        operation="Modify",
        existing_plan=PLAN,
    )

    reply = model.bind_tools(build_execution_tools(PlanGuard())).invoke(
        [
            SystemMessage(content=build_system_prompt()),
            HumanMessage(content=planner_input),
        ]
    )

    assert len(server.requests) == 1
    request = server.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == "Bearer test-key"

    body = request["body"]
    assert body["model"] == "test-model"
    user_content = body["messages"][-1]["content"]
    assert "Operation: Modify" in user_content
    assert "do not allocate a second plan" in user_content
    assert PLAN["plan_id"] in user_content
    assert PLAN["window"] in user_content

    tool_names = {item["function"]["name"] for item in body["tools"]}
    assert {
        "get_cell_energy_profile",
        "evaluate_energy_saving_plan",
        "modify_energy_saving_plan",
        "generate_energy_saving_report",
    } <= tool_names

    assert reply.tool_calls[0]["name"] == "get_cell_energy_profile"
    assert reply.tool_calls[0]["args"] == {"cell_id": "cell-002", "date": "2026-11-03"}


def test_live_planner_create_prompt_has_no_modify_block(
    fake_openai: tuple[_FakeOpenAIServer, str],
    offline_config: SampleConfig,
) -> None:
    server, base_url = fake_openai
    model = build_planner_model(_live_config(offline_config, base_url), operation="Create")
    planner_input = build_planner_input("Accepted Task-T: start energy saving.", None, operation="Create")

    model.invoke([HumanMessage(content=planner_input)])

    user_content = server.requests[0]["body"]["messages"][-1]["content"]
    assert "Operation: Modify" not in user_content
    assert "Existing plan to update" not in user_content
