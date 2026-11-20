"""Served-area catalog and semantic matching of the sample RAN.

The RAN serves a small set of named areas. A Task-T whose ``region`` is not one of them opens an
information negotiation instead of failing: the server lists the served areas, ranks them against
the requested text, and lets the client confirm one. The ranking is deliberately conservative -- a
high score becomes a recommendation, never an automatic answer -- so an unrecognized name still
asks the engineer to confirm.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Final

from shared.run_constants import LANGUAGE_EN, LANGUAGE_ZH

#: Similarity at or above which the first candidate is recommended to the engineer.
RECOMMENDATION_THRESHOLD: Final[float] = 0.62

#: Words removed before matching so "Region A area" still matches the area name.
_GENERIC_WORDS: Final[frozenset[str]] = frozenset({"area", "region", "zone", "the", "of", "in", "at"})

#: Chinese suffixes removed before matching.
_CJK_GENERIC_SUFFIXES: Final[tuple[str, ...]] = ("区域", "地区", "片区")

#: Non-word separators (Unicode letters such as Chinese are kept).
_SEPARATOR_PATTERN: Final[re.Pattern[str]] = re.compile(r"[^\w㐀-鿿]+", re.UNICODE)

#: CJK detection used to render the proposal in the request language.
_CJK_PATTERN: Final[re.Pattern[str]] = re.compile(r"[一-鿿]")


@dataclass(frozen=True)
class ServedArea:
    """One area the sample RAN serves.

    Attributes:
        area_id: Stable identifier used when matching and in the sample metadata.
        labels: Localized canonical name of the area.
        aliases: Alternative spellings a client may use for the same area.
        cell_ids: Cells of the sample dataset that belong to the area.
    """

    area_id: str
    labels: dict[str, str]
    aliases: tuple[str, ...] = ()
    cell_ids: tuple[str, ...] = ()

    def label(self, language: str) -> str:
        """Return the canonical name in ``language``, falling back to English."""
        return self.labels.get(language, self.labels[LANGUAGE_EN])

    @property
    def names(self) -> tuple[str, ...]:
        """Return every spelling that identifies this area."""
        return (self.labels[LANGUAGE_EN], self.labels[LANGUAGE_ZH], *self.aliases)


#: Areas served by the sample RAN, in display order. The first one is the default. The names are
#: deliberately neutral: a real NMS run replaces them with the region and sites the vendor serves.
SERVED_AREAS: Final[tuple[ServedArea, ...]] = (
    ServedArea(
        area_id="region-a",
        labels={
            LANGUAGE_EN: "Region A",
            LANGUAGE_ZH: "区域 A",
        },
        aliases=("Region A Area", "Region-A", "区域 A 区域", "区域A"),
        cell_ids=("cell-001", "cell-002"),
    ),
    ServedArea(
        area_id="region-b",
        labels={
            LANGUAGE_EN: "Region B",
            LANGUAGE_ZH: "区域 B",
        },
        aliases=("Region B Area", "Region-B", "区域 B 区域", "区域B"),
        cell_ids=("cell-003",),
    ),
)

#: Default area of the sample requests and of the unknown-area accept refill.
DEFAULT_SERVED_AREA: Final[ServedArea] = SERVED_AREAS[0]

#: Localized default region name, kept as the sample's historical ``REGION_STRINGS`` mapping.
REGION_STRINGS: Final[dict[str, str]] = dict(DEFAULT_SERVED_AREA.labels)


@dataclass(frozen=True)
class AreaMatch:
    """One served area ranked against the requested text.

    Attributes:
        area: The served area.
        score: Similarity in ``[0, 1]``; ``1`` is an exact label or alias match.
        matched_name: The spelling that produced the score.
    """

    area: ServedArea
    score: float
    matched_name: str


def normalize_area(value: str) -> str:
    """Normalize an area name for exact and fuzzy comparison."""
    text = unicodedata.normalize("NFKC", value).casefold().strip()
    text = _SEPARATOR_PATTERN.sub(" ", text)
    for word in _GENERIC_WORDS:
        text = re.sub(rf"\b{re.escape(word)}\b", " ", text)
    for suffix in _CJK_GENERIC_SUFFIXES:
        text = text.replace(suffix, "")
    return " ".join(text.split())


def detect_language(text: str) -> str:
    """Return the process language implied by one request or proposal text."""
    return LANGUAGE_ZH if _CJK_PATTERN.search(text) else LANGUAGE_EN


def _similarity(requested: str, candidate: str) -> float:
    """Return a token/sequence similarity in ``[0, 1]`` for two normalized names."""
    left = normalize_area(requested)
    right = normalize_area(candidate)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.9
    ratio = SequenceMatcher(None, left, right).ratio()
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if left_tokens and right_tokens:
        overlap = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    else:
        overlap = 0.0
    return round(max(ratio, 0.7 * ratio + 0.3 * overlap), 4)


def match_served_areas(requested: str) -> tuple[AreaMatch, ...]:
    """Rank every served area against ``requested``, best first.

    Exact names and aliases score ``1``. Other text is matched by token overlap and edit
    similarity; an unrecognized name still returns every served area so the client can choose.
    """
    matches: list[AreaMatch] = []
    for area in SERVED_AREAS:
        best_score = 0.0
        best_name = area.labels[LANGUAGE_EN]
        for name in area.names:
            score = _similarity(requested, name)
            if score > best_score:
                best_score = score
                best_name = name
        matches.append(AreaMatch(area=area, score=best_score, matched_name=best_name))
    order = {area.area_id: index for index, area in enumerate(SERVED_AREAS)}
    matches.sort(key=lambda match: (-match.score, order[match.area.area_id]))
    return tuple(matches)


def resolve_served_area(value: str) -> ServedArea | None:
    """Return the area whose exact name or alias equals ``value``, else ``None``."""
    normalized = normalize_area(value)
    if not normalized:
        return None
    for area in SERVED_AREAS:
        if any(normalize_area(name) == normalized for name in area.names):
            return area
    return None


def recommended_area(matches: tuple[AreaMatch, ...]) -> ServedArea | None:
    """Return the best candidate when it is similar enough to recommend, else ``None``."""
    if matches and matches[0].score >= RECOMMENDATION_THRESHOLD:
        return matches[0].area
    return None


def build_area_proposal_text(
    requested: str,
    matches: tuple[AreaMatch, ...],
    language: str,
) -> str:
    """Build the business question that asks the client to confirm a served area."""
    separator = "；" if language == LANGUAGE_ZH else "; "
    names = separator.join(match.area.label(language) for match in matches)
    closest = recommended_area(matches)
    if language == LANGUAGE_ZH:
        if closest is not None:
            return (
                f"无法识别区域“{requested}”。最接近的区域：{closest.label(language)}。"
                f"可用区域：{names}。请确认最接近的区域或选择其他可用区域，并保留原保障速率时段。"
            )
        return (
            f"无法识别区域“{requested}”。可用区域：{names}。未找到相近区域，请确认一个可用区域，并保留原保障速率时段。"
        )
    if closest is not None:
        return (
            f'The requested area "{requested}" is not recognized by the RAN. '
            f"Closest match: {closest.label(language)}. Served areas: {names}. "
            "Please confirm the closest match or choose another served area, and keep the "
            "requested guaranteed throughput windows."
        )
    return (
        f'The requested area "{requested}" is not recognized by the RAN. '
        f"Served areas: {names}. No close match was found. Please confirm a served area and keep "
        "the requested guaranteed throughput windows."
    )


def build_area_options(requested: str, matches: tuple[AreaMatch, ...]) -> dict[str, object]:
    """Build the sample-only option payload carried by the negotiation proposal."""
    closest = recommended_area(matches)
    return {
        "field": "region",
        "requested": requested,
        "keep": ["guaranteedThroughputTarget"],
        "options": [
            {
                "value": match.area.label(LANGUAGE_EN),
                "labels": dict(match.area.labels),
                "aliases": list(match.area.aliases),
                "score": match.score,
                "recommended": closest is not None and match.area.area_id == closest.area_id,
            }
            for match in matches
        ],
    }


def known_region_names() -> tuple[str, ...]:
    """Return every accepted spelling of a served area."""
    return tuple(name for area in SERVED_AREAS for name in area.names)


__all__ = [
    "DEFAULT_SERVED_AREA",
    "RECOMMENDATION_THRESHOLD",
    "REGION_STRINGS",
    "SERVED_AREAS",
    "AreaMatch",
    "ServedArea",
    "build_area_options",
    "build_area_proposal_text",
    "detect_language",
    "known_region_names",
    "match_served_areas",
    "normalize_area",
    "recommended_area",
    "resolve_served_area",
]
