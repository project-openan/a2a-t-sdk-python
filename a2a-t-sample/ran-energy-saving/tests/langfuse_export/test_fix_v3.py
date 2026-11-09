"""Regression tests for P1-6: the v3 backend is now a real, tested code path.

Covers task-4 item 11: ``--from/--to`` reach the query string, 3.179+ uses the v3
scores endpoint while older instances use the legacy one, scores and session page caps
set ``truncated``, forcing v3 still learns the version from ``/health``, and the legacy
``/traces/{id}`` object fills the summary (``source: "legacy"``) and is archived as
``raw/legacy-trace.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

from langfuse_export import backend_v3, cli
from langfuse_export.backend import select_backend
from langfuse_export.backend_v3 import V3Backend
from langfuse_export.client import NetworkError

from fix_support import FakeApiClient, FakeBackend, TRACE, make_row, set_env, wire_cli_backend, workdir

PAGE_LIMIT = backend_v3.PAGE_LIMIT


# -- observations query ----------------------------------------------------
def test_v3_observations_pass_from_to_to_query():
    client = FakeApiClient()
    backend = V3Backend(client, version="3.180.0")
    backend.fetch_observations(TRACE, from_time="2026-11-01T00:00:00Z", to_time="2026-11-08T00:00:00Z")

    path, params = client.calls[0]
    assert path == "/api/public/observations"
    assert params["traceId"] == TRACE
    assert params["fromStartTime"] == "2026-11-01T00:00:00Z"
    assert params["toStartTime"] == "2026-11-08T00:00:00Z"


def test_v3_observations_without_bounds_do_not_send_time_params():
    client = FakeApiClient()
    V3Backend(client).fetch_observations(TRACE)
    _path, params = client.calls[0]
    assert "fromStartTime" not in params
    assert "toStartTime" not in params


# -- scores endpoint selection --------------------------------------------
def test_v3_scores_use_v3_endpoint_on_3179_plus():
    client = FakeApiClient()
    client.cursor_bodies["/api/public/v3/scores"] = [{"data": [{"id": "s1"}], "meta": {"cursor": None}}]
    rows = V3Backend(client, version="3.179.0").fetch_scores(TRACE)

    assert [row["id"] for row in rows] == ["s1"]
    assert client.calls[0][0] == "/api/public/v3/scores"
    assert all(path == "/api/public/v3/scores" for path, _ in client.calls)


def test_v3_scores_use_legacy_endpoint_below_3179():
    client = FakeApiClient()
    client.page_bodies["/api/public/scores"] = [{"data": [{"id": "s1"}], "meta": {"totalPages": 1}}]
    rows = V3Backend(client, version="3.150.0").fetch_scores(TRACE)

    assert [row["id"] for row in rows] == ["s1"]
    assert client.calls[0][0] == "/api/public/scores"
    assert "/api/public/v3/scores" not in [path for path, _ in client.calls]


# -- page caps -------------------------------------------------------------
def test_v3_scores_cap_with_live_cursor_sets_truncated(monkeypatch):
    monkeypatch.setattr(backend_v3, "SCORE_MAX_PAGES", 2)
    client = FakeApiClient()
    client.cursor_bodies["/api/public/v3/scores"] = [
        {"data": [{"id": "s1"}], "meta": {"cursor": "c1"}},
        {"data": [{"id": "s2"}], "meta": {"cursor": "c2"}},
    ]
    backend = V3Backend(client, version="3.180.0")
    backend.fetch_scores(TRACE)
    assert backend.truncated is True


def test_v3_scores_cap_with_exhausted_cursor_not_truncated(monkeypatch):
    monkeypatch.setattr(backend_v3, "SCORE_MAX_PAGES", 2)
    client = FakeApiClient()
    client.cursor_bodies["/api/public/v3/scores"] = [
        {"data": [{"id": "s1"}], "meta": {"cursor": "c1"}},
        {"data": [{"id": "s2"}], "meta": {"cursor": None}},
    ]
    backend = V3Backend(client, version="3.180.0")
    backend.fetch_scores(TRACE)
    assert backend.truncated is False


def test_v3_session_cap_sets_truncated(monkeypatch):
    monkeypatch.setattr(backend_v3, "MAX_PAGES", 2)
    client = FakeApiClient()
    full_page = [{"id": f"obs_{index}"} for index in range(PAGE_LIMIT)]
    client.page_bodies["/api/public/observations"] = [
        {"data": list(full_page), "meta": {}},
        {"data": list(full_page), "meta": {}},
    ]
    backend = V3Backend(client)
    rows = backend.fetch_session_observations("sess_1")
    assert len(rows) == 2 * PAGE_LIMIT
    assert backend.truncated is True


# -- forced v3 keeps the detected version ---------------------------------
def test_forced_v3_probes_health_for_version():
    client = FakeApiClient()
    client.health = {"version": "3.181.0"}
    backend = select_backend(client, "v3")

    assert backend.kind == "v3"
    assert backend.version == "3.181.0"
    assert client.calls[0][0] == "/api/public/health"


def test_forced_v3_survives_health_failure():
    client = FakeApiClient()

    def on_get(path, params):
        if path == "/api/public/health":
            raise NetworkError("health unavailable")
        return {"data": [], "meta": {}}

    client.on_get = on_get
    backend = select_backend(client, "v3")
    assert backend.kind == "v3"
    assert backend.version == ""


# -- legacy trace object ---------------------------------------------------
LEGACY_TRACE = {
    "id": TRACE,
    "name": "legacy-name",
    "userId": "legacy-user",
    "sessionId": "legacy-session",
    "tags": ["legacy-tag", "legacy-tag-2"],
    "release": "legacy-release",
    "environment": "legacy-env",
}


def test_v3_legacy_trace_fills_summary_and_is_archived(monkeypatch):
    set_env(monkeypatch)
    backend = FakeBackend(
        rows=[make_row(sessionId="derived-session")],
        legacy_trace=LEGACY_TRACE,
        kind="v3",
        api_version="v3",
    )
    wire_cli_backend(monkeypatch, backend)

    with workdir("v3_legacy") as tmp:
        args = cli.build_parser().parse_args([TRACE, "--out", str(tmp / "out"), "--with-session"])
        result = cli.run_export(args)
        export_dir = Path(result["exportDir"])

        summary = json.loads((export_dir / "trace-summary.json").read_text(encoding="utf-8"))
        manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
        transcript = (export_dir / "transcript.md").read_text(encoding="utf-8")
        legacy_raw = json.loads((export_dir / "raw" / "legacy-trace.json").read_text(encoding="utf-8"))

        assert summary["source"] == "legacy"
        assert summary["name"] == "legacy-name"
        assert summary["userId"] == "legacy-user"
        assert summary["sessionId"] == "legacy-session"
        assert summary["tags"] == ["legacy-tag", "legacy-tag-2"]
        assert summary["release"] == "legacy-release"
        assert summary["environment"] == "legacy-env"
        assert legacy_raw == LEGACY_TRACE
        assert "raw/legacy-trace.json" in transcript
        assert "raw/legacy-trace.json" in [entry["path"] for entry in manifest["files"]]
        assert manifest["trace"]["source"] == "legacy"


def test_v3_export_succeeds_with_no_observation_rows(monkeypatch):
    set_env(monkeypatch)
    backend = FakeBackend(rows=[], legacy_trace=LEGACY_TRACE, kind="v3", api_version="v3")
    wire_cli_backend(monkeypatch, backend)

    with workdir("v3_no_rows") as tmp:
        result = cli.run_export(cli.build_parser().parse_args([TRACE, "--out", str(tmp / "out")]))
        export_dir = Path(result["exportDir"])
        summary = json.loads((export_dir / "trace-summary.json").read_text(encoding="utf-8"))

    assert summary["source"] == "legacy"
    assert summary["observationCount"] == 0


def test_v4_summary_stays_derived(monkeypatch):
    set_env(monkeypatch)
    wire_cli_backend(monkeypatch, FakeBackend(rows=[make_row()]))

    with workdir("v4_derived") as tmp:
        result = cli.run_export(cli.build_parser().parse_args([TRACE, "--out", str(tmp / "out")]))
        export_dir = Path(result["exportDir"])
        summary = json.loads((export_dir / "trace-summary.json").read_text(encoding="utf-8"))

    assert summary["source"] == "derived"
    assert not (export_dir / "raw" / "legacy-trace.json").exists()
