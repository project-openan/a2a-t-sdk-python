"""Sample Task-T precheck used by the RAN energy-saving demonstration.

Live semantic validation occasionally accepts a Task-T that the sample considers incomplete or
infeasible. This module keeps those deterministic business checks out of the protocol handler:
the composition root injects :class:`RanDataTaskTPrecheck` into the server runtime.

A deployment replaces this class with its own checks over its Task-T schema.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

from a2a_t.core.validation_pipeline import FilledParamData
from protocol.negotiation_policy import NegotiationKind
from protocol.pre_task_negotiation import PrecheckFinding

from demo.served_areas import (
    build_area_options,
    build_area_proposal_text,
    detect_language,
    match_served_areas,
    resolve_served_area,
)

#: One ``HH:MM~HH:MM`` interval in a target or background-event string.
_TIME_INTERVAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(\d{2}:\d{2})\s*(?:~|to|-)\s*(\d{2}:\d{2})", re.IGNORECASE
)

#: One ``HH:MM~HH:MM,<rate>Mbps`` target segment.
_TARGET_SEGMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(\d{2}:\d{2})~(\d{2}:\d{2})\s*,\s*(\d+(?:\.\d+)?)\s*Mbps", re.IGNORECASE
)

#: Markers of a background outage in the sample's feasibility request.
_OUTAGE_MARKERS: Final[tuple[str, ...]] = ("outage", "power", "停电")

#: Sentence carrying the background event of a rendered Task-T prompt.
_OUTAGE_SENTENCE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[^\n。；;]*(?:outage|停电)[^\n。；;]*", re.IGNORECASE)

#: Area line of a rendered Task-T prompt (localized).
_REGION_SENTENCE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:The area is(?: the)?|区域为)\s*([^.\n。]+)", re.IGNORECASE
)


def _minutes(value: str) -> int:
    """Convert one ``HH:MM`` value to minutes from midnight."""
    hours, minutes = value.split(":", 1)
    return int(hours) * 60 + int(minutes)


def _span(start: str, end: str) -> tuple[int, int]:
    """Return one interval in minutes, wrapping midnight for an end at or before the start."""
    start_minutes = _minutes(start)
    end_minutes = _minutes(end)
    if end_minutes <= start_minutes:
        end_minutes += 24 * 60
    return start_minutes, end_minutes


def _target_segments(data: Mapping[str, object]) -> list[tuple[int, int, float]]:
    """Return the target segments of one Task-T parameter map."""
    target = data.get("guaranteedThroughputTarget")
    if not isinstance(target, str):
        return []
    return [
        (*_span(match.group(1), match.group(2)), float(match.group(3)))
        for match in _TARGET_SEGMENT_PATTERN.finditer(target)
    ]


def _overlaps(first: tuple[int, int], second: tuple[int, int]) -> bool:
    """Return whether two half-open minute intervals overlap."""
    return first[0] < second[1] and second[0] < first[1]


def _deterministic_negotiation_kind(data: Mapping[str, object]) -> NegotiationKind | None:
    """Detect the sample's target/feasibility triggers without relying on a model verdict."""
    segments = _target_segments(data)
    for index, first in enumerate(segments):
        for second in segments[index + 1 :]:
            if _overlaps(first[:2], second[:2]):
                return NegotiationKind.TARGET

    background = data.get("backgroundEvent")
    if isinstance(background, str) and any(marker in background.lower() for marker in _OUTAGE_MARKERS):
        outage_match = _TIME_INTERVAL_PATTERN.search(background)
        if outage_match is not None:
            outage = _span(outage_match.group(1), outage_match.group(2))
            if any(_overlaps(segment[:2], outage) and segment[2] > 2.0 for segment in segments):
                return NegotiationKind.FEASIBILITY
    return None


def _conflict_data(
    data: Mapping[str, object],
    prompt: str | None,
) -> Mapping[str, object]:
    """Merge the values a rendered Task-T prompt still carries into one parameter map.

    A failed semantic validation nulls the slot it rejected in its extracted parameters, so the
    conflict check reads the target segments and the background-event sentence from the prompt.
    """
    if not prompt:
        return data
    merged = dict(data)
    # Only override a field the prompt actually carries: a stub prompt in a unit test must not
    # hide the parameters it was given.
    region = _REGION_SENTENCE_PATTERN.search(prompt)
    if region is not None:
        merged["region"] = region.group(1).strip()
    if _TARGET_SEGMENT_PATTERN.search(prompt):
        merged["guaranteedThroughputTarget"] = prompt
    sentence = _OUTAGE_SENTENCE_PATTERN.search(prompt)
    if sentence is not None:
        merged["backgroundEvent"] = _outage_clause(sentence.group(0))
    return merged


def _outage_clause(text: str) -> str:
    """Return the clause of one sentence that names the outage.

    A Task-T context line may list the target segments and the background event together; the
    outage interval must come from the clause after the marker, not from the first interval of
    the line (which is a target segment).
    """
    for part in re.split(r"[，,；;。.!！?？]", text):
        if re.search(r"outage|停电", part, re.IGNORECASE):
            return part
    return text


def _missing_required_params(
    filled: FilledParamData,
    schema: Mapping[str, object],
) -> tuple[str, ...]:
    """Return required caller-schema fields that the semantic pass left empty."""
    required = schema.get("required")
    if not isinstance(required, list):
        return ()
    data = filled.data if isinstance(filled.data, Mapping) else {}
    missing: list[str] = []
    for name in required:
        if not isinstance(name, str):
            continue
        value = data.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(name)
    return tuple(missing)


class RanDataTaskTPrecheck:
    """Demo precheck: required caller fields, overlapping targets, impossible outage guarantees."""

    def __call__(
        self,
        filled: FilledParamData,
        schema: Mapping[str, object],
        *,
        prompt: str | None = None,
    ) -> PrecheckFinding | None:
        """Return the negotiation the sample opens for one Task-T."""
        data = filled.data if isinstance(filled.data, Mapping) else {}
        # With the prompt at hand, check the conflict first: a failed validation nulls the
        # rejected slot, so the parameters alone would look incomplete even when the prompt
        # carries a target that genuinely overlaps.
        checked = _conflict_data(data, prompt) if prompt else data
        if prompt:
            kind = _deterministic_negotiation_kind(checked)
            if kind is not None:
                return PrecheckFinding(
                    kind=kind,
                    detail=f"the sample detected a {kind.value} conflict",
                )
        missing = _missing_required_params(filled, schema)
        if missing:
            return PrecheckFinding(
                kind=NegotiationKind.INFORMATION,
                missing_items=missing,
                detail=f"required fields are missing: {', '.join(missing)}",
            )
        region = checked.get("region")
        if isinstance(region, str) and region.strip() and resolve_served_area(region) is None:
            matches = match_served_areas(region)
            language = detect_language(f"{region} {prompt or ''}")
            return PrecheckFinding(
                kind=NegotiationKind.INFORMATION,
                missing_items=("region",),
                detail=f"the sample does not serve area '{region}'",
                proposal_text=build_area_proposal_text(region, matches, language),
                options=build_area_options(region, matches),
            )
        if prompt:
            return None
        kind = _deterministic_negotiation_kind(data)
        if kind is None:
            return None
        return PrecheckFinding(
            kind=kind,
            detail=f"the sample detected a {kind.value} conflict",
        )


__all__ = ["RanDataTaskTPrecheck"]
