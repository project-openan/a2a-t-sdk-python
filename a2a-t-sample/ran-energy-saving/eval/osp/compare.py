"""Normalized comparisons of the OSP expected intent and Task-T prompt.

The expected values in the OSP workbook use a notation the SDK does not have to reproduce
character for character (``10 Mbps`` vs ``10Mbps``, ``08:00-12:00`` vs the expanded hourly form).
The comparators therefore normalize the business meaning first and report one diff per field or
section, leaving the raw values in the report for inspection.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from eval.osp.intent_schema import OSP_INTENT_KEYS

#: Task-T sections compared exactly.
EXACT_TASK_T_SECTIONS: Final[tuple[str, ...]] = (
    "Operation Type",
    "Task Type",
    "Task Object",
    "Task Target",
)

#: Task-T sections compared after text normalization.
NORMALIZED_TASK_T_SECTIONS: Final[tuple[str, ...]] = ("Task Context",)

#: Task-T sections kept only for human inspection (fixed template prose).
RECORD_ONLY_TASK_T_SECTIONS: Final[tuple[str, ...]] = (
    "Task Description",
    "Terminology Explanation",
    "Expected Output",
)

#: Every section a converted input must carry.
TASK_T_SECTIONS: Final[tuple[str, ...]] = (
    "Operation Type",
    "Task Type",
    "Task Description",
    "Task Object",
    "Task Target",
    "Task Context",
    "Terminology Explanation",
    "Expected Output",
)

_SECTION_RE: Final[re.Pattern[str]] = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_TIME_PAIR_RE: Final[re.Pattern[str]] = re.compile(
    r"(\d{1,2}):(\d{2})(?::\d{2})?\s*(?:-|~|–|—|to)\s*(\d{1,2}):(\d{2})(?::\d{2})?"
)
_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"([0-9]+(?:\.[0-9]+)?)")
_RATE_RE: Final[re.Pattern[str]] = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:Mbps|Mbit/s|Mbit|M)\b", re.IGNORECASE
)

_RAT_ALIASES: Final[dict[str, str]] = {
    "ALL": "ALL",
    "LTE": "LTE",
    "4G": "LTE",
    "E-UTRAN": "LTE",
    "EUTRAN": "LTE",
    "NR": "NR",
    "5G": "NR",
    "NG-RAN": "NR",
    "NGRAN": "NR",
    "LNR": "ALL",
}

_SITE_PREFIXES: Final[tuple[str, ...]] = ("ne ", "base station ", "site ")


@dataclass(frozen=True)
class Diff:
    """One field or section whose normalized expected and actual values differ."""

    path: str
    expected: object
    actual: object
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation used by the report."""
        return {
            "path": self.path,
            "expected": self.expected,
            "actual": self.actual,
            "reason": self.reason,
        }


def parse_sections(text: str) -> dict[str, str]:
    """Split one Task-T prompt into ``## Heading`` sections."""
    matches = list(_SECTION_RE.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1).strip()] = text[start:end].strip()
    return sections


def compare_intent(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> list[Diff]:
    """Compare the 11 expected intent keys after per-key normalization."""
    diffs: list[Diff] = []
    for key in OSP_INTENT_KEYS:
        expected_value = expected.get(key)
        actual_value = actual.get(key)
        if _normalize_intent_value(key, expected_value) != _normalize_intent_value(key, actual_value):
            diffs.append(
                Diff(
                    path=f"intent.{key}",
                    expected=expected_value,
                    actual=actual_value,
                )
            )
    return diffs


def compare_task_t(expected_text: str, actual_text: str) -> list[Diff]:
    """Compare the expected and actual Task-T prompts section by section."""
    expected = parse_sections(expected_text)
    actual = parse_sections(actual_text)
    diffs: list[Diff] = []
    for section in TASK_T_SECTIONS:
        if section not in actual:
            diffs.append(
                Diff(
                    path=f"task_t.{section}",
                    expected=expected.get(section, ""),
                    actual="<missing>",
                    reason="section missing from the generated Task-T",
                )
            )
    for section in EXACT_TASK_T_SECTIONS:
        if _normalize_section(section, expected.get(section, "")) != _normalize_section(
            section, actual.get(section, "")
        ):
            diffs.append(
                Diff(
                    path=f"task_t.{section}",
                    expected=expected.get(section, ""),
                    actual=actual.get(section, ""),
                )
            )
    for section in NORMALIZED_TASK_T_SECTIONS:
        if not _contexts_match(expected.get(section, ""), actual.get(section, "")):
            diffs.append(
                Diff(
                    path=f"task_t.{section}",
                    expected=expected.get(section, ""),
                    actual=actual.get(section, ""),
                    reason="normalized text differs",
                )
            )
    return diffs


def normalize_task_context(text: str) -> str:
    """Normalize the free-form Task Context for comparison.

    The SDK and the workbook describe the same context with different template labels
    (``Rate guarantee goal:``) and separators (``;`` vs a newline).  Stripping those labels from
    both sides keeps the comparison focused on the business values.
    """
    value = str(text or "")
    value = value.replace("：", ":").replace("，", ",").replace("；", ";")
    value = value.replace("–", "-").replace("—", "-").replace("~", "-").replace("→", "-")
    value = re.sub(r"\((?:full|whole) period\)", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\(recurring daily\)", "", value, flags=re.IGNORECASE)
    value = re.sub(
        r'start time\s*"?(\d{1,2}:\d{2})(?::\d{2})?"?\s*,\s*end time\s*'
        r'"?(\d{1,2}:\d{2})(?::\d{2})?"?',
        r"\1-\2",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"(\d{1,2}:\d{2})(?::\d{2})?\s*(?:to|~|–|—|-)\s*(\d{1,2}:\d{2})(?::\d{2})?",
        r"\1-\2",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\bensuring\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\bensure the .*? target is\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\bcarrier frequency\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\bMbit/s\b", "Mbps", value, flags=re.IGNORECASE)
    value = re.sub(r"\bMbit\b", "Mbps", value, flags=re.IGNORECASE)
    value = _RATE_RE.sub(lambda match: f"{match.group(1)}Mbps", value)
    value = value.replace(";", "\n")
    value = re.sub(
        r"^\s*(?:time-segmented\s+)?(?:rate guarantee (?:goal|target)|"
        r"guaranteed throughput target|energy saving time(?: range)?|"
        r"cell rat for energy saving|cell radio access technology for energy saving|"
        r"energy saving mode|background event)\s*:\s*",
        "",
        value,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    value = re.sub(r"\bset the\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+to\s+", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\.(?=\s|$)", "", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s*([,:;])\s*", r"\1", value)
    return value.strip().lower()


#: Function words that carry no business value in a free-form Task Context.
_CONTEXT_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "at",
        "be",
        "during",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "the",
        "to",
        "with",
    }
)


def _task_context_tokens(text: str) -> tuple[str, ...]:
    """Return a word-order-insensitive token bag of the normalized Task Context."""
    tokens = re.findall(r"[a-z0-9][a-z0-9:.+\-]*", normalize_task_context(text))
    return tuple(sorted(token for token in tokens if token not in _CONTEXT_STOPWORDS))


_RATE_MBPS_RE: Final[re.Pattern[str]] = re.compile(r"(\d+(?:\.\d+)?)\s*mbps", re.IGNORECASE)
_SITE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:lnr|nes|nres|lte)_[a-z0-9_]+\b", re.IGNORECASE
)
_RAT_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:e-utran|eutran|lte|4g|ng-ran|ngran|nr|5g|lnr)\b", re.IGNORECASE
)
_MODE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:aggressive|conservative|lossless|lossy)\b|"
    r"\buser experience first\b|\benergy saving first\b",
    re.IGNORECASE,
)
_RAT_MAP: Final[dict[str, str]] = {
    "e-utran": "LTE",
    "eutran": "LTE",
    "lte": "LTE",
    "4g": "LTE",
    "ng-ran": "NR",
    "ngran": "NR",
    "nr": "NR",
    "5g": "NR",
    "lnr": "ALL",
}


@dataclass(frozen=True)
class _ContextSignature:
    """Business signals extracted from one free-form Task Context."""

    rates: frozenset[str]
    hours: frozenset[int]
    sites: frozenset[str]
    rats: frozenset[str]
    modes: frozenset[str]
    tokens: frozenset[str]


def _context_signature(text: str) -> _ContextSignature:
    """Extract rates, hours, sites, RATs and modes from one Task Context."""
    normalized = normalize_task_context(text)
    raw = str(text or "").lower()
    rates = frozenset(_RATE_MBPS_RE.findall(normalized))
    hours = frozenset(int(hour) for hour in _normalize_time(normalized) if hour.isdigit())
    sites = frozenset(match.lower() for match in _SITE_RE.findall(raw))
    rats = frozenset(_RAT_MAP[match.lower()] for match in _RAT_RE.findall(raw))
    modes = frozenset(_normalize_mode(match) for match in _MODE_RE.findall(raw))
    tokens = frozenset(_task_context_tokens(text))
    return _ContextSignature(rates, hours, sites, rats, modes, tokens)


def _contexts_match(expected: str, actual: str) -> bool:
    """Compare two Task Contexts by business signals, falling back to a token bag.

    A context that carries rates, sites, RATs or modes is compared on those signals only; the
    phrasing then differs freely. A context that only carries a time range is additionally compared
    on its token bag, so a changed video bitrate or another value is still caught.
    """
    exp = _context_signature(expected)
    act = _context_signature(actual)
    business = any(
        (
            exp.rates,
            exp.sites,
            exp.rats,
            exp.modes,
            act.rates,
            act.sites,
            act.rats,
            act.modes,
        )
    )
    if business:
        return (
            exp.rates == act.rates
            and exp.hours == act.hours
            and exp.sites == act.sites
            and exp.rats == act.rats
            and exp.modes == act.modes
        )
    return exp.hours == act.hours and exp.tokens == act.tokens


def _normalize_section(section: str, value: str) -> str:
    text = _clean_text(value)
    if section == "Task Object":
        text = re.sub(r"^(?:the area is|area|region)\s*:?\s*", "", text, flags=re.IGNORECASE)
        text = text.rstrip(".")
    elif section == "Task Target":
        text = re.sub(
            r"^(?:energy consumption goal|goal|target)\s*:\s*", "", text, flags=re.IGNORECASE
        )
    return text


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_intent_value(key: str, value: object) -> object:
    if key == "Operation type":
        return _clean_text(str(value or "")).capitalize()
    if key == "Energy saving cell RAT":
        return _normalize_rat(value)
    if key == "Energy saving mode":
        return _normalize_mode(value)
    if key == "Energy saving region":
        return _clean_text(str(value or "")).lower()
    if key == "Whether Experience-Preserving Energy Saving is Required":
        return _normalize_experience(value)
    if key == "Special Requirements for Carrier Shutdown Frequencies":
        return _clean_text(str(value or "")).lower()
    if key == "Energy saving time":
        return _normalize_time(value)
    if key == "Frequencies of carriers that can be shut down":
        return _normalize_frequencies(value)
    if key == "Gain target":
        return _normalize_gain(value)
    if key == "Guaranteed throughput target by zone":
        return _normalize_zone_targets(value)
    if key == "Scenario-based energy saving":
        return _normalize_scenario_based(value)
    return _normalize_generic(value)


def _normalize_rat(value: object) -> str:
    text = _clean_text(str(value or "")).upper()
    return _RAT_ALIASES.get(text, text)


def _normalize_mode(value: object) -> str:
    text = " ".join(_clean_text(str(value or "")).lower().replace("-", " ").split())
    if not text:
        return ""
    if (
        "aggressive" in text
        or "maximize energy" in text
        or "high gs" in text
        or text == "green"
    ):
        return "Aggressive"
    if (
        "conservative" in text
        or "maximize user" in text
        or "maximize kpi" in text
        or "kpi assurance" in text
    ):
        return "Conservative"
    return text


def _normalize_experience(value: object) -> str:
    text = _clean_text(str(value or "")).lower()
    if not text:
        return ""
    if text in {"y", "yes", "true"} or "experience first" in text or "lossless" in text:
        return "Y"
    if text in {"n", "no", "false"} or "energy saving first" in text or "lossy" in text:
        return "N"
    return text.upper()


def _normalize_gain(value: object) -> str:
    text = _clean_text(str(value or ""))
    if not text:
        return ""
    match = _NUMBER_RE.search(text)
    return match.group(1) if match is not None else text.lower()


def _normalize_frequencies(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        return (_clean_text(str(value or "")),)
    normalized: list[str] = []
    for item in value:
        text = _clean_text(str(item or "")).replace(",", "")
        match = _NUMBER_RE.search(text)
        normalized.append(match.group(1) if match is not None else text.lower())
    return tuple(sorted(set(normalized)))


def _normalize_time(value: object) -> tuple[str, ...]:
    text = _clean_text(str(value or ""))
    if not text:
        return ()
    hours: set[int] = set()
    for match in _TIME_PAIR_RE.finditer(text):
        start = int(match.group(1))
        end = int(match.group(3))
        if start == end:
            hours.add(start % 24)
            continue
        if end < start:
            end += 24
        for hour in range(start, min(end, 24)):
            hours.add(hour % 24)
    if not hours:
        return (text.lower(),)
    return tuple(str(hour) for hour in sorted(hours))


def _normalize_zone_targets(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        return (_normalize_generic(value),)
    entries: list[tuple[object, ...]] = []
    for item in value:
        if not isinstance(item, Mapping):
            entries.append((_normalize_generic(item),))
            continue
        rate = _normalize_rate(item.get("Guaranteed throughput target"))
        period = _normalize_time(item.get("Effective time period"))
        entries.append((rate, period))
    return tuple(sorted(entries, key=repr))


def _normalize_scenario_based(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        return (_normalize_generic(value),)
    entries: list[tuple[object, ...]] = []
    for item in value:
        if not isinstance(item, Mapping):
            entries.append((_normalize_generic(item),))
            continue
        sites = _normalize_sites(item.get("Site list"))
        targets = _normalize_zone_targets(item.get("Guaranteed throughput target by site"))
        scenario = _clean_text(str(item.get("Scenario") or "")).lower()
        period = _normalize_time(item.get("Scenario period"))
        entries.append((sites, targets, scenario, period))
    return tuple(sorted(entries, key=repr))


def _normalize_sites(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return (_clean_text(str(value or "")),) if value else ()
    sites: list[str] = []
    for item in value:
        text = _clean_text(str(item or ""))
        lowered = text.lower()
        for prefix in _SITE_PREFIXES:
            if lowered.startswith(prefix):
                text = text[len(prefix) :].strip()
                break
        if text:
            sites.append(text)
    return tuple(sorted(set(sites)))


def _normalize_rate(value: object) -> str:
    text = _clean_text(str(value or ""))
    match = _RATE_RE.search(text)
    if match is not None:
        return match.group(1)
    number = _NUMBER_RE.search(text)
    return number.group(1) if number is not None else text.lower()


def _normalize_generic(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _normalize_generic(item)) for key, item in value.items()))
    if isinstance(value, list | tuple):
        return tuple(_normalize_generic(item) for item in value)
    if isinstance(value, str):
        return _clean_text(value).lower()
    return value
