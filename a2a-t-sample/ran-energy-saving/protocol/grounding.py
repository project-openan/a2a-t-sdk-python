"""Deterministic grounding checks for an automatic negotiation decision.

The automatic decision agent may only send a Task-T value when that value is backed by one of:

* the operator's original request (the intent fields or the initial Task-T extraction);
* the current server proposal's structured options or affirmative content.

A model call is not proof: the value must match. This module never calls a model and never reads
the trace pipeline; the decision flow uses it before sending an ACCEPT.

Grounding is deliberately narrow. It judges only values that actually travel in the message being
built, and only values that the decision changes. A value the server already accepted in the
initial Task-T is grounded by construction: the client must not re-prove the operator's own values
or treat a field this negotiation does not touch as missing. Field completeness is a pre-send
concern, owned by the caller's required-field gate.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from shared.models import NegotiationProposeView

from protocol.target_grammar import find_segments


#: One Task-T value the automatic decision may not send.
@dataclass(frozen=True)
class UngroundedValue:
    """A filled value that no allowed source supports."""

    field: str
    value: str
    reason: str


#: Sentences that explicitly reject or describe the value as unusable. Their values are not
#: grounding: the very value a server rejects (``Region 'X' is not recognized``) must not become
#: an accepted value.
_NEGATION_MARKERS: Final[tuple[str, ...]] = (
    "not recognized",
    "not served",
    "does not serve",
    "unrecognized",
    "unsupported",
    "not available",
    "rejected",
    "overlap",
    "conflict",
    "不识别",
    "无法识别",
    "不支持",
    "未识别",
    "不可用",
    "重叠",
    "冲突",
    "拒绝",
)

#: Sentences that offer a usable value.
_AFFIRMATIVE_MARKERS: Final[tuple[str, ...]] = (
    "available",
    "served",
    "serves",
    "confirm",
    "choose",
    "corrected",
    "alternative",
    "agreed",
    "可用",
    "服务",
    "确认",
    "选择",
    "更正",
    "替代",
)

#: A sentence that states a requirement as infeasible (the rejected request, not an offer).
_INFEASIBLE_SENTENCE: Final[re.Pattern[str]] = re.compile(
    r"infeasible|not feasible|cannot|can ?not|unable|original request|不可行|无法|不能满足|原请求",
    re.IGNORECASE,
)

#: A plain rate in affirmative proposal prose ("reduce the target from 10Mbps to 2Mbps").
_RATE_VALUE: Final[re.Pattern[str]] = re.compile(r"\d+(?:\.\d+)?\s*Mbps", re.IGNORECASE)

#: One served-area value following a label in affirmative prose.
_AREA_VALUE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:Served areas|Available areas|Served region|Available region)\s*[:：]\s*([^\n.]+)", re.IGNORECASE),
    re.compile(r"(?:Available test region)\s*[:：]\s*([^\n.]+)", re.IGNORECASE),
    re.compile(r"(?:corrected region|served region is|available region is)\s*[:：]?\s*([^\n.]+)", re.IGNORECASE),
)

#: Punctuation trimmed from a normalized value.
_TRIM_PUNCTUATION: Final[str] = " \t\r\n.,;:!?\"'()[]{}，。；：！？、（）【】"


def normalize(value: object) -> str:
    """Return the comparison form of one value."""
    text = unicodedata.normalize("NFKC", str(value))
    text = re.sub(r"\s+", " ", text).casefold()
    return text.strip(_TRIM_PUNCTUATION)


def present(value: object) -> bool:
    """Return whether one filled value counts as present."""
    return value is not None and normalize(value) != ""


def _sentences(text: str) -> list[str]:
    """Split proposal content into sentences/lines for context checks."""
    parts = re.split(r"[\n。；;]+|(?<=[.!?])\s+", text)
    return [part.strip() for part in parts if part.strip()]


def _negated(sentence: str) -> bool:
    """Return whether a sentence describes a rejected/unusable value."""
    lowered = normalize(sentence)
    return any(marker in lowered for marker in _NEGATION_MARKERS)


def _affirmative(sentence: str) -> bool:
    """Return whether a sentence offers a usable value."""
    lowered = normalize(sentence)
    return any(marker in lowered for marker in _AFFIRMATIVE_MARKERS)


def _options_values(propose: NegotiationProposeView) -> dict[str, set[str]]:
    """Return normalized structured option values keyed by Task-T parameter name."""
    options = propose.options
    if options is None:
        return {}
    values = {normalize(option.value) for option in options.options if option.value}
    if not values:
        return {}
    return {options.field: values}


def server_region_values(propose: NegotiationProposeView) -> set[str]:
    """Return the normalized region values the current proposal offers.

    Only structured options and affirmative prose ground a region; a value quoted in a rejection
    sentence does not.
    """
    values = _options_values(propose).get("region", set())
    for sentence in _sentences(propose.content):
        if _negated(sentence):
            continue
        for pattern in _AREA_VALUE_PATTERNS:
            match = pattern.search(sentence)
            if match is None:
                continue
            for part in re.split(r"[,，、;；]", match.group(1)):
                candidate = normalize(part)
                if candidate:
                    values.add(candidate)
    return values


def server_entity_values(propose: NegotiationProposeView) -> dict[str, set[str]]:
    """Return the entity values the proposal itself offers, keyed by Task-T field.

    The server is the only authority for these fields, so this is the only source besides the
    operator's original request that may answer one. Structured options cover every entity field;
    free prose is parsed for a region only.
    """
    values = dict(_options_values(propose))
    regions = server_region_values(propose)
    if regions:
        values.setdefault("region", set()).update(regions)
    return values


def server_target_segments(propose: NegotiationProposeView) -> set[str]:
    """Return the normalized target segments the proposal offers affirmatively."""
    segments: set[str] = set()
    for sentence in _sentences(propose.content):
        if _negated(sentence) and not _affirmative(sentence):
            continue
        segments.update(_canonical_segments(sentence))
    parameters = propose.parameters
    if isinstance(parameters, Mapping):
        raw = parameters.get("guaranteedThroughputTarget")
        if isinstance(raw, str):
            segments.update(_canonical_segments(raw))
    return segments


def server_target_rates(propose: NegotiationProposeView) -> set[str]:
    """Return the plain Mbps rates an affirmative proposal sentence offers.

    A feasibility proposal often states its revised target in prose
    ("reduce the throughput target from 10Mbps to 2Mbps for the outage period") without the
    ``{HH:MM-HH:MM,NMbps}`` notation. The accepted value is still grounded by that sentence.
    """
    rates: set[str] = set()
    for sentence in _sentences(propose.content):
        if _negated(sentence) and not _affirmative(sentence):
            continue
        if _INFEASIBLE_SENTENCE.search(sentence):
            # A rate the proposal itself declares infeasible is the rejected baseline, never the
            # server's alternative; counting it would let the client "agree" to it.
            continue
        rates.update(normalize(match.group(0)) for match in _RATE_VALUE.finditer(sentence))
    return rates


def server_contains(propose: NegotiationProposeView, value: object) -> bool:
    """Return whether an affirmative proposal sentence contains the value."""
    wanted = normalize(value)
    if not wanted:
        return False
    for sentence in _sentences(propose.content):
        if _negated(sentence) and not _affirmative(sentence):
            continue
        if wanted in normalize(sentence):
            return True
    parameters = propose.parameters
    if isinstance(parameters, Mapping):
        for raw in parameters.values():
            if normalize(raw) == wanted:
                return True
    return False


def _unchanged(name: str, value: object, other: object) -> bool:
    """Return whether one value equals another, comparing targets by their notation.

    The accept normalizes a target to ``{HH:MM-HH:MM,NMbps}`` while the baseline may carry the
    vendor's ``{HH:MM-HH:MM: NMbps}``; a textual comparison would report the unchanged value as a
    change and demand a source for it.
    """
    if name == "guaranteedThroughputTarget":
        left = sorted(_canonical_segments(value))
        right = sorted(_canonical_segments(other))
        if left or right:
            return left == right
    return normalize(value) == normalize(other)


def _target_segments(value: object) -> list[str]:
    """Return the normalized segments of one target value."""
    return _canonical_segments(value)


def _canonical_segments(value: object) -> list[str]:
    """Return the canonical ``{HH:MM-HH:MM,NMbps}`` segments of one value, in source order."""
    return [normalize(segment.canonical()) for segment in find_segments(value)]


def find_ungrounded(
    filled: Mapping[str, object],
    *,
    baseline: Mapping[str, object],
    intent: Mapping[str, object],
    propose: NegotiationProposeView,
) -> list[UngroundedValue]:
    """Return every changed value of ``filled`` that no allowed source grounds.

    A value equal to the baseline (the Task-T the server already accepted) or to the operator's
    intent is grounded by construction: the client does not re-prove what it already sent. Only a
    changed value needs a source, and only the fields this negotiation may change are checked.
    Completeness of the required fields is not grounded here; the pre-send gate owns it.

    Args:
        filled: Task-T values the ACCEPT would carry.
        baseline: Task-T values already accepted by the server.
        intent: Operator-derived business values (may equal the baseline).
        propose: Inbound server proposal.

    Returns:
        One :class:`UngroundedValue` per changed value with no allowed source.
    """
    ungrounded: list[UngroundedValue] = []
    regions = server_entity_values(propose)
    segments = server_target_segments(propose)
    for name, value in filled.items():
        if not present(value):
            continue
        if _unchanged(name, value, baseline.get(name, "")):
            continue
        if _unchanged(name, value, intent.get(name, "")):
            continue
        if name == "operationType":
            if normalize(value) in regions.get(name, set()):
                continue
            # The operation type is never changed by a negotiation; a different value is either
            # the operator's own (handled above) or invented.
            ungrounded.append(UngroundedValue(name, str(value), "changed operation type is not offered by the server"))
            continue
        if name == "region":
            if normalize(value) in regions.get(name, set()):
                continue
            ungrounded.append(UngroundedValue(name, str(value), "region is not offered by the server"))
            continue
        if name in ("siteList", "energySavingGoal"):
            if normalize(value) in regions.get(name, set()) or server_contains(propose, value):
                continue
            ungrounded.append(UngroundedValue(name, str(value), f"{name} is not offered by the server"))
            continue
        if name == "guaranteedThroughputTarget":
            value_segments = _target_segments(value)
            if not value_segments:
                ungrounded.append(UngroundedValue(name, str(value), "target is not time-segmented"))
                continue
            missing = [item for item in value_segments if item not in segments]
            if not missing:
                continue
            value_rates = {
                normalize(match.group(0))
                for item in value_segments
                for match in _RATE_VALUE.finditer(item)
            }
            if value_rates and value_rates <= server_target_rates(propose):
                # The proposal offered the same rate in prose; the segment notation differs.
                continue
            ungrounded.append(
                UngroundedValue(name, str(value), "target segments are not offered by the server")
            )
            continue
        if server_contains(propose, value):
            continue
        ungrounded.append(UngroundedValue(name, str(value), "value is not grounded"))
    return ungrounded


def grounding_reask_text(propose: NegotiationProposeView, ungrounded: list[UngroundedValue]) -> str:
    """Build the corrective proposal text for one grounded decision turn."""
    fields = ", ".join(f"{item.field}={item.value or '<missing>'}" for item in ungrounded)
    options = ""
    if propose.options is not None and propose.options.options:
        values = "; ".join(option.value for option in propose.options.options if option.value)
        options = f"\nServer options for {propose.options.field}: {values}."
    return (
        f"{propose.content}{options}\n\n"
        "## Grounding requirement\n"
        f"The values {fields} do not appear in the original user request or in this server "
        "proposal. Do not invent a value. Use a value the original request states, a value from "
        "the server options above, or leave the field out; if an item the server asked for cannot "
        "be answered from those sources, return reject and name the missing item in the rationale. "
        "Values this negotiation did not change need no new source. "
        "Return only the NegotiationDecision JSON object."
    )


__all__ = [
    "UngroundedValue",
    "find_ungrounded",
    "grounding_reask_text",
    "normalize",
    "present",
    "server_entity_values",
    "server_region_values",
    "server_target_rates",
    "server_contains",
    "server_target_segments",
]
