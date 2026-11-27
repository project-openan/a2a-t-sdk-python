"""Matching rules of the two OSP expectations the corpus judges against observed evidence.

The case YAML used to state only the validation verdict and the negotiation round count; the
per-slot errors of a block and the server's negotiation messages were evidence. Two optional
blocks extend a case without changing ``validate`` (still a plain verdict string):

``slot_errors`` (``expect.protocols.a2a-t``)
    The per-slot errors a blocked A2A-T request must report.

    ```yaml
    slot_errors:
      mode: any                     # any (default) | cover
      expect:
        - code: slot.constraint_violated
          slotName: 增益目标        # exact observed slot name
          message: 增益目标不允许为 20%   # exact observed message, whitespace-normalized
          contains: [gain, 增益]     # any keyword hit in slot name / message / facts
          facts: { actual: "20" }    # facts subset
        - slot.not_provided          # a bare code string is a code-only matcher
    ```

    ``any`` passes when one observed error matches one entry; ``cover`` passes when every observed
    error is matched (and at least one error was observed). ``contains`` entries are alternatives
    of each other, so one keyword list covers both languages of the model-rendered label.
    The tester exports the whole observed error (``code`` / ``slotName`` / ``message`` /
    ``facts``) so that adopting a candidate freezes what was actually reported; an
    author who does not want the model-rendered parts to be part of the oracle deletes those keys
    from the case. A matcher that states no condition matches any error, which is how an author
    says "a slot error, any kind".

``negotiation.messages`` (``expect.protocols.<protocol>.negotiation``)
    The server's negotiation message per round, as a list of acceptable renderings.

    ```yaml
    negotiation:
      expected: true
      expected_rounds: 1
      messages:                     # outer index = round (1-based), inner = alternatives
        - - "## Feasibility Negotiation\n..."
          - "## Feasibility Negotiation\n..."
    ```

    A flat list of strings is accepted only when the case expects one round; a multi-round case
    must nest, because a flat list would be ambiguous between "one per round" and "alternatives of
    round 1".

The two observations come from different places (a validation stage detail versus ``run.proposals``)
but the rules are shared by the evaluation runner and the tester, so they live here: a comparator that
silently drifted between the two would make a green item meaningless.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from eval.osp.compare import business_facts, free_text_tokens, parse_sections_ordered

#: Dimension name of the blocked-request expectation, used by the tester and reports.
SLOT_DIMENSION: Final[str] = "slot_errors"
#: Dimension name of the negotiation-message expectation.
MESSAGE_DIMENSION: Final[str] = "messages"

#: ``any``: one match satisfies the expectation. ``cover``: every observed error must be matched.
SLOT_MODE_ANY: Final[str] = "any"
SLOT_MODE_COVER: Final[str] = "cover"
SLOT_MODES: Final[tuple[str, ...]] = (SLOT_MODE_ANY, SLOT_MODE_COVER)

#: Path of the A2A-T protocol block inside a case document.
A2A_T_PROTOCOL: Final[str] = "a2a-t"


@dataclass(frozen=True)
class SlotErrorMatcher:
    """One acceptable slot error of a blocked request.

    ``code`` and ``slot_name`` are exact matches; ``message`` is an exact match after whitespace
    normalization; ``contains`` is a list of keywords of which at least one must appear in the slot
    name, the message or a fact value; ``facts`` is a subset match over the observed facts. A
    matcher with no condition matches any error, which is how an author says "a slot error, any
    kind".
    """

    code: str | None = None
    slot_name: str | None = None
    message: str | None = None
    contains: tuple[str, ...] = ()
    facts: tuple[tuple[str, str], ...] = ()

    def matches(self, observed: Mapping[str, object]) -> bool:
        """Report whether one observed slot error satisfies this matcher."""
        if self.code is not None and str(observed.get("code") or "") != self.code:
            return False
        if self.slot_name is not None and str(observed.get("slotName") or "") != self.slot_name:
            return False
        if self.message is not None and _normalize_text(observed.get("message")) != (_normalize_text(self.message)):
            return False
        observed_facts = observed.get("facts")
        fact_map = (
            {str(key): str(value) for key, value in observed_facts.items()}
            if isinstance(observed_facts, Mapping)
            else {}
        )
        for key, expected in self.facts:
            actual = fact_map.get(key)
            if actual is None or _normalize_value(actual) != _normalize_value(expected):
                return False
        if self.contains:
            haystack = " ".join(
                (
                    str(observed.get("slotName") or ""),
                    str(observed.get("message") or ""),
                    *fact_map.values(),
                )
            ).lower()
            if not any(keyword.lower() in haystack for keyword in self.contains):
                return False
        return True

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation shown by the tester."""
        data: dict[str, object] = {}
        if self.code is not None:
            data["code"] = self.code
        if self.slot_name is not None:
            data["slotName"] = self.slot_name
        if self.message is not None:
            data["message"] = self.message
        if self.contains:
            data["contains"] = list(self.contains)
        if self.facts:
            data["facts"] = dict(self.facts)
        return data


@dataclass(frozen=True)
class SlotErrorExpectation:
    """The ``slot_errors`` block of one case."""

    mode: str
    matchers: tuple[SlotErrorMatcher, ...]

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation shown by the tester."""
        return {"mode": self.mode, "expect": [matcher.to_dict() for matcher in self.matchers]}


@dataclass(frozen=True)
class SlotErrorMatch:
    """Outcome of matching one observation against a :class:`SlotErrorExpectation`."""

    matched: bool
    mode: str
    #: Indexes of the expected entries that matched at least one observed error.
    matched_matchers: tuple[int, ...] = ()
    #: Indexes of the observed errors no expected entry matched.
    unmatched_observed: tuple[int, ...] = ()
    #: Indexes of the expected entries nothing matched (normal for an alternatives list).
    unmatched_matchers: tuple[int, ...] = ()

    @property
    def reason(self) -> str:
        """Return the operator-facing explanation of a mismatch."""
        if self.matched:
            return ""
        if not self.unmatched_observed and not self.matched_matchers:
            return "未观测到任何 slot 错误"
        if self.mode == SLOT_MODE_COVER and self.unmatched_observed:
            errors = ",".join(str(index) for index in self.unmatched_observed)
            return f"观测到的 slot 错误 [{errors}] 不在期望内"
        return "观测到的 slot 错误与期望均不匹配"

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation stored on the stage detail."""
        return {
            "matched": self.matched,
            "mode": self.mode,
            "matchedMatchers": list(self.matched_matchers),
            "unmatchedObserved": list(self.unmatched_observed),
            "unmatchedMatchers": list(self.unmatched_matchers),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SlotErrorEvidence:
    """The slot-error expectation of one validation together with its match.

    The evaluation runner and the tester both store this on the validation stage detail; keeping the
    shape here is what stops the two from reporting an interception differently.
    """

    expectation: SlotErrorExpectation
    observed: tuple[dict[str, object], ...]
    match: SlotErrorMatch | None

    def matched(self) -> bool:
        """Return whether the expectation is satisfied (``True`` when it was not judged)."""
        return self.match is None or self.match.matched

    def to_detail(self) -> dict[str, object]:
        """Return the two keys a validation stage detail carries."""
        return {
            "expectedSlotErrors": self.expectation.to_dict(),
            "slotErrorMatch": self.match.to_dict() if self.match is not None else None,
        }

    def diffs(self) -> tuple[dict[str, object], ...]:
        """Render the failed match as one diff per observation no entry explained."""
        if self.match is None or self.match.matched:
            return ()
        expected = self.expectation.to_dict()
        if not self.observed:
            return (
                {
                    "path": "validate.slotErrors",
                    "expected": expected,
                    "actual": [],
                    "reason": self.match.reason,
                },
            )
        unexplained = set(self.match.unmatched_observed)
        return tuple(
            {
                "path": f"validate.slotErrors[{index}]",
                "expected": expected,
                "actual": dict(error),
                "reason": self.match.reason,
            }
            for index, error in enumerate(self.observed)
            if index in unexplained
        )


def slot_error_evidence(
    case: Mapping[str, Any],
    expected_verdict: object,
    actual_verdict: object,
    observed: Iterable[Mapping[str, object]] | None,
    *,
    protocol: str = A2A_T_PROTOCOL,
) -> SlotErrorEvidence | None:
    """Build the slot-error evidence of one validation, or ``None`` when the case states none.

    The block describes what a *blocked* request must report, so it is only matched when both
    verdicts say ``blocked``: a run that did not block already fails on the verdict, and matching
    its (absent) slot errors would only add noise to the diff table.
    """
    expectation = slot_error_expectation(case, protocol)
    if expectation is None:
        return None
    errors = tuple(dict(error) for error in (observed or ()) if isinstance(error, Mapping))
    if expected_verdict != "blocked" or actual_verdict != "blocked":
        return SlotErrorEvidence(expectation=expectation, observed=errors, match=None)
    return SlotErrorEvidence(
        expectation=expectation,
        observed=errors,
        match=match_slot_errors(expectation, errors),
    )


@dataclass(frozen=True)
class RoundMessageMatch:
    """Outcome of matching one negotiation round."""

    round: int
    matched: bool
    expected: tuple[str, ...]
    actual: str
    alternative: int | None = None
    reason: str = ""
    #: Index of the observed message this round used, or ``None`` when nothing was observed.
    #: A page that renders every inbound frame needs it to mark the extras as unjudged.
    source_index: int | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation used by the report and the diff table."""
        return {
            "round": self.round,
            "matched": self.matched,
            "expected": list(self.expected),
            "actual": self.actual,
            "alternative": self.alternative,
            "reason": self.reason,
            "sourceIndex": self.source_index,
        }


@dataclass(frozen=True)
class MessageMatch:
    """Outcome of matching a whole negotiation dialogue."""

    matched: bool
    rounds: tuple[RoundMessageMatch, ...]

    def failed_rounds(self) -> tuple[RoundMessageMatch, ...]:
        """Return the rounds whose observation matched no acceptable rendering."""
        return tuple(round_match for round_match in self.rounds if not round_match.matched)

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation stored on the stage detail."""
        return {
            "matched": self.matched,
            "rounds": [round_match.to_dict() for round_match in self.rounds],
        }


@dataclass(frozen=True)
class NegotiationMessageExpectation:
    """The ``negotiation.messages`` block of one protocol expectation."""

    #: Outer index = 1-based round; inner tuple = acceptable renderings of that round.
    rounds: tuple[tuple[str, ...], ...]

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation shown by the tester."""
        return {"rounds": [list(alternatives) for alternatives in self.rounds]}


def slot_error_expectation(
    case: Mapping[str, Any],
    protocol: str = A2A_T_PROTOCOL,
) -> SlotErrorExpectation | None:
    """Return the case's ``slot_errors`` expectation, or ``None`` when it states none.

    Raises:
        ValueError: when the block is malformed; a case file is data and a silent skip would hide
            a typo as "no expectation".
    """
    block = _protocol_block(case, protocol)
    raw = block.get(SLOT_DIMENSION)
    if raw is None:
        return None
    if isinstance(raw, Sequence) and not isinstance(raw, str):
        # Shorthand: a bare list is an ``any`` list of matchers.
        return SlotErrorExpectation(SLOT_MODE_ANY, tuple(_matcher(entry) for entry in raw))
    if not isinstance(raw, Mapping):
        raise ValueError(f"{SLOT_DIMENSION} must be a mapping or a list")
    mode = str(raw.get("mode") or SLOT_MODE_ANY).strip().lower()
    if mode not in SLOT_MODES:
        raise ValueError(f"unknown {SLOT_DIMENSION}.mode {mode!r}; expected one of {SLOT_MODES}")
    entries = raw.get("expect")
    if entries is None:
        entries = []
    if not isinstance(entries, Sequence) or isinstance(entries, str):
        raise ValueError(f"{SLOT_DIMENSION}.expect must be a list")
    matchers = tuple(_matcher(entry) for entry in entries)
    if not matchers:
        # An empty list is not an assertion; keep it distinct from "no expectation".
        return None
    return SlotErrorExpectation(mode, matchers)


def match_slot_errors(
    expectation: SlotErrorExpectation,
    observed: Iterable[Mapping[str, object]],
) -> SlotErrorMatch:
    """Match the observed slot errors of one validation against an expectation.

    ``any`` (the default) is the operator's "any one of these is fine" reading: one observed error
    satisfying one entry passes. ``cover`` additionally requires every observed error to be
    covered, which turns a request that starts reporting unrelated errors red.
    """
    errors = [dict(error) for error in observed]
    matched_matchers: list[int] = []
    unmatched_observed: list[int] = []
    for index, error in enumerate(errors):
        hits = [matcher_index for matcher_index, matcher in enumerate(expectation.matchers) if matcher.matches(error)]
        matched_matchers.extend(hits)
        if not hits:
            unmatched_observed.append(index)
    unique_hits = tuple(sorted(set(matched_matchers)))
    unmatched_matchers = tuple(index for index in range(len(expectation.matchers)) if index not in unique_hits)
    if expectation.mode == SLOT_MODE_COVER:
        matched = bool(errors) and not unmatched_observed
    else:
        matched = bool(unique_hits)
    return SlotErrorMatch(
        matched=matched,
        mode=expectation.mode,
        matched_matchers=unique_hits,
        unmatched_observed=tuple(unmatched_observed),
        unmatched_matchers=unmatched_matchers,
    )


def negotiation_message_expectation(
    case: Mapping[str, Any],
    protocol: str,
) -> NegotiationMessageExpectation | None:
    """Return the case's expected server negotiation messages, or ``None`` when it states none.

    Raises:
        ValueError: when the block is malformed, when a flat list is used by a multi-round case, or
            when the number of expected rounds disagrees with ``expected_rounds``.
    """
    block = _protocol_block(case, protocol)
    negotiation = block.get("negotiation")
    if not isinstance(negotiation, Mapping):
        return None
    raw = negotiation.get(MESSAGE_DIMENSION)
    if raw is None:
        return None
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise ValueError(f"negotiation.{MESSAGE_DIMENSION} must be a list")
    expected_rounds = int(negotiation.get("expected_rounds") or 0)
    flat = all(isinstance(entry, str) for entry in raw)
    if flat:
        if expected_rounds > 1:
            raise ValueError(
                f"negotiation.{MESSAGE_DIMENSION} must nest one group per round: "
                f"this case expects {expected_rounds} rounds"
            )
        rounds = (tuple(str(entry) for entry in raw),) if raw else ()
    else:
        groups: list[tuple[str, ...]] = []
        for entry in raw:
            if isinstance(entry, str):
                groups.append((entry,))
                continue
            if not isinstance(entry, Sequence):
                raise ValueError(f"negotiation.{MESSAGE_DIMENSION} rounds must be lists of text")
            alternatives = tuple(str(text) for text in entry if isinstance(text, str) and text.strip())
            if not alternatives:
                raise ValueError(f"negotiation.{MESSAGE_DIMENSION} carries an empty round")
            groups.append(alternatives)
        rounds = tuple(groups)
    if not rounds:
        return None
    if expected_rounds and len(rounds) != expected_rounds:
        raise ValueError(
            f"negotiation.{MESSAGE_DIMENSION} describes {len(rounds)} rounds but expected_rounds is {expected_rounds}"
        )
    return NegotiationMessageExpectation(rounds)


def match_negotiation_messages(
    expectation: NegotiationMessageExpectation,
    observed: Sequence[Mapping[str, object]],
) -> MessageMatch:
    """Match the observed proposals round by round against the expectation.

    ``observed`` entries carry ``round`` (1-based), ``kind`` and ``content`` (the rendered
    Negotiation-T prose, or the plain question of a pure-A2A hop). A round not observed, an extra
    observed round, and a round matching no alternative all fail the dialogue; the per-round result
    keeps the reason and the ``source_index`` of the frame it used, so the tester can show which
    round diverged and the page can mark a second frame of the same round as unjudged.
    """
    by_round: dict[int, tuple[str, int]] = {}
    extra_rounds: list[tuple[int, str, int]] = []
    for index, proposal in enumerate(observed):
        round_number = _round_number(proposal.get("round"), index + 1)
        content = str(proposal.get("content") or "")
        if round_number > len(expectation.rounds):
            extra_rounds.append((round_number, content, index))
            continue
        # The first inbound frame of a round is the proposal; later frames of the same round stay
        # visible on the page but are deliberately not folded into the verdict.
        by_round.setdefault(round_number, (content, index))
    matches: list[RoundMessageMatch] = []
    for index, alternatives in enumerate(expectation.rounds):
        round_number = index + 1
        observed_round = by_round.get(round_number)
        if observed_round is None:
            matches.append(
                RoundMessageMatch(
                    round=round_number,
                    matched=False,
                    expected=alternatives,
                    actual="",
                    reason="未观测到该轮协商",
                )
            )
            continue
        actual, source_index = observed_round
        alternative, reason = _best_alternative(alternatives, actual)
        matches.append(
            RoundMessageMatch(
                round=round_number,
                matched=alternative is not None,
                expected=alternatives,
                actual=actual,
                alternative=alternative,
                reason="" if alternative is not None else reason,
                source_index=source_index,
            )
        )
    for round_number, content, source_index in sorted(extra_rounds):
        matches.append(
            RoundMessageMatch(
                round=round_number,
                matched=False,
                expected=(),
                actual=content,
                reason="观测到期望之外的额外协商轮次",
                source_index=source_index,
            )
        )
    return MessageMatch(
        matched=bool(matches) and all(match.matched for match in matches),
        rounds=tuple(matches),
    )


def compare_negotiation_message(expected: str, actual: str) -> tuple[bool, str]:
    """Return whether one observed message satisfies one acceptable rendering.

    A markdown expectation is judged by its ``##`` sections: each expected section must appear in
    the observed message in the same relative order, and the expected section's business facts must
    all appear in the observed section's facts. A plain-language expectation (a pure-A2A question)
    falls back to the same fact rule over the whole text, and to a token-containment rule when the
    expected text states no structured fact at all.

    The wording is never compared character for character: the server generates the message with a
    model, and two runs of the same case legitimately paraphrase the same requirement.
    """
    expected_sections = parse_sections_ordered(expected)
    if not expected_sections:
        return _facts_or_tokens_match(expected, actual, where="")
    actual_sections = parse_sections_ordered(actual)
    if not actual_sections:
        return False, "观测到的协商报文没有小节"
    cursor = 0
    for title, body in expected_sections:
        found = _find_section(actual_sections, title, start=cursor)
        if found is None:
            return False, f"缺少小节 {title}"
        cursor = found + 1
        ok, reason = _facts_or_tokens_match(body, actual_sections[found][1], where=title)
        if not ok:
            return False, reason
    return True, ""


def _facts_or_tokens_match(expected: str, actual: str, *, where: str) -> tuple[bool, str]:
    """Compare one block by business facts, falling back to tokens when it states none."""
    expected_facts = business_facts(expected)
    actual_facts = business_facts(actual)
    label = f"{where}: " if where else ""
    if expected_facts:
        missing = sorted(expected_facts - actual_facts)
        if missing:
            return False, f"{label}缺少业务事实 {', '.join(missing)}"
        return True, ""
    expected_tokens = free_text_tokens(expected)
    if not expected_tokens:
        return True, ""
    missing_tokens = sorted(expected_tokens - free_text_tokens(actual))
    if missing_tokens:
        return False, f"{label}缺少关键词 {', '.join(missing_tokens)}"
    return True, ""


def _best_alternative(alternatives: Sequence[str], actual: str) -> tuple[int | None, str]:
    """Return the first matching alternative index, or the reason of the closest miss."""
    reasons: list[str] = []
    for index, candidate in enumerate(alternatives):
        matched, reason = compare_negotiation_message(candidate, actual)
        if matched:
            return index, ""
        reasons.append(f"备选 {index + 1}：{reason}")
    return None, "；".join(reasons) or "没有可接受的备选报文"


def _find_section(
    sections: Sequence[tuple[str, str]],
    title: str,
    *,
    start: int,
) -> int | None:
    """Return the index of the section matching ``title`` at or after ``start``."""
    wanted = _normalize_title(title)
    for index in range(start, len(sections)):
        if _titles_match(wanted, _normalize_title(sections[index][0])):
            return index
    return None


def _normalize_title(title: str) -> str:
    """Normalize one markdown heading for comparison (width, case, decorations, parenthetical)."""
    value = unicodedata.normalize("NFKC", title).strip().lower()
    value = re.sub(r"\(.*?\)", "", value)
    return re.sub(r"[^0-9a-z一-鿿]+", " ", value).strip()


def _titles_match(expected: str, actual: str) -> bool:
    """Report whether two normalized headings name the same section.

    The server and the corpus label the same section with different decorations (``Negotiation
    Proposal`` vs ``Negotiation``), so a heading matches when either contains the other.
    """
    if not expected or not actual:
        return False
    return expected == actual or expected in actual or actual in expected


def _matcher(raw: object) -> SlotErrorMatcher:
    """Build one matcher from its YAML form (a mapping or a bare code string)."""
    if isinstance(raw, str):
        return SlotErrorMatcher(code=raw.strip() or None)
    if not isinstance(raw, Mapping):
        raise ValueError(f"{SLOT_DIMENSION} entries must be mappings or code strings")
    code = raw.get("code")
    slot_name = raw.get("slotName", raw.get("slot_name"))
    message = raw.get("message")
    contains = raw.get("contains")
    facts = raw.get("facts")
    return SlotErrorMatcher(
        code=str(code).strip() if isinstance(code, str) and code.strip() else None,
        slot_name=(str(slot_name).strip() if isinstance(slot_name, str) and slot_name.strip() else None),
        message=(str(message).strip() if isinstance(message, str) and message.strip() else None),
        contains=_string_tuple(contains),
        facts=(
            tuple((str(key), str(value)) for key, value in facts.items() if isinstance(key, str) and value is not None)
            if isinstance(facts, Mapping)
            else ()
        ),
    )


def _string_tuple(raw: object) -> tuple[str, ...]:
    """Return a sequence of non-empty strings, or an empty tuple for anything else."""
    if isinstance(raw, str):
        return (raw,) if raw.strip() else ()
    if not isinstance(raw, Sequence):
        return ()
    return tuple(str(item) for item in raw if isinstance(item, str) and item.strip())


def _normalize_value(value: str) -> str:
    """Normalize one fact value for the subset comparison."""
    return " ".join(unicodedata.normalize("NFKC", str(value)).lower().split())


def _normalize_text(value: object) -> str:
    """Normalize one message for the exact comparison (width and whitespace only, not case)."""
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def _round_number(value: object, default: int) -> int:
    """Return the round index of one observation, falling back to its position."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if value > 0 else default
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else default
    return default


def _protocol_block(case: Mapping[str, Any], protocol: str) -> Mapping[str, Any]:
    """Return one protocol's expectation block, or an empty mapping when absent."""
    expect = case.get("expect")
    protocols = expect.get("protocols") if isinstance(expect, Mapping) else None
    block = protocols.get(protocol) if isinstance(protocols, Mapping) else None
    return block if isinstance(block, Mapping) else {}


__all__ = [
    "A2A_T_PROTOCOL",
    "MESSAGE_DIMENSION",
    "MessageMatch",
    "NegotiationMessageExpectation",
    "RoundMessageMatch",
    "SLOT_DIMENSION",
    "SLOT_MODES",
    "SLOT_MODE_ANY",
    "SLOT_MODE_COVER",
    "SlotErrorEvidence",
    "SlotErrorExpectation",
    "SlotErrorMatch",
    "SlotErrorMatcher",
    "compare_negotiation_message",
    "match_negotiation_messages",
    "match_slot_errors",
    "negotiation_message_expectation",
    "slot_error_evidence",
    "slot_error_expectation",
]
