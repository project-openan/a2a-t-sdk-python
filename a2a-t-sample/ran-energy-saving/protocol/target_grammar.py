"""The sample's one grammar for a time-segmented guaranteed-throughput target.

Before this module the sample carried seven copies of the same regex in three dialects
(``client_flow``, ``client_policy``, ``grounding``, ``plain_a2a``, ``pre_task_negotiation``,
``pure_a2a`` and the Task-T pre-check). A live run then sent ``{08:00-18:00: 10Mbps}`` — the form
the English slot overlay asks the model to write — while the accept path accepted only
``{08:00~18:00,10Mbps}`` and dropped the negotiated value without a trace.

Every module parses targets through this module now. It reads the notations the repository and the
vendor actually produce (tilde, hyphen, en/em dash, ``to``, Chinese punctuation, one- or two-digit
hours, optional braces) and writes the canonical ``{HH:MM-HH:MM,NMbps}`` form back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

__all__ = [
    "TargetSegment",
    "find_segments",
    "find_window",
    "format_segments",
    "has_segment",
    "normalize_text",
]

#: One window followed by its rate. Prose such as ``08:00 to 18:00`` without a rate never matches,
#: and a rate followed by a window (``10Mbps during 08:00-18:00``) never matches either.
_SEGMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\{?\s*"
    r"(?P<start>\d{1,2}:\d{2})\s*"
    r"(?:~|～|-|–|—|至|到|\bto\b)\s*"
    r"(?P<end>\d{1,2}:\d{2})\s*"
    r"[,:：，、]\s*"
    r"(?P<rate>\d+(?:\.\d+)?)\s*"
    r"Mbps"
    r"\s*\}?",
    re.IGNORECASE,
)


#: One clock window without a rate, e.g. the outage window of a background event.
_WINDOW_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<start>\d{1,2}:\d{2})\s*"
    r"(?:~|～|-|–|—|至|到|\bto\b)\s*"
    r"(?P<end>\d{1,2}:\d{2})",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class TargetSegment:
    """One parsed ``{HH:MM-HH:MM,NMbps}`` segment.

    Attributes:
        start: canonical start clock, ``HH:MM``.
        end: canonical end clock, ``HH:MM``.
        rate_mbps: numeric guaranteed rate in Mbps.
        raw: the matched source text, kept for logs and trace.
    """

    start: str
    end: str
    rate_mbps: float
    raw: str

    @property
    def start_minutes(self) -> int:
        """Return the start clock as minutes from midnight."""
        return _minutes(self.start)

    @property
    def end_minutes(self) -> int:
        """Return the end clock as minutes from midnight."""
        return _minutes(self.end)

    def canonical(self) -> str:
        """Return this segment in the canonical ``{HH:MM-HH:MM,NMbps}`` form."""
        return f"{{{self.start}-{self.end},{self.rate_mbps:g}Mbps}}"


def find_segments(text: object) -> list[TargetSegment]:
    """Return every valid target segment of one text, in source order.

    Args:
        text: any value a caller may hold; non-strings yield no segments.

    Returns:
        The parsed segments; out-of-range clocks are skipped rather than guessed.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    segments: list[TargetSegment] = []
    for match in _SEGMENT_PATTERN.finditer(text):
        start = _canonical_clock(match.group("start"))
        end = _canonical_clock(match.group("end"))
        if start is None or end is None:
            continue
        segments.append(TargetSegment(start, end, float(match.group("rate")), match.group(0)))
    return segments


def format_segments(segments: list[TargetSegment]) -> str:
    """Render segments in the canonical joined form used by Task-T and the accept text."""
    return ", ".join(segment.canonical() for segment in segments)


def normalize_text(text: object) -> str | None:
    """Return the canonical target of one text, or ``None`` when it carries none."""
    segments = find_segments(text)
    if not segments:
        return None
    return format_segments(segments)


def has_segment(text: object) -> bool:
    """Return whether one text carries at least one valid target segment."""
    return bool(find_segments(text))


def find_window(text: object) -> tuple[str, str] | None:
    """Return the first ``(start, end)`` clock window of one text, in any written notation.

    Background events name their window without a rate ("power outage from 08:00 to 18:00"), so
    they cannot use :func:`find_segments`; sharing this parser keeps every module reading the same
    separators (``~ - – — to 至 到``, one- or two-digit hours, optional spaces).
    """
    if not isinstance(text, str) or not text.strip():
        return None
    match = _WINDOW_PATTERN.search(text)
    if match is None:
        return None
    start = _canonical_clock(match.group("start"))
    end = _canonical_clock(match.group("end"))
    if start is None or end is None:
        return None
    return start, end


def _canonical_clock(value: str) -> str | None:
    """Return ``HH:MM`` for a valid clock, ``None`` when the clock is out of range.

    ``24:00`` is a legal clock in this sample: time windows are half-open one-day intervals whose
    end is ``24:00`` (see the slot constraint), so it is not an out-of-range hour.
    """
    hours_text, _, minutes_text = value.partition(":")
    hours = int(hours_text)
    minutes = int(minutes_text)
    if not 0 <= minutes <= 59:
        return None
    if hours == 24 and minutes == 0:
        return "24:00"
    if not 0 <= hours <= 23:
        return None
    return f"{hours:02d}:{minutes:02d}"


def _minutes(clock: str) -> int:
    """Convert one canonical ``HH:MM`` clock to minutes from midnight."""
    hours_text, _, minutes_text = clock.partition(":")
    return int(hours_text) * 60 + int(minutes_text)
