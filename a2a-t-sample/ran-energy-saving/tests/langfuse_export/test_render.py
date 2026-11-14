from pathlib import Path

from langfuse_export.render import render_transcript

from helpers import FIXED_EXPORTED_AT, build_bundle

ROOT = Path(__file__).resolve().parent


def test_golden_transcript(sample_rows, sample_scores):
    bundle = build_bundle(sample_rows, sample_scores)
    rendered = render_transcript(bundle, FIXED_EXPORTED_AT)
    golden = (ROOT / "samples" / "transcript.md").read_text(encoding="utf-8")
    assert rendered == golden


def test_transcript_is_lf_only(sample_rows, sample_scores):
    bundle = build_bundle(sample_rows, sample_scores)
    rendered = render_transcript(bundle, FIXED_EXPORTED_AT)
    assert "\r" not in rendered
    assert rendered.endswith("\n")


def test_long_input_is_folded(sample_rows, sample_scores):
    bundle = build_bundle(sample_rows, sample_scores)
    rendered = render_transcript(bundle, FIXED_EXPORTED_AT)
    assert "[truncated: 4,000 of 9,318 chars shown]" in rendered


def test_media_row_and_extension(sample_rows, sample_scores):
    bundle = build_bundle(sample_rows, sample_scores)
    rendered = render_transcript(bundle, FIXED_EXPORTED_AT)
    assert "media/med_7c3a91f2.png" in rendered
    assert "18,432 B" in rendered


def test_no_cost_renders_na():
    bundle = build_bundle([], [])
    rendered = render_transcript(bundle, FIXED_EXPORTED_AT)
    assert "| 成本 | n/a |" in rendered
