"""Unit tests of the a2a-sdk HTTP wire capture."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from protocol.wire_log import HttpWireLog


def _handler(request: httpx.Request) -> httpx.Response:
    """Answer one test request without a network."""
    del request
    return httpx.Response(200, json={"ok": True})


async def test_http_hook_captures_the_raw_send_message_request(tmp_path: Path) -> None:
    """The log stores the body the SDK sent (the ``message`` envelope), redacted."""
    wire_log = HttpWireLog()
    wire_log.set_run(tmp_path, "run-1")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_handler),
        event_hooks={"request": [wire_log.record_request]},
        headers={"Authorization": "Bearer secret-token"},
    ) as client:
        await client.post(
            "http://agent.test/a2a/json/message:send",
            json={
                "message": {
                    "messageId": "msg-1",
                    "role": "ROLE_USER",
                    "parts": [{"text": "create ran energy saving task"}],
                }
            },
        )

    lines = (tmp_path / "run-1" / "wire.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["dir"] == "out"
    assert record["method"] == "POST"
    assert record["url"].endswith("/a2a/json/message:send")
    # The real envelope, not the inner Message object.
    assert record["body"]["message"]["messageId"] == "msg-1"
    assert record["body"]["message"]["parts"][0]["text"] == "create ran energy saving task"
    assert record["headers"]["authorization"] == "***"
    assert "secret-token" not in json.dumps(record)


async def test_http_hook_is_a_noop_before_a_run_is_set(tmp_path: Path) -> None:
    """A client outside a run writes nothing."""
    wire_log = HttpWireLog()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_handler),
        event_hooks={"request": [wire_log.record_request]},
    ) as client:
        await client.post("http://agent.test/a2a/json/message:send", json={"message": {}})

    assert not (tmp_path / "wire.jsonl").exists()
    assert wire_log.path is None


def test_set_run_points_the_log_at_the_run_directory(tmp_path: Path) -> None:
    """The path follows the Run Explorer id and clears when the id is empty."""
    wire_log = HttpWireLog()

    wire_log.set_run(tmp_path, "run-2")
    assert wire_log.path == tmp_path / "run-2" / "wire.jsonl"

    wire_log.set_run(tmp_path, None)
    assert wire_log.path is None
