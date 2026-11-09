"""Shared fakes for the proxy / TLS regression tests (not collected by pytest)."""

from __future__ import annotations

import http.client
import json
import shutil
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fix_support import FakeResponse  # re-exported for the net tests

TMP_ROOT = Path("tmp_test_net")
TRACE = "9f2c1a7de4b34c0e8a5f2d1c6b9e3a70"
PROXY_USER = "proxyuser"
PROXY_PASS = "Sup3rSecret"
PROXY_SECRET = f"{PROXY_USER}:{PROXY_PASS}"
MEDIA_SECRET = "SECRETSIG"

CLEAN_ENV_KEYS = (
    "LANGFUSE_HOST",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_PROXY",
    "LANGFUSE_NO_PROXY",
    "LANGFUSE_CACERT",
    "LANGFUSE_INSECURE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "SSL_CERT_FILE",
)


@contextmanager
def workdir(name: str):
    path = TMP_ROOT / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        try:
            TMP_ROOT.rmdir()
        except OSError:
            pass


def clean_net_env(monkeypatch) -> None:
    for key in CLEAN_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def set_credentials(monkeypatch, host: str) -> None:
    monkeypatch.setenv("LANGFUSE_HOST", host)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")


# -- fake Langfuse ----------------------------------------------------------
def make_row(base: str) -> dict[str, Any]:
    return {
        "id": "obs_1",
        "traceId": TRACE,
        "parentObservationId": None,
        "type": "SPAN",
        "name": "root",
        "startTime": "2026-11-06T22:14:02.100Z",
        "endTime": "2026-11-06T22:14:02.110Z",
        "latency": 0.01,
        "level": "DEFAULT",
        "usageDetails": {},
        "metadata": {},
        "input": "@@@langfuseMedia:type=image/png|id=med_1|source=base64_data_uri@@@",
        "traceName": "net-trace",
    }


class _LangfuseHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):  # silence
        pass

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, body: bytes, content_type: str = "application/octet-stream") -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        server = self.server
        base = f"http://127.0.0.1:{server.server_port}"
        path = self.path.split("?")[0]
        server.seen.append((self.path, dict(self.headers)))
        if path == "/api/public/health":
            return self._json({"version": "4.76.0"})
        if path == "/api/public/v2/observations":
            return self._json({"data": [make_row(base)]})
        if path == "/api/public/v3/scores":
            return self._json({"data": []})
        if path == "/api/public/projects":
            return self._json({"data": [{"id": "proj_1", "name": "Proj"}]})
        if path.startswith("/api/public/media/"):
            return self._json(
                {
                    "mediaId": "med_1",
                    "contentType": "image/png",
                    "contentLength": 5,
                    "url": f"{base}/blob?X-Amz-Signature={MEDIA_SECRET}",
                    "urlExpiry": "2026-11-07T12:00:00.000Z",
                }
            )
        if path == "/blob":
            return self._bytes(b"hello", "image/png")
        return self._json({"error": "not found"}, 404)


class FakeLangfuse:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _LangfuseHandler)
        self.server.seen = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self.server.server_port

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def seen(self) -> list[tuple[str, dict[str, str]]]:
        return self.server.seen

    def __enter__(self) -> "FakeLangfuse":
        self.thread.start()
        return self

    def __exit__(self, *exc_info) -> bool:
        self.server.shutdown()
        self.server.server_close()
        return False


# -- recording HTTP proxy ---------------------------------------------------
class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):  # silence
        pass

    def _raw(self, status: int, body: bytes, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_CONNECT(self) -> None:
        server = self.server
        server.seen.append((f"CONNECT {self.path}", dict(self.headers)))
        # Reject every tunnel: requests surfaces this as ProxyError -> exit 4.
        return self._raw(407, b"", {"Proxy-Authenticate": 'Basic realm="proxy"'})

    def do_GET(self) -> None:
        server = self.server
        server.seen.append((self.path, dict(self.headers)))
        if server.mode == "407":
            return self._raw(407, b"", {"Proxy-Authenticate": 'Basic realm="proxy"'})
        target = urlsplit(self.path)
        if not target.hostname:
            return self._raw(400, b"absolute URI required")
        path = target.path + (f"?{target.query}" if target.query else "")
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in ("proxy-authorization", "proxy-connection", "connection")
        }
        headers["X-Via-Proxy"] = "1"
        conn = http.client.HTTPConnection(target.hostname, target.port or 80, timeout=10)
        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            body = resp.read()
            forwarded = [(key, value) for key, value in resp.getheaders() if key.lower() not in ("transfer-encoding", "connection", "content-length")]
        finally:
            conn.close()
        self.send_response(resp.status)
        for key, value in forwarded:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeProxy:
    def __init__(self, mode: str = "forward") -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ProxyHandler)
        self.server.mode = mode
        self.server.seen = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self.server.server_port

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def seen(self) -> list[tuple[str, dict[str, str]]]:
        return self.server.seen

    def __enter__(self) -> "FakeProxy":
        self.thread.start()
        return self

    def __exit__(self, *exc_info) -> bool:
        self.server.shutdown()
        self.server.server_close()
        return False


def unused_port() -> int:
    """Bind then close: the address is (almost certainly) refused afterwards."""
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def run_cli(monkeypatch, argv: list[str]) -> int:
    from langfuse_export import cli

    monkeypatch.setattr("time.sleep", lambda *_: None)
    return cli.main(argv)
