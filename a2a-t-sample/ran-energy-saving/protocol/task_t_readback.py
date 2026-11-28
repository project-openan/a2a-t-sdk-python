"""Read a rendered Task-T back into the caller-schema fields the client needs.

The client no longer asks the SDK to re-extract the Task-T before sending it. The accept refill
and the decision grounding still need the operator-derived values the Task-T carries, so this
module reads them from the prompt the client just rendered: the template's own section layout plus
the sample's one rate grammar. It never invents a value and never calls a model.

Only the fields the accept refill can use are read. ``siteList`` and ``backgroundEvent`` are
recovered from the labelled parts of the Task Context section when the model wrote them in the
labelled form; an unlabelled context leaves them absent.
"""

from __future__ import annotations

import re
from typing import Final

from protocol.target_grammar import find_segments, format_segments

__all__ = ["read_task_t_fields"]

#: One ``## Heading`` section of a rendered Task-T, body up to the next heading.
_SECTION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^##\s+(?P<head>[^\n]+)\n(?P<body>.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL
)

#: Section heading (lower-cased) per caller-schema field; the template writes both languages.
_SECTION_HEADINGS: Final[dict[str, tuple[str, ...]]] = {
    "operationType": ("operation type", "操作类型"),
    "region": ("task object", "任务对象"),
    "energySavingGoal": ("task target", "任务目标"),
    "__context__": ("task context", "任务上下文"),
}

#: Labelled parts of the Task Context section. The slot contract renders them as
#: ``Label: value`` separated by commas, with the value keeping its own commas/brackets.
_CONTEXT_LABELS: Final[dict[str, tuple[str, ...]]] = {
    "siteList": ("site list", "站点列表"),
    "guaranteedThroughputTarget": (
        "time-segmented rate guarantee target",
        "分时段速率保障目标",
        "rate guarantee target",
        "速率保障目标",
    ),
    "backgroundEvent": ("background event", "背景事件"),
}

_LABEL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<label>"
    + "|".join(
        re.escape(label)
        for labels in _CONTEXT_LABELS.values()
        for label in labels
    )
    + r")\s*[:：]",
    re.IGNORECASE,
)


def _sections(prompt_text: str) -> dict[str, str]:
    """Return the Task-T sections keyed by their lower-cased heading."""
    return {
        match.group("head").strip().lower(): match.group("body").strip()
        for match in _SECTION_PATTERN.finditer(prompt_text or "")
    }


def _section(sections: dict[str, str], field: str) -> str:
    """Return one section's body, the first matching bilingual heading winning."""
    for heading in _SECTION_HEADINGS[field]:
        body = sections.get(heading)
        if body:
            return body
    return ""


def _labelled_parts(context: str) -> dict[str, str]:
    """Split the Task Context into its labelled parts.

    ``Site list: ["A", "B"], Background event: outage`` must keep the comma inside the bracket,
    so a plain comma split would break it. The labels are the only separators.
    """
    matches = list(_LABEL_PATTERN.finditer(context))
    parts: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(context)
        label = match.group("label").strip().lower()
        value = context[start:end].strip().rstrip(",").strip()
        field = next(
            (
                name
                for name, labels in _CONTEXT_LABELS.items()
                if label in labels
            ),
            "",
        )
        if field and value and field not in parts:
            parts[field] = value
    return parts


def _site_list(value: str) -> str:
    """Return the comma-joined site names of one rendered site list."""
    stripped = value.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        stripped = stripped[1:-1]
    names = [part.strip().strip("\"'") for part in stripped.split(",")]
    return ", ".join(name for name in names if name)


def read_task_t_fields(prompt_text: str) -> dict[str, object]:
    """Return the caller-schema fields a rendered Task-T carries.

    Args:
        prompt_text: the Task-T prompt the client is about to send.

    Returns:
        The non-empty fields, keyed by their caller-schema name. An absent section or value is
        simply omitted, so a caller can use the mapping as a baseline without inventing defaults.
    """
    if not prompt_text or not prompt_text.strip():
        return {}
    sections = _sections(prompt_text)
    fields: dict[str, object] = {}
    for field in ("operationType", "region", "energySavingGoal"):
        value = _section(sections, field)
        if value:
            fields[field] = " ".join(value.split())
    context = _section(sections, "__context__")
    parts = _labelled_parts(context)
    if parts.get("siteList"):
        sites = _site_list(parts["siteList"])
        if sites:
            fields["siteList"] = sites
    if parts.get("backgroundEvent"):
        fields["backgroundEvent"] = " ".join(parts["backgroundEvent"].split())
    target = parts.get("guaranteedThroughputTarget", "")
    # The rate target lives in the Task Context section. Never scan the whole prompt: the Task
    # Description or a echoed request may mention a rate that is not the task's own target.
    segments = find_segments(target or context)
    if segments:
        fields["guaranteedThroughputTarget"] = format_segments(segments)
    elif target:
        # A phrase target ("Ensuring user experience for 720p video") is what the request said;
        # the accept filter decides whether it can travel as a Task-T value.
        fields["guaranteedThroughputTarget"] = " ".join(target.split())
    return fields
