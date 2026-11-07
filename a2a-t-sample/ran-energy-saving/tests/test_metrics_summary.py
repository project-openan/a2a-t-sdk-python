"""Tests of the cache-aware aggregation of stored run metrics."""

from __future__ import annotations

from protocol.metrics_summary import summarize_metrics


def _row(run_id: str, *, cached: int | None, rate: float | None) -> dict[str, object]:
    """Build one stored metric document with a cache section."""
    return {
        "runId": run_id,
        "protocol": "a2a-t",
        "scenario": "night",
        "tokens": {
            "available": True,
            "prompt": 1000,
            "completion": 10,
            "total": 1010,
            "cached": cached,
            "cacheHitRate": rate,
        },
        "latency": {},
        "accuracy": {},
        "negotiation": {},
    }


def test_cache_average_skips_runs_that_did_not_report_it() -> None:
    summary = summarize_metrics(
        [_row("a", cached=768, rate=0.768), _row("b", cached=None, rate=None)]
    )

    row = summary[0]
    tokens = row["tokens"]
    assert isinstance(tokens, dict)
    assert tokens["cached"]["avg"] == 768
    assert tokens["cached"]["samples"] == 1
    assert row["cacheHitRate"] == 0.768


def test_cache_summary_is_none_without_any_report() -> None:
    summary = summarize_metrics([_row("a", cached=None, rate=None)])

    row = summary[0]
    tokens = row["tokens"]
    assert isinstance(tokens, dict)
    assert tokens["cached"]["avg"] is None
    assert row["cacheHitRate"] is None
