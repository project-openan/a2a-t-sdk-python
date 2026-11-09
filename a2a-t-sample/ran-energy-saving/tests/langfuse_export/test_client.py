import pytest
import requests

from langfuse_export import client as client_module
from langfuse_export.client import AuthError, LangfuseClient, NetworkError, NotFoundError


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


def make_client(monkeypatch):
    monkeypatch.setattr(client_module.time, "sleep", lambda *_: None)
    return LangfuseClient("https://example.test", "pk", "sk", max_attempts=3)


def test_retry_then_success(monkeypatch):
    client = make_client(monkeypatch)
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if len(calls) == 1:
            return FakeResponse(429, headers={"Retry-After": "5"})
        return FakeResponse(200, {"ok": True})

    monkeypatch.setattr(client.session, "get", fake_get)
    assert client.get_json("/api/public/health") == {"ok": True}
    assert len(calls) == 2


def test_auth_error(monkeypatch):
    client = make_client(monkeypatch)
    monkeypatch.setattr(client.session, "get", lambda *a, **k: FakeResponse(401))
    with pytest.raises(AuthError):
        client.get_json("/api/public/v2/observations")


def test_not_found(monkeypatch):
    client = make_client(monkeypatch)
    monkeypatch.setattr(client.session, "get", lambda *a, **k: FakeResponse(404))
    with pytest.raises(NotFoundError):
        client.get_json("/api/public/v3/scores")


def test_network_error_after_retries(monkeypatch):
    client = make_client(monkeypatch)

    def fake_get(*args, **kwargs):
        raise requests.Timeout("timeout")

    monkeypatch.setattr(client.session, "get", fake_get)
    with pytest.raises(NetworkError):
        client.get_json("/api/public/health")
    assert len(client.records) == 1
    assert client.records[0].status is None


def test_cursor_pagination(monkeypatch):
    client = make_client(monkeypatch)
    pages = [{"data": [1], "meta": {"cursor": "c1"}}, {"data": [2], "meta": {"cursor": None}}]
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return FakeResponse(200, pages[len(calls) - 1])

    monkeypatch.setattr(client.session, "get", fake_get)
    bodies = list(client.iter_cursor_pages("/api/public/v2/observations", {"limit": 1000}, max_pages=5))
    assert [body["data"] for body in bodies] == [[1], [2]]
    assert "cursor" not in calls[0]
    assert calls[1]["cursor"] == "c1"
