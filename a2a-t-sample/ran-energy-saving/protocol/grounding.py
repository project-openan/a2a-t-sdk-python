"""Deterministic grounding checks for an automatic negotiation decision.

The automatic decision agent may only fill a Task-T value when that value is backed by one of:

* the operator's original request (the intent fields or the initial Task-T extraction);
* the current server proposal's structured options or affirmative content;
* a deterministic tool result from the same decision turn, when that tool's data domain is the
  same as the server's (local demo only).

A model call is not proof: the value must match. This module never calls a model and never reads
the trace pipeline; the decision flow uses it before sending an ACCEPT.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from shared.models import NegotiationProposeView
from shared.task_schema import TASK_PARAM_SCHEMA


#: One deterministic tool call recorded during a decision turn.
@dataclass(frozen=True)
class ToolCall:
    """One ADK tool invocation with its arguments and JSON result."""

    name: str
    args: Mapping[str, object]
    result: Mapping[str, object]


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

#: One ``{HH:MM~HH:MM,NMbps}`` target segment.
_TARGET_SEGMENT: Final[re.Pattern[str]] = re.compile(
    r"\{\s*\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2}\s*,\s*\d+(?:\.\d+)?\s*Mbps\s*\}",
    re.IGNORECASE,
)

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


def _present(value: object) -> bool:
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


def _server_target_segments(propose: NegotiationProposeView) -> set[str]:
    """Return the normalized target segments the proposal offers affirmatively."""
    segments: set[str] = set()
    for sentence in _sentences(propose.content):
        if _negated(sentence) and not _affirmative(sentence):
            continue
        for match in _TARGET_SEGMENT.findall(sentence):
            segments.add(normalize(match))
    parameters = propose.parameters
    if isinstance(parameters, Mapping):
        raw = parameters.get("guaranteedThroughputTarget")
        if isinstance(raw, str):
            segments.update(normalize(item) for item in _TARGET_SEGMENT.findall(raw))
    return segments


def _server_contains(propose: NegotiationProposeView, value: object) -> bool:
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


def _tool_grounds(field: str, value: object, tool_calls: Sequence[ToolCall]) -> bool:
    """Return whether a deterministic tool result from this turn contains the value."""
    wanted = normalize(value)
    if not wanted:
        return False
    for call in tool_calls:
        result = call.result
        if field == "region" and normalize(result.get("region", "")) == wanted:
            return True
        if field == "siteList":
            sites = result.get("site_list")
            if isinstance(sites, Sequence) and not isinstance(sites, str):
                if wanted in {normalize(item) for item in sites}:
                    return True
    return False


def _target_segments(value: object) -> list[str]:
    """Return the normalized segments of one target value."""
    return [normalize(item) for item in _TARGET_SEGMENT.findall(str(value))]


def find_ungrounded(
    filled: Mapping[str, object],
    *,
    baseline: Mapping[str, object],
    intent: Mapping[str, object],
    propose: NegotiationProposeView,
    tool_calls: Sequence[ToolCall],
    tools_authoritative: bool,
) -> list[UngroundedValue]:
    """Return every value of ``filled`` that no allowed source grounds.

    The baseline is the initial Task-T extraction and the intent is the operator's business
    reading; a value equal to either one is grounded. A changed value needs the server proposal or
    an authoritative tool result. Required fields that are absent are reported as ungrounded too.
    """
    ungrounded: list[UngroundedValue] = []
    regions = server_region_values(propose)
    segments = _server_target_segments(propose)
    for name, value in filled.items():
        if not _present(value):
            continue
        if normalize(value) == normalize(baseline.get(name, "")):
            continue
        if normalize(value) == normalize(intent.get(name, "")):
            continue
        if name == "operationType":
            ungrounded.append(UngroundedValue(name, str(value), "changed operation type is not grounded"))
            continue
        if name == "region":
            if normalize(value) in regions:
                continue
            if tools_authoritative and _tool_grounds(name, value, tool_calls):
                continue
            ungrounded.append(UngroundedValue(name, str(value), "region is not offered by the server"))
            continue
        if name == "guaranteedThroughputTarget":
            value_segments = _target_segments(value)
            if not value_segments:
                ungrounded.append(UngroundedValue(name, str(value), "target is not time-segmented"))
                continue
            missing = [item for item in value_segments if item not in segments]
            if not missing:
                continue
            ungrounded.append(
                UngroundedValue(name, str(value), "target segments are not offered by the server")
            )
            continue
        if _server_contains(propose, value):
            continue
        if tools_authoritative and _tool_grounds(name, value, tool_calls):
            continue
        ungrounded.append(UngroundedValue(name, str(value), "value is not grounded"))
    required = TASK_PARAM_SCHEMA.get("required")
    site_level = any(_present(filled.get(field)) for field in ("siteList", "backgroundEvent"))
    if isinstance(required, list):
        for name in required:
            if name == "guaranteedThroughputTarget" and not site_level:
                # Area-level requests may omit the rate; the server executes the default
                # no-disruption mode. Site-level requests still need a concrete rate.
                continue
            if isinstance(name, str) and not _present(filled.get(name)):
                ungrounded.append(UngroundedValue(name, "", "required value is missing"))
    return ungrounded


def grounding_reask_text(propose: NegotiationProposeView, ungrounded: Sequence[UngroundedValue]) -> str:
    """Build the corrective proposal text for one grounded decision turn."""
    fields = ", ".join(f"{item.field}={item.value or '<missing>'}" for item in ungrounded)
    options = ""
    if propose.options is not None and propose.options.options:
        values = "; ".join(option.value for option in propose.options.options if option.value)
        options = f"\nServer options for {propose.options.field}: {values}."
    return (
        f"{propose.content}{options}\n\n"
        "## Grounding requirement\n"
        f"The values {fields} are not grounded. An ACCEPT may only carry values that appear in "
        "the original user request, in this server proposal, or in a tool result from this turn. "
        "If no grounded value exists, do not accept; return reject or abort with a clear rationale. "
        "Return only the NegotiationDecision JSON object."
    )


__all__ = [
    "ToolCall",
    "UngroundedValue",
    "find_ungrounded",
    "grounding_reask_text",
    "normalize",
    "server_region_values",
]
