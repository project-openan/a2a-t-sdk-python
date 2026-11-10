"""Tests for the one canonical throughput-target grammar of the sample.

The sample used to carry seven copies of this regex in three dialects; a live run then sent
``{08:00-18:00: 10Mbps}`` while the accept path only accepted ``{08:00~18:00,10Mbps}`` and dropped
the negotiated value without a word. These tests pin the one parser every module must share.
"""

from __future__ import annotations

import pytest
from protocol.target_grammar import (
    TargetSegment,
    find_segments,
    format_segments,
    has_segment,
    normalize_text,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{08:00~18:00,2Mbps}", ["{08:00~18:00,2Mbps}"]),
        ("{08:00-18:00,2Mbps}", ["{08:00~18:00,2Mbps}"]),
        ("{08:00-18:00: 10Mbps}", ["{08:00~18:00,10Mbps}"]),
        ("{08:00 ~ 18:00 , 2 Mbps}", ["{08:00~18:00,2Mbps}"]),
        ("{8:00-18:00, 2Mbps}", ["{08:00~18:00,2Mbps}"]),
        ("{08:00–18:00,2Mbps}", ["{08:00~18:00,2Mbps}"]),
        ("{08:00—18:00,2Mbps}", ["{08:00~18:00,2Mbps}"]),
        ("08:00 to 18:00, 2Mbps", ["{08:00~18:00,2Mbps}"]),
        ("{08:00~18:00，2Mbps}", ["{08:00~18:00,2Mbps}"]),
        ("{08:00~18:00、2Mbps}", ["{08:00~18:00,2Mbps}"]),
        (
            "{00:00~06:00、10Mbps}、{06:00~18:00、2Mbps}",
            ["{00:00~06:00,10Mbps}", "{06:00~18:00,2Mbps}"],
        ),
        ("Time-segmented rate guarantee target: {08:00-18:00: 10Mbps}", ["{08:00~18:00,10Mbps}"]),
        (
            "{00:00-07:00,2Mbps}, {07:00~23:00, 20Mbps}",
            ["{00:00~07:00,2Mbps}", "{07:00~23:00,20Mbps}"],
        ),
    ],
)
def test_the_grammar_reads_every_notation_the_repository_writes(text: str, expected: list[str]) -> None:
    """A value written in any dialect the sample produces parses to the canonical form."""
    assert format_segments(find_segments(text)) == ", ".join(expected)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "power outage from 08:00 to 18:00",
        "Reduce rate target to 2Mbps or less.",
        "10Mbps during the 08:00-18:00 period",
        "25:00~26:00,2Mbps",
        "08:00~18:00,2Gbps",
    ],
)
def test_the_grammar_does_not_invent_a_segment_from_prose(text: str) -> None:
    """Only a window followed by a Mbps rate is a segment; prose stays prose."""
    assert find_segments(text) == []
    assert normalize_text(text) is None
    assert has_segment(text) is False


def test_segments_expose_clock_values_and_rate() -> None:
    """Callers compare windows and rates through the parsed numbers, not their own regex groups."""
    segment = find_segments("{08:30-18:45: 2.5Mbps}")[0]
    assert segment == TargetSegment(start="08:30", end="18:45", rate_mbps=2.5, raw="{08:30-18:45: 2.5Mbps}")
    assert segment.start_minutes == 510
    assert segment.end_minutes == 1125


def test_normalize_text_keeps_the_canonical_join() -> None:
    """A multi-window value normalizes to the joined canonical form."""
    assert normalize_text(" {00:00-07:00,2Mbps} , {07:00~24:00,10Mbps} ") == (
        "{00:00~07:00,2Mbps}, {07:00~24:00,10Mbps}"
    )
