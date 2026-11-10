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
import unicodedata
from collections.abc import Iterable, Mapping
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

#: The vendor phrases an information proposal two other ways ("Energy saving region is missing.",
#: "Please provide the region name"). Both name an item from the same bounded vocabulary, so the
#: page and the structuring prompt can still say what this round actually asks for.
_ITEM_IS_MISSING_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b([A-Za-z][A-Za-z ]*?)\s+is\s+missing\b",
    re.IGNORECASE,
)
_PROVIDE_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"provide the\s+([A-Za-z][A-Za-z ]*?)\s+name\b",
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


#: Everything that is not a letter, digit or CJK character. Comparing compact forms makes
#: punctuation and separators (``{00:00~06:00,2Mbps}`` vs ``00:00-06:00 2 Mbps``) irrelevant.
_COMPACT_STRIP: Final[re.Pattern[str]] = re.compile(r"[^0-9a-z一-鿿]+")

#: A proposal that asks the client to confirm a plan. Such a question is answered by agreeing,
#: not by carrying a Task-T value; a value-less accept is therefore a complete answer.
_CONFIRMATION_QUESTION: Final[re.Pattern[str]] = re.compile(
    r"\bconfirm(?:ation)?\b|确认",
    re.IGNORECASE,
)


def asks_for_confirmation(content: str | None) -> bool:
    """Return whether one proposal body asks the client to confirm a plan."""
    return isinstance(content, str) and _CONFIRMATION_QUESTION.search(content) is not None

#: One clock token of a structured target segment.
_TIME_TOKEN: Final[re.Pattern[str]] = re.compile(r"\d{1,2}:\d{2}")

#: One rate token of a structured target segment.
_RATE_TOKEN: Final[re.Pattern[str]] = re.compile(r"\d+(?:\.\d+)?\s*Mbps", re.IGNORECASE)


def _compact(value: object) -> str:
    """Return the separator-free comparison form of one value."""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return _COMPACT_STRIP.sub("", text)


def _answer_items(value: object) -> list[object]:
    """Return the individual values one filled parameter carries."""
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, (str, int, float)) and not isinstance(item, bool)]
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return [value]
    return []


def _value_grounded(value: object, text: str, compact: str) -> bool:
    """Whether one filled value appears in the operator's answer.

    Structured targets are compared token by token: the model may restate
    ``{00:00~06:00,2Mbps}`` with different separators, but every clock and rate it claims must
    still be present in the answer.
    """
    rendered = str(value)
    tokens = [*_TIME_TOKEN.findall(rendered), *_RATE_TOKEN.findall(rendered)]
    if tokens:
        return all(_compact(token) in compact for token in tokens)
    wanted = _compact(rendered)
    if len(wanted) >= 2:
        return wanted in compact
    return re.search(rf"(?<![0-9a-z]){re.escape(wanted)}(?![0-9a-z])", text) is not None


def ungrounded_answer_fields(
    answer: str,
    filled: Mapping[str, object] | None,
    *,
    allow: Iterable[object] = (),
) -> tuple[str, ...]:
    """Return the filled parameters whose values the operator never supplied.

    The manual-answer structurer is an LLM: it can return a value that appears nowhere in the
    engineer's words (the reported ``Region A`` answer). A pure A2A question has no structured
    channel, so an invented value would be sent to the vendor as if the operator had typed it.
    ``allow`` carries values the page itself supplied (a clicked served-area option), which are
    operator input too.
    """
    text = unicodedata.normalize(
        "NFKC",
        " ".join([answer, *(str(item) for item in allow if item)]),
    ).casefold()
    compact = _COMPACT_STRIP.sub("", text)
    missing: list[str] = []
    for name, value in (filled or {}).items():
        if any(not _value_grounded(item, text, compact) for item in _answer_items(value)):
            missing.append(name)
    return tuple(missing)


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
    for pattern in (_ITEM_IS_MISSING_PATTERN, _PROVIDE_NAME_PATTERN):
        for match in pattern.finditer(content):
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


__all__ = ["asks_for_confirmation", "missing_item_labels", "ungrounded_answer_fields"]
