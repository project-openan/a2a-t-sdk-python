"""Regression tests for P1-2: retry exhaustion on 429/5xx must be a network failure.

Covers (task-4 item 5): ``NetworkError`` (exit code 4) with the final HTTP status in
the message, both at the client level and end-to-end through ``cli.main``, plus the
guarantee that non-retryable 4xx still map to exit code 1 (no regression).
"""

from __future__ import annotations

import pytest

from langfuse_export import cli
from langfuse_export.client import NetworkError

from fix_support import TRACE, FakeResponse, make_client, set_env, workdir

RETRYABLE = [429, 500, 502, 503, 504]


@pytest.mark.parametrize("status", RETRYABLE)
def test_retry_exhausted_raises_network_error_with_last_status(monkeypatch, status):
    client = make_client(monkeypatch)
    attempts: list[str] = []

    def router(url, params=None, timeout=None):
        attempts.append(url)
        return FakeResponse(status)

    monkeypatch.setattr(client.session, "get", router)
    with pytest.raises(NetworkError) as info:
        client.get_json("/api/public/health")

    assert str(status) in str(info.value)
    assert "retry exhausted" in str(info.value)
    assert len(attempts) == 3  # max_attempts: 1 initial + 2 retries
    assert client.records[-1].endpoint == "/api/public/health"
    assert client.records[-1].status == status


def test_retry_exhausted_does_not_raise_api_error(monkeypatch):
    from langfuse_export.client import ApiError

    client = make_client(monkeypatch)
    monkeypatch.setattr(client.session, "get", lambda *a, **k: FakeResponse(503))
    with pytest.raises(NetworkError):
        client.get_json("/api/public/v2/observations")
    # A retryable status is transport-level: it must not fall through to ApiError.
    assert not isinstance(NetworkError("x"), ApiError)


@pytest.mark.parametrize("status", [429, 500])
def test_cli_main_returns_4_when_retries_exhausted(monkeypatch, capsys, status):
    set_env(monkeypatch)
    client = make_client(monkeypatch)
    monkeypatch.setattr(client.session, "get", lambda url, params=None, timeout=None: FakeResponse(status))
    monkeypatch.setattr(cli, "_client_from", lambda config, args: client)

    with workdir(f"retry_main_{status}") as tmp:
        code = cli.main([TRACE, "--out", str(tmp / "out")])

    captured = capsys.readouterr()
    assert code == 4
    assert str(status) in captured.err
    assert code != 1


def test_cli_main_returns_1_for_non_retryable_400(monkeypatch, capsys):
    set_env(monkeypatch)
    client = make_client(monkeypatch)
    monkeypatch.setattr(client.session, "get", lambda url, params=None, timeout=None: FakeResponse(400))
    monkeypatch.setattr(cli, "_client_from", lambda config, args: client)

    with workdir("api_400") as tmp:
        code = cli.main([TRACE, "--out", str(tmp / "out")])

    assert code == 1
    assert "400" in capsys.readouterr().err
