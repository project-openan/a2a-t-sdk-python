"""Manual-answer helpers of the client negotiation flow.

The presentation lets an engineer answer a server proposal in natural language. The answer is not
sent as free text blindly: the OSS agent structures it into a ``NegotiationDecision`` first, the
engineer previews the parsed values, and only then does the normal decision endpoint submit it.

This module owns the proposal-text parsing used to show the requested items and to ground the
structuring prompt. The actual structuring is done by the ADK decision provider
(:meth:`AdkDecisionProvider.structure_answer`); there is no deterministic fallback, by design: an
unstructured answer must not silently become Task-T values.
"""

from __future__ import annotations

import re
from typing import Final

#: "Please provide the following missing items: A, B." / "Required items: A, B".
#: The separator stays on one line: ``\s`` would swallow the newline of a block heading and let
#: the inline pattern match the first list number instead of the block below it.
_INLINE_ITEMS_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"missing items?[ \t]*:[ \t]*([^\n.]+)", re.IGNORECASE),
    re.compile(r"provide the following items?[ \t]*:[ \t]*([^\n.]+)", re.IGNORECASE),
    re.compile(r"required (?:information )?items?[ \t]*:[ \t]*([^\n.]+)", re.IGNORECASE),
    re.compile(r"需要补充(?:以下)?(?:信息|项)?\s*[:：]\s*([^\n。]+)"),
)

#: "Please provide the correct <item> name.": a bounded vocabulary, so an unknown word is not
#: echoed back as a made-up item label.
_CORRECT_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"provide the correct\s+([A-Za-z][A-Za-z ]*?)\s+name\b",
    re.IGNORECASE,
)

#: Canonical item words mapped to the label the card shows. Keep small and explicit.
_CANONICAL_LABELS: Final[dict[str, str]] = {
    "region": "Region",
    "rate": "Rate",
    "throughput": "Throughput",
    "cell": "Cell",
    "site": "Site",
    "window": "Window",
}

#: A block list under a "Required Information Items" heading.
_BLOCK_HEADING_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:required information items?|需要补充(?:的)?信息)\s*:?\s*\n((?:[^\n]*\n?)+)",
    re.IGNORECASE,
)

#: One numbered or bulleted item.
_LIST_ITEM_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\s*(?:\d+[.)、]|[-*•])\s*(.+?)\s*$")

#: Separators inside an inline item list.
_INLINE_SEPARATOR_PATTERN: Final[re.Pattern[str]] = re.compile(r"\s*(?:,|，|、|;|；|\band\b)\s*")


def _canonical_item_label(phrase: str) -> str | None:
    """Map a bounded item phrase ("region", "energy saving region") to its user-facing label."""
    lowered = phrase.lower()
    for word, label in _CANONICAL_LABELS.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return label
    return None


def missing_item_labels(content: str) -> tuple[str, ...]:
    """Return the user-facing labels an information proposal asks the engineer to provide.

    The live validator and the sample policy both render the missing items as prose; this parser
    covers the shapes they use (an inline list after "missing items:", a numbered block under
    "Required Information Items", and "please provide the correct <item> name"). It is a
    display/prompt aid only: the model still decides the exact Task-T parameter names.
    """
    if not isinstance(content, str) or not content.strip():
        return ()
    labels: list[str] = []
    # The block heading is checked before the inline patterns: "Required Information Items:" is
    # also an inline match on its own, and would otherwise return the first list number.
    block = _BLOCK_HEADING_PATTERN.search(content)
    if block is not None:
        for line in block.group(1).splitlines():
            match = _LIST_ITEM_PATTERN.match(line)
            if match is None:
                continue
            label = match.group(1).strip(" .;；。")
            if label and label not in labels:
                labels.append(label)
    if labels:
        return tuple(labels)
    for match in _CORRECT_NAME_PATTERN.finditer(content):
        label = _canonical_item_label(match.group(1))
        if label is not None and label not in labels:
            labels.append(label)
    if labels:
        return tuple(labels)
    for pattern in _INLINE_ITEMS_PATTERNS:
        match = pattern.search(content)
        if match is None:
            continue
        for part in _INLINE_SEPARATOR_PATTERN.split(match.group(1)):
            label = part.strip(" .;；。")
            if label and label not in labels:
                labels.append(label)
    return tuple(labels)


__all__ = ["missing_item_labels"]
