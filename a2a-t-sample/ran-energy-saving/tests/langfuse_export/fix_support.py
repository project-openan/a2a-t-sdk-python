"""Shared fakes and helpers for the post-review regression tests.

This module is deliberately not named ``test_*`` so pytest does not collect it.
It is imported by the ``tests/test_fix_*.py`` files via the ``tests/`` entry that
``conftest.py`` puts on ``sys.path``.
"""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from langfuse_export import client as client_module
from langfuse_export.client import LangfuseClient

TRACE = "9f2c1a7de4b34c0e8a5f2d1c6b9e3a70"

# A presigned-style URL: it must never be logged, written, or recorded anywhere.
PRESIGNED_URL = (
    "https://bucket.example.test/media.png"
    "?X-Amz-Signature=SECRETSIG&X-Amz-Credential=SECRETCRED"
)

# Workspace-relative temp root: pytest.tmp_path is unusable (the sandbox denies %TEMP%).
TMP_ROOT = Path("tmp_test_fix")


@contextmanager
def workdir(name: str):
    """Create ``tmp_test_fix/<name>`` and always remove it afterwards."""
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


# -- raw rows ---------------------------------------------------------------
def make_row(**overrides: Any) -> dict[str, Any]:
    """Synthetic observation row with the fields ``normalize`` consumes."""
    base: dict[str, Any] = {
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
    }
    base.update(overrides)
    return base


def media_token(media_id: str = "med_1", mime: str = "image/png") -> str:
    return f"@@@langfuseMedia:type={mime}|id={media_id}|source=base64_data_uri@@@"


# -- fake HTTP --------------------------------------------------------------
class FakeResponse:
    """Minimal ``requests.Response`` stand-in (json / stream / headers)."""

    def __init__(
        self,
        status: int = 200,
        payload: Any = None,
        headers: dict[str, str] | None = None,
        text: str = "",
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status
        self._payload = {} if payload is None else payload
        self.headers = dict(headers or {})
        self.text = text
        self._chunks = list(chunks or [])

    def json(self) -> Any:
        return self._payload

    def iter_content(self, chunk_size: int = 65536):
        for chunk in self._chunks:
            yield chunk

    def close(self) -> None:
        pass

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


def install_media_router(monkeypatch, responses: list[Any]) -> list[tuple[str, dict[str, Any]]]:
    """Patch the credential-free ``requests.get`` used by ``LangfuseClient.download``.

    ``responses`` holds ``FakeResponse`` instances (or exceptions to raise). When a
    single response is left it is reused, which keeps "always fails" cases short.
    Returns the captured ``(url, kwargs)`` pairs.
    """
    calls: list[tuple[str, dict[str, Any]]] = []
    queue = list(responses)

    def fake_get(url: str, **kwargs: Any):
        calls.append((url, dict(kwargs)))
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(client_module.requests, "get", fake_get)
    return calls


def make_client(monkeypatch, host: str = "https://example.test") -> LangfuseClient:
    """Real client with retry sleeps disabled; no request ever leaves the process."""
    monkeypatch.setattr(client_module.time, "sleep", lambda *_: None)
    return LangfuseClient(host, "pk-test", "sk-test", max_attempts=3)


def install_session_router(monkeypatch, client: LangfuseClient, router) -> list[tuple[str, dict[str, Any]]]:
    """Patch ``client.session.get`` with ``router(url, params)`` and record calls."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get(url: str, params: Any = None, timeout: Any = None):
        calls.append((url, dict(params or {})))
        return router(url, params)

    monkeypatch.setattr(client.session, "get", fake_get)
    return calls


# -- fake backends ----------------------------------------------------------
class FakeMediaClient:
    """Client stand-in for ``run_export`` when media downloads must not happen."""

    def __init__(self) -> None:
        self.records: list[Any] = []

    def download(self, *args: Any, **kwargs: Any) -> int:
        raise AssertionError("media download was not expected in this test")


class FakeBackend:
    """Backend stand-in implementing the interface ``run_export`` calls."""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        scores: list[dict[str, Any]] | None = None,
        media: dict[str, Any] | None = None,
        session_rows: list[dict[str, Any]] | None = None,
        legacy_trace: dict[str, Any] | None = None,
        truncated: bool = False,
        project: tuple[str | None, str | None] = (None, None),
        project_error: BaseException | None = None,
        kind: str = "v4",
        api_version: str = "v4",
        client: Any = None,
    ) -> None:
        self.kind = kind
        self.api_version = api_version
        self.rows = rows or []
        self.scores = scores or []
        self.media = {key: list(value) if isinstance(value, list) else value for key, value in (media or {}).items()}
        self.session_rows = session_rows or []
        self.legacy_trace = legacy_trace
        self.truncated = truncated
        self.project = project
        self.project_error = project_error
        self.warnings: list[str] = []
        self.client = client if client is not None else FakeMediaClient()
        self.fetch_media_calls: list[str] = []
        self.observation_args: list[tuple] = []

    # -- required interface ------------------------------------------------
    def fetch_observations(self, trace_id, from_time=None, to_time=None, expand_metadata=None):
        self.observation_args.append((trace_id, from_time, to_time, expand_metadata))
        return self.rows

    def fetch_scores(self, trace_id):
        return self.scores

    def fetch_media(self, media_id: str):
        self.fetch_media_calls.append(media_id)
        value = self.media.get(media_id)
        if isinstance(value, list):
            value = value.pop(0) if len(value) > 1 else value[0]
        if isinstance(value, BaseException):
            raise value
        if value is None:
            raise AssertionError(f"unexpected media fetch for {media_id}")
        return dict(value)

    def fetch_project(self):
        if self.project_error is not None:
            self.warnings.append(f"project lookup failed: {type(self.project_error).__name__}")
            return None, None
        return self.project

    def fetch_session_observations(self, session_id):
        return self.session_rows

    def fetch_legacy_trace(self, trace_id):
        return self.legacy_trace


class FakeApiClient:
    """``LangfuseClient`` stand-in for backend unit tests (records calls, no HTTP)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.records: list[Any] = []
        self.health: dict[str, Any] = {"version": "4.76.0"}
        self.page_bodies: dict[str, list[dict[str, Any]]] = {}
        self.cursor_bodies: dict[str, list[dict[str, Any]]] = {}
        self.on_get = None

    def get_json(self, path: str, params: dict[str, Any] | None = None):
        params = dict(params or {})
        self.calls.append((path, params))
        if self.on_get is not None:
            return self.on_get(path, params)
        if path == "/api/public/health":
            return self.health
        queue = self.page_bodies.get(path)
        if queue:
            return queue.pop(0)
        return {"data": [], "meta": {}}

    def iter_cursor_pages(self, path, params, max_pages, cursor_key="cursor"):
        self.calls.append((path, dict(params or {})))
        queue = self.cursor_bodies.get(path, [])
        for _ in range(max_pages):
            if not queue:
                return
            body = queue.pop(0)
            yield body
            if not (body.get("meta") or {}).get(cursor_key):
                return


# -- misc -------------------------------------------------------------------
def set_env(monkeypatch, host: str | None = "https://cloud.langfuse.com") -> None:
    """Provide offline credentials; ``host=None`` exercises the default-host path."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    if host is None:
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    else:
        monkeypatch.setenv("LANGFUSE_HOST", host)


def wire_cli_backend(monkeypatch, backend: FakeBackend) -> None:
    """Make ``run_export`` use ``backend`` (and its client) instead of real ones."""
    from langfuse_export import cli

    monkeypatch.setattr(cli, "select_backend", lambda client, forced: backend)
    # ``run_export`` builds a client before selecting the backend; reuse the fake
    # backend's client so request records (media downloads) land in the manifest.
    monkeypatch.setattr(cli, "_client_from", lambda config, args: backend.client)
