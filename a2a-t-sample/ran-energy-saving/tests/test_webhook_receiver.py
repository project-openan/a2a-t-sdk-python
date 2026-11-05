"""Contract tests of the embedded webhook receiver (plain, TLS, Basic auth, public URL).

The TLS cases need the yellow-zone certificate pair under ``Orange/``; without it they are skipped
so the auth contract still runs on a clean checkout.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from shared.webhook_receiver import WebhookReceiver

_CASE_DIR = Path(__file__).resolve().parents[1]
_CERT = _CASE_DIR / "Orange" / "server.cer"
_KEY = _CASE_DIR / "Orange" / "server_key_nopass.pem"

_EVENT = {"statusUpdate": {"taskId": "t-1", "status": {"state": "TASK_STATE_CANCELED"}}}

#: The real NMS authenticates with Basic auth and sends no push token.
_CREDENTIALS = "north-user:secret"

_needs_cert = pytest.mark.skipif(
    not (_CERT.exists() and _KEY.exists()),
    reason=f"yellow-zone TLS certificate pair is not present at {_CERT.parent}",
)


def _basic(credentials: str) -> dict[str, str]:
    return {"Authorization": f"Basic {base64.b64encode(credentials.encode()).decode()}"}


@pytest.fixture
def events() -> list[dict[str, object]]:
    return []


async def _send(receiver: WebhookReceiver, headers: dict[str, str], *, tls: bool = False) -> httpx.Response:
    scheme = "https" if tls else "http"
    async with httpx.AsyncClient(verify=False, trust_env=False) as client:
        return await client.post(f"{scheme}://127.0.0.1:{receiver.port}/webhook", json=_EVENT, headers=headers)


@asynccontextmanager
async def _receiver(events: list[dict[str, object]], **kwargs: object) -> AsyncIterator[WebhookReceiver]:
    receiver = WebhookReceiver(on_event=events.append, host="127.0.0.1", port=0, **kwargs)  # type: ignore[arg-type]
    await receiver.start()
    try:
        yield receiver
    finally:
        await receiver.stop()


def test_public_url_wins_over_the_bind_address(events: list[dict[str, object]]) -> None:
    receiver = WebhookReceiver(
        token="ignored",
        on_event=events.append,
        public_url="https://141.5.146.234:9000/webhook",
        auth_credentials=_CREDENTIALS,
    )

    assert receiver.url == "https://141.5.146.234:9000/webhook"


def test_without_a_public_url_the_scheme_follows_the_tls_pair(events: list[dict[str, object]]) -> None:
    plain = WebhookReceiver(token="token", on_event=events.append, host="192.0.2.10", port=9000)
    tls = WebhookReceiver(
        token="token",
        on_event=events.append,
        host="192.0.2.10",
        port=9000,
        tls_cert=str(_CERT),
        tls_key=str(_KEY),
    )

    assert plain.url == "http://192.0.2.10:9000/webhook"
    assert tls.url == "https://192.0.2.10:9000/webhook"


async def test_listener_accepts_basic_auth_without_the_push_token(events: list[dict[str, object]]) -> None:
    async with _receiver(events, token="ignored", auth_credentials=_CREDENTIALS) as receiver:
        response = await _send(receiver, _basic(_CREDENTIALS))

    assert response.status_code == 200
    assert events == [_EVENT]


async def test_listener_rejects_wrong_or_missing_basic_credentials(events: list[dict[str, object]]) -> None:
    async with _receiver(events, token="ignored", auth_credentials=_CREDENTIALS) as receiver:
        wrong = await _send(receiver, _basic("north-user:wrong"))
        # A push token does not stand in for Basic auth once credentials are configured.
        token_only = await _send(receiver, {"X-A2A-Notification-Token": "ignored"})
        bearered = await _send(receiver, {"Authorization": "Bearer north-token"})

    assert (wrong.status_code, token_only.status_code, bearered.status_code) == (401, 401, 401)
    assert events == []


async def test_without_credentials_the_push_token_is_still_checked(events: list[dict[str, object]]) -> None:
    """The a2a-sdk / simulator path must keep its push-token contract."""
    async with _receiver(events, token="push-token") as receiver:
        accepted = await _send(receiver, {"X-A2A-Notification-Token": "push-token"})
        rejected = await _send(receiver, {"X-A2A-Notification-Token": "wrong"})

    assert (accepted.status_code, rejected.status_code) == (200, 401)
    assert events == [_EVENT]


async def test_auth_rejection_logs_the_header_shape_but_never_the_credential(
    events: list[dict[str, object]], caplog: pytest.LogCaptureFixture
) -> None:
    """Diagnosing the NMS push needs the scheme, not the secret it carries."""
    async with _receiver(events, token="ignored", auth_credentials=_CREDENTIALS) as receiver:
        with caplog.at_level(logging.WARNING):
            await _send(receiver, {"Authorization": "Bearer north-secret-token"})

    rejected = [record.getMessage() for record in caplog.records if "auth-rejected" in record.getMessage()]
    assert len(rejected) == 1
    assert "scheme=Bearer" in rejected[0]
    assert "north-secret-token" not in caplog.text
    assert base64.b64encode(b"north-secret-token").decode() not in caplog.text


@_needs_cert
async def test_https_listener_accepts_basic_auth(events: list[dict[str, object]]) -> None:
    settings = {"token": "ignored", "tls_cert": str(_CERT), "tls_key": str(_KEY), "auth_credentials": _CREDENTIALS}
    async with _receiver(events, **settings) as receiver:
        response = await _send(receiver, _basic(_CREDENTIALS), tls=True)

    assert response.status_code == 200
    assert events == [_EVENT]


@_needs_cert
async def test_https_listener_refuses_a_plain_http_client(events: list[dict[str, object]]) -> None:
    """The listener really terminates TLS: a plain-http call never gets an HTTP response."""
    settings = {"token": "ignored", "tls_cert": str(_CERT), "tls_key": str(_KEY), "auth_credentials": _CREDENTIALS}
    async with _receiver(events, **settings) as receiver:
        with pytest.raises(httpx.HTTPError):
            await _send(receiver, _basic(_CREDENTIALS), tls=False)

    assert events == []
