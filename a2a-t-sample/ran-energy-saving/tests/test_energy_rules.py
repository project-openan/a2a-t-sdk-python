"""Tests of the shared deterministic energy-saving business rules."""

from __future__ import annotations

from shared.energy_rules import (
    duration_hours,
    is_outage_infeasible,
    minutes_range,
    outage_duration_hours,
)


def test_a_range_is_measured_to_its_envelope() -> None:
    assert minutes_range("08:00-18:00") == (480, 1080)
    # Two windows with a gap span their envelope, exactly as the vendor measures them.
    assert minutes_range("08:00-10:00&15:00-18:00") == (480, 1080)


def test_the_full_day_ends_at_its_last_hour() -> None:
    assert minutes_range("00:00-24:00") == (0, 1439)


def test_an_end_on_the_hour_excludes_that_hour() -> None:
    assert duration_hours(180, 420) == 4.0
    # The expanded spelling of the same interval must measure the same.
    assert duration_hours(180, 419) == 4.0
    assert duration_hours(180, 421) > 4.0


def test_feasibility_requires_both_thresholds_strictly() -> None:
    assert is_outage_infeasible(4.0, [10.0]) is False
    assert is_outage_infeasible(4.1, [2.0]) is False
    assert is_outage_infeasible(4.1, [None]) is False
    assert is_outage_infeasible(4.1, [2.1]) is True
    assert is_outage_infeasible(None, [10.0]) is False


def test_only_the_outage_sentence_is_measured() -> None:
    """A full-day activation window in the same request must not widen the outage."""
    text = "Energy saving time range: 00:00-24:00. Power outage from 08:00 to 10:00."
    assert outage_duration_hours(text) == 2.0


def test_a_request_without_an_outage_has_no_duration() -> None:
    assert outage_duration_hours("Energy saving time range: 00:00-24:00.") is None


def test_a_chinese_outage_is_measured() -> None:
    assert outage_duration_hours("停电 08:00 至 18:00，保障速率 10Mbps。") == 10.0
