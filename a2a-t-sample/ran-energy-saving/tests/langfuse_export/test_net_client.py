import pathlib
import warnings as warnings_module

import pytest
import requests
from urllib3.exceptions import InsecureRequestWarning

from langfuse_export import client as client_module
from langfuse_export.client import ApiError, AuthError, LangfuseClient, NetworkError
from net_support import PROXY_SECRET, FakeResponse


def make_client(**kwargs):
    host = kwargs.pop("host", "https://example.test")
    return LangfuseClient(host, "pk-test", "sk-test", max_attempts=2, **kwargs)


def test_explicit_proxy_is_applied_and_ignores_env_no_proxy(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")
    client = make_client(proxy="http://user:pass@proxy.corp:8080")
    assert client.proxies == {
        "http": "http://user:pass@proxy.corp:8080",
        "https": "http://user:pass@proxy.corp:8080",
    }
    assert client.session.proxies == client.proxies
    assert client.session.trust_env is True
    assert client.verify is True


def test_no_proxy_disables_env_and_trust_env(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://env.proxy:9999")
    client = make_client(no_proxy=True)
    assert client.proxies is None
    assert client.session.trust_env is False
    assert "https" not in client.session.proxies


def test_verify_priority_insecure_over_cacert():
    insecure = make_client(insecure=True, cacert="/tmp/ca.pem")
    assert insecure.verify is False
    assert insecure.session.verify is False
    custom_ca = make_client(cacert="/tmp/ca.pem")
    assert custom_ca.verify == "/tmp/ca.pem"
    default = make_client()
    assert default.verify is True


def test_407_maps_to_auth_error_without_credentials(monkeypatch):
    client = make_client(proxy=f"http://{PROXY_SECRET}@proxy.corp:8080")
    monkeypatch.setattr(client_module.time, "sleep", lambda *_: None)
    monkeypatch.setattr(client.session, "get", lambda *a, **k: FakeResponse(407, text="proxy says no"))
    with pytest.raises(AuthError) as info:
        client.get_json("/api/public/health")
    message = str(info.value)
    assert "proxy authentication failed" in message
    assert PROXY_SECRET not in message
    assert "proxy.corp:8080" in message


def test_proxy_error_maps_to_network_error(monkeypatch):
    client = make_client(proxy=f"http://{PROXY_SECRET}@proxy.corp:8080")
    monkeypatch.setattr(client_module.time, "sleep", lambda *_: None)

    def raise_proxy_error(*args, **kwargs):
        raise requests.exceptions.ProxyError("cannot connect to proxy")

    monkeypatch.setattr(client.session, "get", raise_proxy_error)
    with pytest.raises(NetworkError) as info:
        client.get_json("/api/public/health")
    message = str(info.value)
    assert "proxy.corp:8080" in message
    assert PROXY_SECRET not in message


def test_download_sends_only_user_agent_and_applies_network_settings(monkeypatch):
    # No pytest tmp_path (sandbox denies %TEMP%): use a workspace-relative file.
    dest = pathlib.Path("tmp_test_net_download.bin")
    dest.parent.mkdir(exist_ok=True)
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(200, headers={"Content-Length": "5"}, chunks=[b"hello"])

    monkeypatch.setattr(client_module.requests, "get", fake_get)
    client = make_client(proxy="http://proxy.corp:8080", insecure=True)
    try:
        written = client.download(
            "https://bucket.example/blob?X-Amz-Signature=SECRETSIG",
            dest,
            max_bytes=1024,
            label="media/med_1",
            proxies=client.proxies,
            verify=client.verify,
        )
    finally:
        dest.unlink(missing_ok=True)
    assert written == 5
    _url, kwargs = calls[0]
    assert kwargs["headers"] == {"User-Agent": client_module.USER_AGENT}
    assert "Authorization" not in kwargs["headers"]
    assert kwargs["proxies"] == client.proxies
    assert kwargs["verify"] is False
    assert client.records[-1].endpoint == "media/med_1"


def test_download_no_proxy_uses_trust_env_false_session(monkeypatch):
    created = {}

    class FakeSession:
        def __init__(self, *args, **kwargs):
            created["session"] = self
            self.trust_env = True
            self.headers = {"Cookie": "should-be-cleared"}

        def get(self, url, **kwargs):
            created["kwargs"] = kwargs
            return FakeResponse(200, headers={"Content-Length": "2"}, chunks=[b"ok"])

        def close(self):
            created["closed"] = True

    monkeypatch.setattr(client_module.requests, "Session", FakeSession)
    client = make_client(no_proxy=True)
    dest = pathlib.Path("tmp_test_net_download2.bin")
    try:
        written = client.download("http://127.0.0.1/blob", dest, max_bytes=1024, label="media/med_2")
    finally:
        dest.unlink(missing_ok=True)
    assert written == 2
    assert created["session"].trust_env is False
    assert created["session"].headers == {"User-Agent": client_module.USER_AGENT}
    assert created["kwargs"]["proxies"] is None
    assert created["closed"] is True


def test_download_failure_removes_partial_file(monkeypatch):
    dest = pathlib.Path("tmp_test_net_download3.bin")
    dest.write_bytes(b"partial")

    def fake_get(url, **kwargs):
        return FakeResponse(403, text="expired")

    monkeypatch.setattr(client_module.requests, "get", fake_get)
    client = make_client()
    with pytest.raises(ApiError):
        client.download("https://bucket.example/blob", dest, max_bytes=1024)
    assert not dest.exists()


def test_insecure_installs_warning_filter():
    make_client(insecure=True)
    filtered = [
        entry
        for entry in warnings_module.filters
        if entry[0] == "ignore" and entry[2] is InsecureRequestWarning
    ]
    assert filtered
