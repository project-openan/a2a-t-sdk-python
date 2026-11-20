"""Regression tests for the CLI contract fixes (P2 batch) and the page-cap rules.

Covers task-4 items 9-10: ``--quiet`` / ``--quiet --json`` / ``--json`` output,
URL percent-decoding, v4 page-cap truncation boundaries (exactly 50 pages with an
exhausted cursor is NOT truncation, a still-live cursor at the cap is), v4 session
truncation, ``--with-session`` raw output, and the truncated warning wiring.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from langfuse_export import backend_v4, cli
from langfuse_export.backend_v4 import V4Backend
from langfuse_export.config import ConfigError

from fix_support import (
    TRACE,
    FakeBackend,
    FakeResponse,
    install_session_router,
    make_client,
    make_row,
    set_env,
    wire_cli_backend,
    workdir,
)


# -- output contracts ------------------------------------------------------
def test_quiet_prints_only_export_dir(monkeypatch, capsys):
    set_env(monkeypatch)
    wire_cli_backend(monkeypatch, FakeBackend(rows=[make_row()]))

    with workdir("quiet") as tmp:
        code = cli.main([TRACE, "--out", str(tmp / "out"), "--quiet"])
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert code == 0
        assert len(lines) == 1
        assert Path(lines[0]).is_dir()
        assert "observations=" not in out
        assert "warning:" not in out


def test_quiet_wins_over_json(monkeypatch, capsys):
    set_env(monkeypatch)
    wire_cli_backend(monkeypatch, FakeBackend(rows=[make_row()]))

    with workdir("quiet_json") as tmp:
        code = cli.main([TRACE, "--out", str(tmp / "out"), "--quiet", "--json"])
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert code == 0
        assert len(lines) == 1
        assert Path(lines[0]).is_dir()
        assert not out.strip().startswith("{")
        with pytest.raises(json.JSONDecodeError):
            json.loads(out)


def test_json_output_is_ascii_and_parsable(monkeypatch, capsys):
    set_env(monkeypatch)
    wire_cli_backend(monkeypatch, FakeBackend(rows=[make_row()]))

    with workdir("json_out") as tmp:
        code = cli.main([TRACE, "--out", str(tmp / "out"), "--json"])
        out = capsys.readouterr().out
        assert code == 0
        payload = json.loads(out)
        assert payload["traceId"] == TRACE
        assert "exportDir" in payload
        assert out.isascii()


# -- URL parsing -----------------------------------------------------------
def test_parse_trace_url_decodes_percent_encoding():
    trace_id, host = cli.parse_trace_input(
        "https://cloud.langfuse.com/project/proj_1/traces/abc%2DDEF%5F123?timestamp=1"
    )
    assert trace_id == "abc-DEF_123"
    assert host == "https://cloud.langfuse.com"


def test_parse_trace_url_rejects_decoded_invalid_id():
    with pytest.raises(ConfigError):
        cli.parse_trace_input("https://cloud.langfuse.com/traces/abc%20def")


def test_parse_percent_encoded_query_is_ignored():
    trace_id, host = cli.parse_trace_input(
        f"https://us.cloud.langfuse.com/traces/{TRACE}?filter=%2Fagent"
    )
    assert trace_id == TRACE
    assert host == "https://us.cloud.langfuse.com"


# -- v4 page caps ----------------------------------------------------------
def page_router(monkeypatch, last_page_has_cursor: bool):
    """Serve one cursor page per request; page 50 ends or still hands out a cursor."""
    state = {"page": 0}

    def router(url, params=None):
        page = state["page"]
        state["page"] += 1
        has_cursor = page < 49 or last_page_has_cursor
        return FakeResponse(
            200,
            {"data": [{"id": f"obs_{page}"}], "meta": {"cursor": f"c{page + 1}" if has_cursor else None}},
        )

    client = make_client(monkeypatch)
    install_session_router(monkeypatch, client, router)
    return client


def test_exactly_50_pages_with_exhausted_cursor_is_not_truncated(monkeypatch):
    client = page_router(monkeypatch, last_page_has_cursor=False)
    backend = V4Backend(client)
    rows = backend.fetch_observations(TRACE)

    assert len(rows) == 50
    assert backend.truncated is False


def test_50_pages_with_live_cursor_is_truncated(monkeypatch):
    client = page_router(monkeypatch, last_page_has_cursor=True)
    backend = V4Backend(client)
    rows = backend.fetch_observations(TRACE)

    assert len(rows) == 50
    assert backend.truncated is True


def test_v4_session_cap_sets_truncated(monkeypatch):
    monkeypatch.setattr(backend_v4, "OBSERVATION_MAX_PAGES", 2)

    def live_router(url, params=None):
        return FakeResponse(200, {"data": [{"id": "obs"}], "meta": {"cursor": "next"}})

    client = make_client(monkeypatch)
    install_session_router(monkeypatch, client, live_router)
    backend = V4Backend(client)
    backend.fetch_session_observations("sess_1")
    assert backend.truncated is True


def test_v4_session_cap_with_exhausted_cursor_is_not_truncated(monkeypatch):
    monkeypatch.setattr(backend_v4, "OBSERVATION_MAX_PAGES", 2)
    state = {"page": 0}

    def ending_router(url, params=None):
        page = state["page"]
        state["page"] += 1
        cursor = "next" if page == 0 else None
        return FakeResponse(200, {"data": [{"id": "obs"}], "meta": {"cursor": cursor}})

    client = make_client(monkeypatch)
    install_session_router(monkeypatch, client, ending_router)
    backend = V4Backend(client)
    assert len(backend.fetch_session_observations("sess_1")) == 2
    assert backend.truncated is False


# -- with-session + truncated wiring --------------------------------------
def test_with_session_writes_raw_file_and_lists_it_in_transcript(monkeypatch):
    set_env(monkeypatch)
    rows = [make_row(sessionId="sess_1")]
    session_rows = [make_row(id="obs_session_2", sessionId="sess_1")]
    wire_cli_backend(monkeypatch, FakeBackend(rows=rows, session_rows=session_rows))

    with workdir("with_session") as tmp:
        args = cli.build_parser().parse_args([TRACE, "--out", str(tmp / "out"), "--with-session"])
        result = cli.run_export(args)
        export_dir = Path(result["exportDir"])
        transcript = (export_dir / "transcript.md").read_text(encoding="utf-8")
        manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))

        assert (export_dir / "raw" / "session-observations.jsonl").is_file()
        assert "raw/session-observations.jsonl" in transcript
        assert manifest["sessionExported"] is True


def test_truncated_backend_flag_reaches_manifest_and_warning(monkeypatch):
    set_env(monkeypatch)
    wire_cli_backend(monkeypatch, FakeBackend(rows=[make_row()], truncated=True))

    with workdir("truncated_flag") as tmp:
        args = cli.build_parser().parse_args([TRACE, "--out", str(tmp / "out")])
        result = cli.run_export(args)
        export_dir = Path(result["exportDir"])
        manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["truncated"] is True
    assert any("page limit reached" in warning for warning in manifest["warnings"])
