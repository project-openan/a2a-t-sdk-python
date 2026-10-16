"""Offline tests of the webhook receiver and transport config plumbing of the ran-energy-saving demo.

Drives the embedded ``WebhookReceiver`` with real httpx requests against an OS-assigned ephemeral
port (in-process HTTP only, per the sample's no-cross-process-HTTP rule): the registered token
answers 200 and delivers the parsed StreamResponse-shaped dict to the injected callback, a wrong
or missing ``X-A2A-Notification-Token`` header answers 401 without delivery, and a malformed or
unrecognized JSON body answers 400 without delivery. The start/stop lifecycle is pinned (graceful
stop releases the port; stop is idempotent), a taken port surfaces as the documented ``RuntimeError``
(uvicorn's bind-failure ``SystemExit`` converted), and the transport resolvers
(``A2AT_SAMPLE_TRANSPORT`` / ``A2AT_SAMPLE_WEBHOOK_PORT`` / ``A2AT_SAMPLE_WEBHOOK_TOKEN``
/ ``A2AT_SAMPLE_WEBHOOK_IDLE_TIMEOUT``) follow the scenario/channel resolver tests: defaults,
overrides, and invalid-value fallbacks.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import time
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from client.scenario_data import (
    SUPPORTED_TRANSPORTS,
    TRANSPORT_ENV_KEY,
    TRANSPORT_SSE,
    TRANSPORT_WEBHOOK,
    WEBHOOK_IDLE_TIMEOUT_ENV_KEY,
    WEBHOOK_PORT_ENV_KEY,
    WEBHOOK_TOKEN_ENV_KEY,
    resolve_transport,
    resolve_webhook_idle_timeout,
    resolve_webhook_port,
    resolve_webhook_token,
)
from common.webhook_receiver import DEFAULT_WEBHOOK_HOST, DEFAULT_WEBHOOK_PORT, WebhookReceiver

#: StreamResponse-shaped webhook body of the tests: the ``statusUpdate`` top-level key of the
#: ``MessageToDict`` shape the SSE stream (and the push POST body) carries.
_STATUS_UPDATE_BODY: dict[str, object] = {
    "statusUpdate": {
        "taskId": "task-1",
        "contextId": "ctx-1",
        "status": {"state": "TASK_STATE_SUBMITTED"},
        "final": False,
    }
}

#: The registered token of the receiver under test (the ``A2AT_SAMPLE_WEBHOOK_TOKEN`` default).
_DEMO_TOKEN = "demo-webhook-token"


@pytest.fixture(autouse=True)
def _clean_webhook_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the developer's OS environment transport/webhook entries."""
    for key in (TRANSPORT_ENV_KEY, WEBHOOK_PORT_ENV_KEY, WEBHOOK_TOKEN_ENV_KEY, WEBHOOK_IDLE_TIMEOUT_ENV_KEY):
        monkeypatch.delenv(key, raising=False)


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    """Return whether a TCP connection to ``host:port`` succeeds (the run_demo port probe)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


async def _post_against_receiver(
    *,
    request_token: str | None,
    raw_body: bytes | None = None,
    json_body: object | None = None,
) -> tuple[httpx.Response, list[dict[str, object]], list[str]]:
    """Serve one request against a fresh ephemeral-port receiver; return (response, events, logs).

    The receiver lives only inside this call: started on the caller's event loop, stopped in the
    ``finally`` block, so every request test exercises the full lifecycle around the POST.
    """
    events: list[dict[str, object]] = []
    logs: list[str] = []
    receiver = WebhookReceiver(token=_DEMO_TOKEN, on_event=events.append, port=0, log_sink=logs.append)
    await receiver.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            headers = {} if request_token is None else {"X-A2A-Notification-Token": request_token}
            if raw_body is not None:
                response = await client.post(receiver.url, content=raw_body, headers=headers)
            else:
                response = await client.post(receiver.url, json=json_body, headers=headers)
    finally:
        await receiver.stop()
    return response, events, logs


# --- receiver: token check and event delivery --------------------------------------------------


def test_webhook_receiver_accepts_the_registered_token_and_delivers_the_parsed_event() -> None:
    response, events, logs = asyncio.run(
        _post_against_receiver(request_token=_DEMO_TOKEN, json_body=_STATUS_UPDATE_BODY)
    )
    assert response.status_code == 200
    # The callback receives the raw parsed dict, not an adapted event (dict->flow is the caller's job).
    assert events == [_STATUS_UPDATE_BODY]
    assert any("event-received" in line for line in logs)


def test_webhook_receiver_rejects_a_wrong_token_with_401() -> None:
    response, events, logs = asyncio.run(
        _post_against_receiver(request_token="wrong-token", json_body=_STATUS_UPDATE_BODY)
    )
    assert response.status_code == 401
    assert events == []  # rejected events must not reach the callback
    assert any("token-rejected" in line for line in logs)


def test_webhook_receiver_rejects_a_missing_token_with_401() -> None:
    response, events, _ = asyncio.run(_post_against_receiver(request_token=None, json_body=_STATUS_UPDATE_BODY))
    assert response.status_code == 401
    assert events == []


def test_webhook_receiver_rejects_a_malformed_body_with_400() -> None:
    # 400 is the pinned malformed-body answer (the token check comes first, so this posts a valid token).
    response, events, _ = asyncio.run(_post_against_receiver(request_token=_DEMO_TOKEN, raw_body=b"not-json"))
    assert response.status_code == 400
    assert events == []


def test_webhook_receiver_rejects_an_unrecognized_body_with_400() -> None:
    # A JSON body without a recognized StreamResponse top-level key is not a task event.
    response, events, _ = asyncio.run(
        _post_against_receiver(request_token=_DEMO_TOKEN, json_body={"unknown": {"x": 1}})
    )
    assert response.status_code == 400
    assert events == []


# --- receiver: start/stop lifecycle ------------------------------------------------------------


def test_webhook_receiver_lifecycle_releases_the_port_and_stop_is_idempotent() -> None:
    events: list[dict[str, object]] = []
    logs: list[str] = []
    receiver = WebhookReceiver(token=_DEMO_TOKEN, on_event=events.append, port=0, log_sink=logs.append)

    async def scenario() -> None:
        await receiver.start()
        try:
            # port=0 resolved to the OS-assigned ephemeral port, now serving.
            assert receiver.port > 0
            assert _port_open(receiver.port)
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.post(
                    receiver.url, json=_STATUS_UPDATE_BODY, headers={"X-A2A-Notification-Token": _DEMO_TOKEN}
                )
            assert response.status_code == 200
        finally:
            await receiver.stop()
        # The graceful stop released the listening socket.
        deadline = time.monotonic() + 2.0
        while _port_open(receiver.port) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _port_open(receiver.port)
        # stop() is idempotent (here: a second call inside the same event loop).
        await receiver.stop()

    asyncio.run(scenario())
    assert events == [_STATUS_UPDATE_BODY]
    assert any("listening" in line for line in logs)
    assert any("stopped" in line for line in logs)
    # A third stop() on a fresh event loop is still a safe no-op.
    asyncio.run(receiver.stop())


def test_webhook_receiver_defaults_pin_host_port_and_url() -> None:
    assert DEFAULT_WEBHOOK_HOST == "127.0.0.1"
    assert DEFAULT_WEBHOOK_PORT == 9000
    receiver = WebhookReceiver(token=_DEMO_TOKEN, on_event=lambda event: None)
    assert receiver.port == 9000  # the configured port until started with 0
    assert receiver.url == "http://127.0.0.1:9000/webhook"


def test_webhook_receiver_start_raises_runtime_error_when_the_port_is_taken() -> None:
    # uvicorn answers a bind failure (port already taken) with sys.exit inside its startup; a
    # task-raised SystemExit would tear down the whole event loop instead of being observable,
    # so start() must convert it to the documented RuntimeError (and leave the holder serving).
    async def scenario() -> None:
        holder = WebhookReceiver(token=_DEMO_TOKEN, on_event=lambda event: None, port=0)
        await holder.start()
        try:
            with pytest.raises(RuntimeError, match="failed to start"):
                blocked = WebhookReceiver(token=_DEMO_TOKEN, on_event=lambda event: None, port=holder.port)
                await blocked.start()
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.post(
                    holder.url, json=_STATUS_UPDATE_BODY, headers={"X-A2A-Notification-Token": _DEMO_TOKEN}
                )
            assert response.status_code == 200  # the holder survived the failed startup attempt
        finally:
            await holder.stop()
        assert not _port_open(holder.port)

    asyncio.run(scenario())


# --- transport/webhook resolvers ---------------------------------------------------------------


def test_supported_transports_constant() -> None:
    assert SUPPORTED_TRANSPORTS == (TRANSPORT_SSE, TRANSPORT_WEBHOOK)


def test_resolve_transport_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no .env next to the cwd

    # No .env and no OS environment entry -> sse.
    assert resolve_transport() == TRANSPORT_SSE

    # Unknown value -> sse.
    env_path = tmp_path / ".env"
    env_path.write_text(f"{TRANSPORT_ENV_KEY}=bogus\n", encoding="utf-8")
    assert resolve_transport(env_path=env_path) == TRANSPORT_SSE

    # Known value -> itself.
    env_path.write_text(f"{TRANSPORT_ENV_KEY}=webhook\n", encoding="utf-8")
    assert resolve_transport(env_path=env_path) == TRANSPORT_WEBHOOK

    # OS environment fallback when the .env does not carry the key.
    env_path.write_text("A2AT_LANGUAGE=en-US\n", encoding="utf-8")
    monkeypatch.setenv(TRANSPORT_ENV_KEY, TRANSPORT_WEBHOOK)
    assert resolve_transport(env_path=env_path) == TRANSPORT_WEBHOOK

    # The .env value wins over the OS environment.
    env_path.write_text(f"{TRANSPORT_ENV_KEY}=sse\n", encoding="utf-8")
    assert resolve_transport(env_path=env_path) == TRANSPORT_SSE


def test_resolve_webhook_port_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no .env next to the cwd

    # No .env and no OS environment entry -> the 9000 default.
    assert resolve_webhook_port() == 9000

    # 0 keeps the OS-assigned ephemeral port; a valid override passes through.
    env_path = tmp_path / ".env"
    env_path.write_text(f"{WEBHOOK_PORT_ENV_KEY}=0\n", encoding="utf-8")
    assert resolve_webhook_port(env_path=env_path) == 0
    env_path.write_text(f"{WEBHOOK_PORT_ENV_KEY}=8123\n", encoding="utf-8")
    assert resolve_webhook_port(env_path=env_path) == 8123

    # Invalid values (non-integer, blank, out of range) -> the 9000 default.
    for raw_value in ("bogus", "", "-1", "70000"):
        env_path.write_text(f"{WEBHOOK_PORT_ENV_KEY}={raw_value}\n", encoding="utf-8")
        assert resolve_webhook_port(env_path=env_path) == 9000

    # OS environment fallback when the .env does not carry the key.
    env_path.write_text("A2AT_LANGUAGE=en-US\n", encoding="utf-8")
    monkeypatch.setenv(WEBHOOK_PORT_ENV_KEY, "8123")
    assert resolve_webhook_port(env_path=env_path) == 8123

    # The .env value wins over the OS environment.
    env_path.write_text(f"{WEBHOOK_PORT_ENV_KEY}=9000\n", encoding="utf-8")
    assert resolve_webhook_port(env_path=env_path) == 9000


def test_resolve_webhook_token_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no .env next to the cwd

    # No .env and no OS environment entry -> the demo default.
    assert resolve_webhook_token() == _DEMO_TOKEN

    # A configured token passes through.
    env_path = tmp_path / ".env"
    env_path.write_text(f"{WEBHOOK_TOKEN_ENV_KEY}=client-secret-1\n", encoding="utf-8")
    assert resolve_webhook_token(env_path=env_path) == "client-secret-1"

    # OS environment fallback when the .env does not carry the key.
    env_path.write_text("A2AT_LANGUAGE=en-US\n", encoding="utf-8")
    monkeypatch.setenv(WEBHOOK_TOKEN_ENV_KEY, "client-secret-2")
    assert resolve_webhook_token(env_path=env_path) == "client-secret-2"

    # The .env value wins over the OS environment.
    env_path.write_text(f"{WEBHOOK_TOKEN_ENV_KEY}=client-secret-3\n", encoding="utf-8")
    assert resolve_webhook_token(env_path=env_path) == "client-secret-3"


def test_resolve_webhook_idle_timeout_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no .env next to the cwd

    # No .env and no OS environment entry -> the 30.0 default.
    assert resolve_webhook_idle_timeout() == 30.0

    # A configured timeout passes through.
    env_path = tmp_path / ".env"
    env_path.write_text(f"{WEBHOOK_IDLE_TIMEOUT_ENV_KEY}=45.5\n", encoding="utf-8")
    assert resolve_webhook_idle_timeout(env_path=env_path) == 45.5

    # Invalid values (non-numeric, blank, non-positive) -> the 30.0 default.
    for raw_value in ("bogus", "", "0", "-3"):
        env_path.write_text(f"{WEBHOOK_IDLE_TIMEOUT_ENV_KEY}={raw_value}\n", encoding="utf-8")
        assert resolve_webhook_idle_timeout(env_path=env_path) == 30.0

    # OS environment fallback when the .env does not carry the key.
    env_path.write_text("A2AT_LANGUAGE=en-US\n", encoding="utf-8")
    monkeypatch.setenv(WEBHOOK_IDLE_TIMEOUT_ENV_KEY, "12")
    assert resolve_webhook_idle_timeout(env_path=env_path) == 12.0

    # The .env value wins over the OS environment.
    env_path.write_text(f"{WEBHOOK_IDLE_TIMEOUT_ENV_KEY}=30\n", encoding="utf-8")
    assert resolve_webhook_idle_timeout(env_path=env_path) == 30.0
