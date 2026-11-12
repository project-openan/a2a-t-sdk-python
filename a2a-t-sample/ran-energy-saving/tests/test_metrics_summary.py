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


def test_weighted_cache_rate_weights_by_prompt_tokens() -> None:
    """One 768/793 hit and one 0/5000 report must not average to 48%."""
    hit = _row("hit", cached=768, rate=0.968)
    hit["tokens"] = {**hit["tokens"], "prompt": 793, "total": 795}  # type: ignore[dict-item]
    miss = _row("miss", cached=0, rate=0.0)
    miss["tokens"] = {**miss["tokens"], "prompt": 5000, "total": 5010}  # type: ignore[dict-item]

    row = summarize_metrics([hit, miss])[0]

    assert row["cacheHitRate"] == 0.484
    assert row["cacheHitRateWeighted"] == 0.1326
    assert row["cacheReportingRuns"] == 2


def test_tokens_per_success_and_per_completed_are_separate() -> None:
    """The tester has no accuracy section, so the completed figure must stand on its own."""
    passed = _row("passed", cached=None, rate=None)
    passed["accuracy"] = {"passed": True, "score": 1.0}
    passed["finalState"] = "task_completed"
    failed = _row("failed", cached=None, rate=None)
    failed["accuracy"] = {"passed": False, "score": 0.0}
    failed["finalState"] = "input_invalid"

    row = summarize_metrics([passed, failed])[0]

    assert row["tokensPerSuccess"] == {
        "tokens": 1010.0,
        "successes": 1,
        "tokenReportingSuccesses": 1,
    }
    assert row["tokensPerCompleted"] == {
        "tokens": 1010.0,
        "successes": 1,
        "tokenReportingSuccesses": 1,
    }


def test_component_buckets_are_summed_across_runs() -> None:
    """A protocol comparison needs the per-component split, not only the blended total."""
    first = _row("a", cached=None, rate=None)
    first["tokens"] = {
        **first["tokens"],
        "byComponent": {
            "client:task_t_slot_extraction": {"prompt": 800, "cached": 700, "calls": 1},
            "client:negotiation": {"prompt": 200, "cached": 0, "calls": 1},
        },
    }
    second = _row("b", cached=None, rate=None)
    second["tokens"] = {
        **second["tokens"],
        "byComponent": {
            "client:task_t_slot_extraction": {"prompt": 200, "cached": 100, "calls": 1},
        },
    }

    components = summarize_metrics([first, second])[0]["components"]

    assert components == {
        "client:negotiation": {
            "prompt": 200,
            "completion": 0,
            "total": 0,
            "cached": 0,
            "calls": 1,
            "cacheHitRate": 0.0,
        },
        "client:task_t_slot_extraction": {
            "prompt": 1000,
            "completion": 0,
            "total": 0,
            "cached": 800,
            "calls": 2,
            "cacheHitRate": 0.8,
        },
    }


def test_cache_summary_is_none_without_any_report() -> None:
    summary = summarize_metrics([_row("a", cached=None, rate=None)])

    row = summary[0]
    tokens = row["tokens"]
    assert isinstance(tokens, dict)
    assert tokens["cached"]["avg"] is None
    assert row["cacheHitRate"] is None
