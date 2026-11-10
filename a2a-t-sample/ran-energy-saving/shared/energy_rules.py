"""Deterministic energy-saving business rules shared by the sample's RAN adapters.

The vendor NMS decides feasibility in code, not in the model: a power-outage scenario is
infeasible only when the outage lasts **strictly longer** than four hours **and** a guaranteed
rate is **strictly above** 2 Mbps. Its time convention is also code: an interval that ends on the
whole hour excludes that hour, while an interval ending at ``:59`` includes its hour.

The local RAN precheck and the NMS simulator each implemented one half of that rule, so the same
request could open a negotiation locally and be accepted on the real network. This module is the
single implementation; the evaluation runner uses it as the expected-value oracle.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

#: Outage length above which the feasibility rule fires (strictly greater).
FEASIBILITY_OUTAGE_HOURS: Final[int] = 4

#: Guaranteed rate above which the feasibility rule fires (strictly greater).
FEASIBILITY_RATE_MBPS: Final[float] = 2.0

#: Markers that identify an outage sentence. ``power`` alone is deliberately absent: the fixed
#: Task-T boilerplate mentions "total power consumption" in every request.
_OUTAGE_MARKERS: Final[tuple[str, ...]] = ("outage", "power outage", "停电", "背景事件")

#: One ``HH:MM`` range with any of the separators a request or a Task-T may use.
_TIME_RANGE: Final[re.Pattern[str]] = re.compile(
    r"(\d{1,2}):(\d{2})(?::\d{2})?\s*(?:[~\-–—]|to|until|至)\s*(\d{1,2}):(\d{2})(?::\d{2})?",
    re.IGNORECASE,
)

#: Sentence boundaries used to pick the outage sentence out of a whole Task-T.
_SENTENCE_SPLIT: Final[re.Pattern[str]] = re.compile(r"[\n。；;.!?！？]+")


def minutes_range(value: str) -> tuple[int, int] | None:
    """Return the envelope (earliest start, latest end) of the time ranges in one value.

    A value may carry several ranges (``a & b`` or one prose sentence). The vendor measures the
    envelope, not the covered hours, so a gap widens the measured duration. A value that is the
    single range ``00:00-24:00`` is the full day, whose last hour is 23.

    Args:
        value: text carrying zero or more ``HH:MM`` ranges.

    Returns:
        ``(start, end)`` in minutes from midnight, or ``None`` when nothing parses.
    """
    ranges = [
        (int(start_hour) * 60 + int(start_minute), int(end_hour) * 60 + int(end_minute))
        for start_hour, start_minute, end_hour, end_minute in _TIME_RANGE.findall(value)
    ]
    if not ranges:
        return None
    start = min(item[0] for item in ranges)
    end = max(item[1] for item in ranges)
    if len(ranges) == 1 and start == 0 and end == 24 * 60:
        end = 24 * 60 - 1
    return start, end


def duration_hours(start_minutes: int, end_minutes: int) -> float:
    """Return the vendor's measured duration of one interval.

    An interval that ends on the whole hour excludes that hour, so ``03:00-07:00`` is exactly four
    hours. An interval that ends at ``:59`` includes its hour, so one minute is added to keep the
    two accepted spellings of the same outage equal.

    Args:
        start_minutes: interval start in minutes from midnight.
        end_minutes: interval end in minutes from midnight.

    Returns:
        The duration in hours.
    """
    inclusive_end = 0 if end_minutes % 60 == 0 else 1
    return (end_minutes - start_minutes + inclusive_end) / 60.0


def outage_duration_hours(text: str) -> float | None:
    """Return the duration of the outage window named in one request or Task-T.

    Only the sentence(s) that name the outage are read, so a full-day energy-saving window in the
    same document does not widen the measurement.

    Args:
        text: request or rendered Task-T.

    Returns:
        The outage duration in hours, or ``None`` when the text names no outage or no range.
    """
    lowered = text.lower()
    if not any(marker in lowered for marker in _OUTAGE_MARKERS):
        return None
    sentences = [
        part
        for part in _SENTENCE_SPLIT.split(text)
        if any(marker in part.lower() for marker in _OUTAGE_MARKERS)
    ]
    span = minutes_range(" & ".join(sentences)) if sentences else None
    if span is None:
        return None
    return duration_hours(*span)


def is_outage_infeasible(hours: float | None, rates: Iterable[float | None]) -> bool:
    """Return whether the vendor's feasibility rule fires.

    Both conditions must hold strictly: the outage is longer than four hours and at least one
    guaranteed rate is above 2 Mbps.

    Args:
        hours: outage duration, or ``None`` when the request names no outage window.
        rates: guaranteed rates in Mbps; ``None`` entries are ignored.

    Returns:
        ``True`` when the request must open a feasibility negotiation.
    """
    if hours is None or hours <= FEASIBILITY_OUTAGE_HOURS:
        return False
    return any(rate is not None and rate > FEASIBILITY_RATE_MBPS for rate in rates)


__all__ = [
    "FEASIBILITY_OUTAGE_HOURS",
    "FEASIBILITY_RATE_MBPS",
    "duration_hours",
    "is_outage_infeasible",
    "minutes_range",
    "outage_duration_hours",
]
