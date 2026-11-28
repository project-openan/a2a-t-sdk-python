"""Regression tests for P1-4 (metadata truncation warning) and P1-5 (warning order),
plus the default-host and /api/public host warnings introduced with the fixes.

Covers task-4 items 6-8 and 7: metadata detection rules, warning + manifest flag +
``--strict``, project lookup failures reaching the manifest, and host warnings.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from langfuse_export import cli
from langfuse_export.client import ApiError
from langfuse_export.normalize import detect_metadata_truncation

from fix_support import TRACE, FakeBackend, make_row, set_env, wire_cli_backend, workdir


def run_cli_export(monkeypatch, tmp: Path, backend: FakeBackend, extra: list[str] | None = None):
    wire_cli_backend(monkeypatch, backend)
    args = cli.build_parser().parse_args([TRACE, "--out", str(tmp / "out"), *(extra or [])])
    result = cli.run_export(args)
    export_dir = Path(result["exportDir"])
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    transcript = (export_dir / "transcript.md").read_text(encoding="utf-8")
    return result, export_dir, manifest, transcript


# -- detection rules --------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("y" * 197 + "...", True),   # 200 chars ending in the ellipsis marker
        ("y" * 200 + "...", True),   # 203 chars, documented alternative shape
        ("y" * 196 + "...", False),  # 199 chars: not a documented truncated length
        ("y" * 200, False),          # legitimate 200-char value, no ellipsis
        ("y" * 203, False),          # 203 chars but no ellipsis
        ("y" * 4214, False),         # real-world untruncated value
    ],
)
def test_metadata_truncation_detection_rules(value, expected):
    assert detect_metadata_truncation([{"metadata": {"note": value}}]) is expected


def test_metadata_truncation_detection_nested():
    row = {"metadata": {"outer": {"inner": ["y" * 197 + "..."]}}}
    assert detect_metadata_truncation([row]) is True


# -- warning + manifest flag + strict --------------------------------------
def test_metadata_truncation_sets_warning_and_manifest_flag(monkeypatch):
    set_env(monkeypatch)
    rows = [make_row(metadata={"note": "y" * 197 + "..."})]
    backend = FakeBackend(rows=rows)

    with workdir("meta_truncated") as tmp:
        result, export_dir, manifest, transcript = run_cli_export(monkeypatch, tmp, backend)

    assert manifest["metadataTruncated"] is True
    assert any("metadata may be truncated" in warning for warning in manifest["warnings"])
    assert any("metadata may be truncated" in warning for warning in result["warnings"])
    # The transcript status line counts warnings, so it must reflect this one.
    assert "warnings" in transcript.split("导出状态")[1].splitlines()[0]


def test_legit_200_char_metadata_does_not_warn(monkeypatch):
    set_env(monkeypatch)
    rows = [make_row(metadata={"note": "y" * 200})]
    backend = FakeBackend(rows=rows)

    with workdir("meta_legit") as tmp:
        result, export_dir, manifest, transcript = run_cli_export(monkeypatch, tmp, backend)

    assert manifest["metadataTruncated"] is False
    assert not any("metadata" in warning for warning in manifest["warnings"])


def test_metadata_truncation_makes_strict_exit_1(monkeypatch):
    set_env(monkeypatch)
    backend = FakeBackend(rows=[make_row(metadata={"note": "y" * 197 + "..."})])
    wire_cli_backend(monkeypatch, backend)

    with workdir("meta_strict") as tmp:
        plain = cli.main([TRACE, "--out", str(tmp / "plain")])
        strict = cli.main([TRACE, "--out", str(tmp / "strict"), "--strict"])

    assert plain == 0
    assert strict == 1


# -- P1-5: project failure warning must reach the manifest ------------------
def test_project_failure_warning_lands_in_manifest(monkeypatch):
    set_env(monkeypatch)
    backend = FakeBackend(rows=[make_row()], project_error=ApiError("HTTP 500", status=500))

    with workdir("project_failure") as tmp:
        result, export_dir, manifest, transcript = run_cli_export(monkeypatch, tmp, backend)

    project_warnings = [w for w in manifest["warnings"] if "project lookup failed" in w]
    assert len(project_warnings) == 1
    assert project_warnings[0].endswith("ApiError")


def test_backend_warnings_are_extended_once(monkeypatch):
    set_env(monkeypatch)
    backend = FakeBackend(rows=[make_row()], project_error=ApiError("HTTP 500", status=500))

    with workdir("project_once") as tmp:
        result = run_cli_export(monkeypatch, tmp, backend)[0]

    assert sum(1 for w in result["warnings"] if "project lookup failed" in w) == 1


# -- host warnings ----------------------------------------------------------
def test_default_host_emits_warning(monkeypatch):
    set_env(monkeypatch, host=None)
    backend = FakeBackend(rows=[make_row()])

    with workdir("host_default") as tmp:
        result, export_dir, manifest, transcript = run_cli_export(monkeypatch, tmp, backend)

    assert manifest["hostSource"] == "default"
    assert manifest["host"] == "https://cloud.langfuse.com"
    assert any("default EU host" in warning for warning in manifest["warnings"])


def test_api_public_suffix_is_normalised_with_warning(monkeypatch):
    set_env(monkeypatch, host="https://cloud.langfuse.com/api/public")
    backend = FakeBackend(rows=[make_row()])

    with workdir("host_normalise") as tmp:
        result, export_dir, manifest, transcript = run_cli_export(monkeypatch, tmp, backend)

    assert manifest["host"] == "https://cloud.langfuse.com"
    assert any("host normalised" in warning for warning in manifest["warnings"])
    assert "/api/public/api/public" not in transcript
