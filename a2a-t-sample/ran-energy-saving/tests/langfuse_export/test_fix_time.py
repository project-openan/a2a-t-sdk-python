"""Regression tests for P1-3: rendering and the manifest must keep the given offset.

The golden sample is generated with ``FIXED_EXPORTED_AT`` (+08:00); the renderer and
the manifest used to call ``.astimezone()`` which silently converted that value to the
machine's local zone. These tests inject a +05:30 clock and assert the exact offset
survives, which is independent of the machine timezone (this host is +08:00, so the
old implementation fails the assertions regardless of where the suite runs).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from langfuse_export import cli
from langfuse_export.render import render_transcript
from langfuse_export.writer import build_manifest

from fix_support import TRACE, FakeBackend, make_row, set_env, wire_cli_backend, workdir
from helpers import FIXED_EXPORTED_AT, build_bundle

OFFSET_0530 = datetime(2026, 11, 7, 10, 20, 11, tzinfo=timezone(timedelta(hours=5, minutes=30)))
OFFSET_0530_TEXT = "2026-11-07T10:20:11+05:30"


def test_render_keeps_injected_offset(sample_rows, sample_scores):
    bundle = build_bundle(sample_rows, sample_scores)
    rendered = render_transcript(bundle, OFFSET_0530)

    assert f"| 导出时间 | {OFFSET_0530_TEXT} |" in rendered
    # A local-time conversion (the old behaviour) would rewrite the offset to +08:00.
    assert "+08:00" not in rendered


def test_render_golden_clock_offset_is_fixed(sample_rows, sample_scores):
    bundle = build_bundle(sample_rows, sample_scores)
    rendered = render_transcript(bundle, FIXED_EXPORTED_AT)
    assert "| 导出时间 | 2026-11-07T10:20:11+08:00 |" in rendered


def test_manifest_keeps_injected_offset(sample_rows, sample_scores):
    bundle = build_bundle(sample_rows, sample_scores)
    with workdir("manifest_offset") as tmp:
        manifest = build_manifest(tmp, bundle, OFFSET_0530, "env")
    assert manifest["exportedAt"] == OFFSET_0530_TEXT


def test_run_export_preserves_injected_exported_at(monkeypatch):
    set_env(monkeypatch)
    backend = FakeBackend(rows=[make_row()])
    wire_cli_backend(monkeypatch, backend)

    with workdir("run_export_offset") as tmp:
        args = cli.build_parser().parse_args([TRACE, "--out", str(tmp / "out")])
        result = cli.run_export(args, exported_at=OFFSET_0530)

        export_dir = Path(result["exportDir"])
        assert export_dir.name.endswith("_20261107-102011")
        manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
        transcript = (export_dir / "transcript.md").read_text(encoding="utf-8")

    assert manifest["exportedAt"] == OFFSET_0530_TEXT
    assert f"| 导出时间 | {OFFSET_0530_TEXT} |" in transcript
