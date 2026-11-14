"""Regression tests for the transcript rendering fixes (task-4 item 12).

Covers the score table escaping (pipes and newlines), the Media table's failure/skip
reason column, the ``--with-session`` raw data row, the v3 legacy raw row, and that the
Media table keeps its six columns.
"""

from __future__ import annotations

from langfuse_export.models import MediaAsset, Score
from langfuse_export.render import render_transcript

from fix_support import TRACE
from helpers import FIXED_EXPORTED_AT, build_bundle


def bundle_with(scores=None, media=None, session_rows=None, legacy_trace=None):
    bundle = build_bundle([], [], with_media=False)
    if scores is not None:
        bundle.scores = scores
        bundle.summary.score_count = len(scores)
    if media is not None:
        bundle.media_assets = media
    if session_rows is not None:
        bundle.session_rows = session_rows
    if legacy_trace is not None:
        bundle.legacy_trace = legacy_trace
    return bundle


def score(**overrides) -> Score:
    base = {
        "id": "score_x",
        "name": "helpfulness",
        "value": 0.5,
        "data_type": "NUMERIC",
        "source": "EVAL",
        "timestamp": "2026-11-07T02:20:00.000Z",
        "subject_kind": "trace",
        "subject_id": TRACE,
        "comment": "ok",
    }
    base.update(overrides)
    return Score(**base)


def score_rows(rendered: str) -> list[str]:
    lines = rendered.splitlines()
    start = lines.index("| name | dataType | value | source | subject | comment |")
    rows = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        rows.append(line)
    return rows


def test_score_cells_escape_pipes_and_newlines():
    broken = score(
        name="a|b",
        value="x|y",
        comment="line1\nline2 | tail",
        subject_kind="observation",
        subject_id="obs|x",
    )
    rendered = render_transcript(bundle_with(scores=[broken]), FIXED_EXPORTED_AT)
    rows = score_rows(rendered)
    assert len(rows) == 1
    row = rows[0]

    assert "a\\|b" in row
    assert "x\\|y" in row
    assert "obs\\|x" in row
    assert "line1 line2 \\| tail" in row
    assert "\n" not in row
    # The escaped pipes must not create extra Markdown columns (6 columns = 7 pipes).
    assert row.count("|") - row.count("\\|") == 7


def test_score_without_comment_renders_dash():
    rendered = render_transcript(
        bundle_with(scores=[score(comment=None)]), FIXED_EXPORTED_AT
    )
    assert "| — |" in score_rows(rendered)[0]


def test_media_table_has_reason_column_and_shows_failure():
    failed = MediaAsset(
        media_id="med_fail",
        content_type="image/png",
        status="failed",
        error="download failed after refresh",
    )
    rendered = render_transcript(bundle_with(media=[failed]), FIXED_EXPORTED_AT)

    assert "| mediaId | 类型 | 大小 | 本地文件 | URL 过期 | 备注 |" in rendered
    assert "download failed after refresh" in rendered
    row = next(line for line in rendered.splitlines() if line.startswith("| `med_fail` |"))
    assert row.count("|") - row.count("\\|") == 7


def test_media_skipped_reason_is_rendered():
    skipped = MediaAsset(
        media_id="med_big",
        content_type="image/png",
        content_length=10**6,
        status="skipped",
        error="contentLength 1000000 exceeds limit",
    )
    rendered = render_transcript(bundle_with(media=[skipped]), FIXED_EXPORTED_AT)
    assert "contentLength 1000000 exceeds limit" in rendered
    assert "1,000,000 B" in rendered


def test_successful_media_row_has_dash_reason():
    downloaded = MediaAsset(
        media_id="med_ok",
        content_type="image/png",
        content_length=3,
        status="downloaded",
        local_path="media/med_ok.png",
    )
    rendered = render_transcript(bundle_with(media=[downloaded]), FIXED_EXPORTED_AT)
    row = next(line for line in rendered.splitlines() if line.startswith("| `med_ok` |"))
    assert row.endswith("| — |")
    assert "`media/med_ok.png`" in row


def test_session_raw_file_is_listed():
    rendered = render_transcript(
        bundle_with(session_rows=[{"id": "obs_session"}]), FIXED_EXPORTED_AT
    )
    assert "`raw/session-observations.jsonl`" in rendered


def test_legacy_trace_raw_file_is_listed():
    rendered = render_transcript(
        bundle_with(legacy_trace={"id": TRACE, "name": "legacy"}),
        FIXED_EXPORTED_AT,
    )
    assert "`raw/legacy-trace.json`" in rendered


def test_session_rows_are_not_rendered_as_observations():
    rendered = render_transcript(
        bundle_with(session_rows=[{"id": "obs_session", "name": "session-secret"}]),
        FIXED_EXPORTED_AT,
    )
    assert "session-secret" not in rendered
