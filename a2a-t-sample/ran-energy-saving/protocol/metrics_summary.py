"""Aggregation of per-run metrics for the comparison page and the benchmark report.

The stored metrics are one row per run. This module groups them by protocol/scenario and computes
the values an analysis needs: success rate, token averages, latency averages/p95 and negotiation
rounds. It is a pure function of the metric documents, so it is reused by the Web API, the CSV
export and the benchmark.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence

#: Token fields summarized per group. ``cached`` is ``None`` for providers that never report it.
TOKEN_FIELDS: tuple[str, ...] = ("prompt", "completion", "total", "cached")


def summarize_metrics(
    rows: Iterable[Mapping[str, object]],
    *,
    group_by: Sequence[str] = ("protocol", "scenario"),
) -> list[dict[str, object]]:
    """Group metric documents and compute the comparison summary.

    Args:
        rows: Metric documents, one per run.
        group_by: Top-level metric fields to group by.

    Returns:
        One summary row per group, sorted by the group keys.
    """
    groups: dict[tuple[str, ...], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        key = tuple(str(row.get(field) or "") for field in group_by)
        groups[key].append(row)
    summary: list[dict[str, object]] = []
    for key in sorted(groups):
        group_rows = groups[key]
        entry: dict[str, object] = {
            field: key[index] for index, field in enumerate(group_by)
        }
        entry.update(_group_summary(group_rows))
        summary.append(entry)
    return summary


def summarize_protocols(rows: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    """Summarize every run by protocol only (the headline comparison)."""
    return summarize_metrics(rows, group_by=("protocol",))


def _group_summary(rows: list[Mapping[str, object]]) -> dict[str, object]:
    """Compute one group's aggregates."""
    total = len(rows)
    passed = sum(1 for row in rows if _accuracy(row).get("passed") is True)
    scores = [
        value
        for value in (_number(_accuracy(row).get("score")) for row in rows)
        if value is not None
    ]
    durations = _latency_values(rows, "totalMs")
    models = _latency_values(rows, "modelMs")
    approvals = _latency_values(rows, "approvalWaitMs")
    tokens = {
        field: _token_values(rows, field) for field in TOKEN_FIELDS
    }
    token_available = sum(
        1 for row in rows if _tokens(row).get("available") is True
    )
    rounds = [
        value
        for value in (_number(_negotiation(row).get("rounds")) for row in rows)
        if value is not None
    ]
    cached_sum, cached_prompt_sum, cache_reporting = _cache_totals(rows)
    return {
        "runs": total,
        "passed": passed,
        "successRate": round(passed / total, 4) if total else None,
        "accuracy": round(sum(scores) / len(scores), 4) if scores else None,
        "tokenAvailableRuns": token_available,
        "cacheHitRate": _avg(_cache_hit_rates(rows)),
        # A token-weighted rate: the per-run average above is dominated by runs that made a
        # single cache-reporting call.
        "cacheHitRateWeighted": (
            round(cached_sum / cached_prompt_sum, 4) if cached_prompt_sum else None
        ),
        "cacheReportingRuns": cache_reporting,
        # Cost per outcome. ``success`` uses the accuracy check when the row has one; the tester
        # passes no expectation, so a second figure keyed on the task terminal state is reported
        # alongside it rather than silently mixing the two definitions.
        "tokensPerSuccess": _tokens_per(rows, _accuracy_passed),
        "tokensPerCompleted": _tokens_per(rows, _task_completed),
        # ``side:stage`` buckets were computed per run but never surfaced; a protocol comparison
        # needs them to show which component actually paid for the difference.
        "components": _component_summary(rows),
        "tokens": {
            field: {
                "avg": _avg(values),
                "sum": sum(values) if values else None,
                "samples": len(values),
            }
            for field, values in tokens.items()
        },
        "latency": {
            field: {
                "avg": _avg(values),
                "p95": _percentile(values, 0.95),
                "samples": len(values),
            }
            for field, values in (
                ("totalMs", durations),
                ("modelMs", models),
                ("approvalWaitMs", approvals),
            )
        },
        "avgRounds": _avg(rounds),
    }


def _accuracy(row: Mapping[str, object]) -> dict[str, object]:
    """Return one row's accuracy section."""
    return _section(row.get("accuracy"))


def _tokens(row: Mapping[str, object]) -> dict[str, object]:
    """Return one row's token section."""
    return _section(row.get("tokens"))


def _negotiation(row: Mapping[str, object]) -> dict[str, object]:
    """Return one row's negotiation section."""
    return _section(row.get("negotiation"))


def _section(value: object) -> dict[str, object]:
    """Return one nested section as a mapping."""
    return dict(value) if isinstance(value, Mapping) else {}


def _latency_values(rows: Iterable[Mapping[str, object]], field: str) -> list[float]:
    """Return one latency field from every row that has it."""
    values: list[float] = []
    for row in rows:
        value = _number(_section(row.get("latency")).get(field))
        if value is not None:
            values.append(value)
    return values


def _token_values(rows: Iterable[Mapping[str, object]], field: str) -> list[float]:
    """Return one token field from every row that reported usage."""
    values: list[float] = []
    for row in rows:
        tokens = _tokens(row)
        if tokens.get("available") is not True:
            continue
        value = _number(tokens.get(field))
        if value is not None:
            values.append(value)
    return values


def _cache_hit_rates(rows: Iterable[Mapping[str, object]]) -> list[float]:
    """Return the reported cache hit rate of every run that has one."""
    values: list[float] = []
    for row in rows:
        tokens = _tokens(row)
        if tokens.get("available") is not True:
            continue
        value = _number(tokens.get("cacheHitRate"))
        if value is not None:
            values.append(value)
    return values


def _cache_totals(rows: Iterable[Mapping[str, object]]) -> tuple[float, float, int]:
    """Return ``(cached, prompt, runs)`` over the runs that reported a cache field.

    A run that never reported ``cached`` is excluded from both sides, otherwise an unreported
    cache would look like a miss and drag the weighted rate down.
    """
    cached_total = 0.0
    prompt_total = 0.0
    reporting = 0
    for row in rows:
        tokens = _tokens(row)
        if tokens.get("available") is not True:
            continue
        cached = _number(tokens.get("cached"))
        prompt = _number(tokens.get("prompt"))
        if cached is None or prompt is None:
            continue
        cached_total += cached
        prompt_total += prompt
        reporting += 1
    return cached_total, prompt_total, reporting


def _component_summary(rows: Iterable[Mapping[str, object]]) -> dict[str, dict[str, object]]:
    """Sum the per-run ``side:stage`` token buckets across the group."""
    totals: dict[str, dict[str, float]] = defaultdict(
        lambda: {"prompt": 0.0, "completion": 0.0, "total": 0.0, "cached": 0.0, "calls": 0.0}
    )
    for row in rows:
        tokens = _tokens(row)
        components = tokens.get("byComponent")
        if not isinstance(components, Mapping):
            continue
        for key, bucket in components.items():
            if not isinstance(bucket, Mapping):
                continue
            target = totals[str(key)]
            for field in ("prompt", "completion", "total", "cached", "calls"):
                value = _number(bucket.get(field))
                if value is not None:
                    target[field] += value
    summary: dict[str, dict[str, object]] = {}
    for key in sorted(totals):
        bucket = totals[key]
        prompt = bucket["prompt"]
        summary[key] = {
            "prompt": int(bucket["prompt"]),
            "completion": int(bucket["completion"]),
            "total": int(bucket["total"]),
            "cached": int(bucket["cached"]),
            "calls": int(bucket["calls"]),
            "cacheHitRate": round(bucket["cached"] / prompt, 4) if prompt else None,
        }
    return summary


def _accuracy_passed(row: Mapping[str, object]) -> bool:
    """Return whether one row's accuracy section explicitly passed."""
    return _accuracy(row).get("passed") is True


def _task_completed(row: Mapping[str, object]) -> bool:
    """Return whether one row reached the completed task terminal state."""
    return row.get("finalState") == "task_completed"


def _tokens_per(
    rows: list[Mapping[str, object]], succeeded: Callable[[Mapping[str, object]], bool]
) -> dict[str, object]:
    """Return total tokens divided by the number of rows that met ``succeeded``."""
    winners = [row for row in rows if succeeded(row)]
    spent = 0.0
    counted = 0
    for row in winners:
        tokens = _tokens(row)
        if tokens.get("available") is not True:
            continue
        total = _number(tokens.get("total"))
        if total is None:
            continue
        spent += total
        counted += 1
    return {
        "tokens": round(spent / len(winners), 1) if winners and counted else None,
        "successes": len(winners),
        "tokenReportingSuccesses": counted,
    }


def _number(value: object) -> float | None:
    """Return a numeric value, ``None`` for booleans/non-numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _avg(values: Sequence[float]) -> float | None:
    """Return the arithmetic mean rounded to three decimals."""
    return round(sum(values) / len(values), 3) if values else None


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    """Return a nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return round(ordered[index], 3)


__all__ = ["TOKEN_FIELDS", "summarize_metrics", "summarize_protocols"]
