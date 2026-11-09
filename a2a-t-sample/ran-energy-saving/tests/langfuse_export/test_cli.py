import json
import shutil
import types
from pathlib import Path

import pytest

from langfuse_export import cli
from langfuse_export.client import AuthError, NetworkError, NotFoundError
from langfuse_export.config import ConfigError

TRACE = "9f2c1a7de4b34c0e8a5f2d1c6b9e3a70"


def test_parse_trace_id_from_url():
    trace_id, host = cli.parse_trace_input(
        f"https://cloud.langfuse.com/project/proj_1/traces/{TRACE}?timestamp=1"
    )
    assert trace_id == TRACE
    assert host == "https://cloud.langfuse.com"


def test_parse_bare_trace_id():
    assert cli.parse_trace_input(f"  {TRACE}  ") == (TRACE, None)


def test_parse_invalid_trace_id():
    with pytest.raises(ConfigError):
        cli.parse_trace_input("not a trace id!")


def test_exit_code_mapping(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")

    cases = [
        (ConfigError("bad config"), 1),
        (AuthError("bad key"), 3),
        (NotFoundError("missing"), 2),
        (NetworkError("boom"), 4),
    ]
    for exc, expected in cases:
        def explode(*args, _exc=exc, **kwargs):
            raise _exc

        monkeypatch.setattr(cli, "run_export", explode)
        assert cli.main([TRACE]) == expected


def test_strict_exit_code(monkeypatch):
    monkeypatch.setattr(cli, "run_export", lambda args: {"warnings": ["w"], "traceId": TRACE})
    assert cli.main([TRACE]) == 0
    assert cli.main([TRACE, "--strict"]) == 1


class FakeBackend:
    kind = "v4"
    api_version = "v4"

    def __init__(self, rows, scores):
        self.rows = rows
        self.scores = scores
        self.warnings = []
        self.client = types.SimpleNamespace()

    def fetch_observations(self, *args, **kwargs):
        return self.rows

    def fetch_scores(self, *args, **kwargs):
        return self.scores

    def fetch_project(self):
        return "project_1", "My Project"

    def fetch_media(self, *args, **kwargs):
        raise AssertionError("no media expected")

    def fetch_session_observations(self, *args, **kwargs):
        return []


def test_run_export_writes_layout(monkeypatch, sample_rows, sample_scores):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
    monkeypatch.setattr(
        cli, "select_backend", lambda client, forced: FakeBackend(sample_rows, sample_scores)
    )

    out_root = Path("tmp_test_exports")
    shutil.rmtree(out_root, ignore_errors=True)
    try:
        args = cli.build_parser().parse_args([TRACE, "--out", str(out_root), "--no-media", "--quiet"])
        result = cli.run_export(args)

        export_dir = Path(result["exportDir"])
        assert (export_dir / "transcript.md").is_file()
        assert (export_dir / "trace-summary.json").is_file()
        assert (export_dir / "manifest.json").is_file()
        raw = (export_dir / "raw" / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(raw) == len(sample_rows)
        assert result["observations"] == len(sample_rows)

        manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
        paths = [entry["path"] for entry in manifest["files"]]
        assert "transcript.md" in paths
        assert "manifest.json" not in paths
        assert all(entry["sha256"] for entry in manifest["files"])
        assert manifest["trace"]["traceId"] == TRACE
        summary = json.loads((export_dir / "trace-summary.json").read_text(encoding="utf-8"))
        assert summary["source"] == "derived"
    finally:
        shutil.rmtree(out_root, ignore_errors=True)
