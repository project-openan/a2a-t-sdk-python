"""Tests of the cross-process trace transport (``shared.trace`` emitters)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from shared.trace import FileTraceEmitter, HttpTraceEmitter, _default_trace_post


def _span(
    run_id: str,
    span_id: str,
    *,
    kind: str = "llm",
    detail: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build one minimal server span envelope."""
    return {
        "schemaVersion": 1,
        "runId": run_id,
        "spanId": span_id,
        "kind": kind,
        "phase": "execution",
        "title": f"Server {kind}",
        "detail": detail if detail is not None else {},
    }


class _Poster:
    """Fake POST function recording every attempt and failing on demand."""

    def __init__(self, *, fail_attempts: int = 0) -> None:
        self.calls: list[tuple[str, object, dict[str, str]]] = []
        self.fail_attempts = fail_attempts

    def __call__(self, url: str, body: object, headers: Mapping[str, str]) -> object:
        self.calls.append((url, body, dict(headers)))
        if len(self.calls) <= self.fail_attempts:
            raise RuntimeError("receiver down")
        return 202


def test_file_emitter_writes_per_run_jsonl_and_redacts(tmp_path: Path) -> None:
    emitter = FileTraceEmitter(tmp_path)

    emitter(_span("run-1", "s1", detail={"api_key": "secret", "Authorization": "Bearer x", "safe": "v"}))
    emitter(_span("run-1", "s2", kind="tool"))
    emitter(_span("run-2", "s3"))
    emitter.close()
    emitter(_span("run-1", "s4"))  # ignored after close

    path = tmp_path / "run-1" / "server-trace.jsonl"
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["spanId"] for line in lines] == ["s1", "s2"]
    detail = lines[0]["detail"]
    assert detail["api_key"] == "[redacted]"
    assert detail["Authorization"] == "[redacted]"
    assert detail["safe"] == "v"
    assert (tmp_path / "run-2" / "server-trace.jsonl").exists()


def test_file_emitter_never_escapes_the_directory(tmp_path: Path) -> None:
    emitter = FileTraceEmitter(tmp_path)

    emitter(_span("../../evil", "s1"))
    emitter.close()

    written = list(tmp_path.rglob("server-trace.jsonl"))
    assert len(written) == 1
    assert written[0].parent.parent == tmp_path


def test_http_emitter_groups_by_run_and_sends_the_token() -> None:
    poster = _Poster()
    emitter = HttpTraceEmitter("http://127.0.0.1:8080/", "local-token", post=poster, flush_interval=0.05)

    emitter(_span("run-1", "s1"))
    emitter(_span("run-2", "s2"))
    emitter(_span("run-1", "s3"))
    emitter.close()

    urls = sorted(url for url, _body, _headers in poster.calls)
    assert urls == [
        "http://127.0.0.1:8080/api/runs/run-1/trace",
        "http://127.0.0.1:8080/api/runs/run-2/trace",
    ]
    for _url, body, headers in poster.calls:
        assert headers["x-a2at-trace-token"] == "local-token"
        assert isinstance(body, list)
    run_one = next(body for url, body, _ in poster.calls if "run-1" in url)
    assert isinstance(run_one, list)
    assert [span["spanId"] for span in run_one] == ["s1", "s3"]


def test_http_emitter_retries_a_failing_batch() -> None:
    poster = _Poster(fail_attempts=1)
    emitter = HttpTraceEmitter(
        "http://127.0.0.1:8080",
        "t",
        post=poster,
        flush_interval=0.05,
        max_attempts=3,
    )

    emitter(_span("run-1", "s1"))
    emitter.close()

    assert len(poster.calls) == 2


def test_http_emitter_drops_a_batch_after_the_attempt_budget() -> None:
    poster = _Poster(fail_attempts=99)
    emitter = HttpTraceEmitter(
        "http://127.0.0.1:8080",
        "t",
        post=poster,
        flush_interval=0.05,
        max_attempts=2,
    )

    emitter(_span("run-1", "s1"))
    emitter.close()  # never raises

    assert len(poster.calls) == 2


def test_http_emitter_drops_spans_without_a_run_id() -> None:
    poster = _Poster()
    emitter = HttpTraceEmitter("http://127.0.0.1:8080", "t", post=poster, flush_interval=0.05)

    emitter(_span("", "orphan"))
    emitter(_span("run-1", "s1"))
    emitter.close()

    assert len(poster.calls) == 1
    assert "run-1" in poster.calls[0][0]


def test_http_emitter_redacts_secrets_before_posting() -> None:
    poster = _Poster()
    emitter = HttpTraceEmitter("http://127.0.0.1:8080", "t", post=poster, flush_interval=0.05)

    emitter(
        _span(
            "run-1",
            "s1",
            detail={"headers": {"Authorization": "Bearer secret", "X-API-Key": "key"}, "prompt": "hello"},
        )
    )
    emitter.close()

    _url, body, headers = poster.calls[0]
    assert isinstance(body, list)
    detail = body[0]["detail"]
    assert detail["headers"]["Authorization"] == "[redacted]"
    assert detail["headers"]["X-API-Key"] == "[redacted]"
    assert "Bearer secret" not in json.dumps(body)
    assert headers["x-a2at-trace-token"] == "t"


def test_http_emitter_batches_reach_the_batch_size_without_waiting() -> None:
    poster = _Poster()
    emitter = HttpTraceEmitter(
        "http://127.0.0.1:8080",
        "t",
        post=poster,
        batch_size=2,
        flush_interval=5.0,
    )

    emitter(_span("run-1", "s1"))
    emitter(_span("run-1", "s2"))
    # The background thread flushes as soon as the second span arrives.
    for _ in range(200):
        if poster.calls:
            break
        import time

        time.sleep(0.01)
    emitter.close()

    assert poster.calls
    body: Any = poster.calls[0][1]
    assert [span["spanId"] for span in body] == ["s1", "s2"]


def test_default_trace_post_never_uses_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spans carry prompts/tool data, so a corporate proxy must never receive them."""
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:9")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:9")
    captured: dict[str, object] = {}

    class _Response:
        status_code = 202

        def raise_for_status(self) -> None:
            return None

    def fake_post(url: str, **kwargs: object) -> _Response:
        captured["url"] = url
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(httpx, "post", fake_post)

    assert _default_trace_post("http://127.0.0.1:8080/api/x", {"spans": []}, {"a": "b"}) == 202
    assert captured["trust_env"] is False
    assert captured["url"] == "http://127.0.0.1:8080/api/x"


def test_default_trace_post_reports_the_ui_rejection_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 404/403 carries the reason (unknown_run, trace_token_invalid) into the OSS log."""

    class _Response:
        status_code = 404
        text = '{"error": "Unknown run.", "code": "unknown_run"}'

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: _Response())

    with pytest.raises(RuntimeError, match="unknown_run"):
        _default_trace_post("http://127.0.0.1:8080/api/runs/run-1/trace", [], {})
