"""Demo business rules of the pure A2A target negotiation.

The protocol module (:mod:`protocol.pure_a2a`) knows how to ask and resume a Task; this module
knows the RAN dataset's baseline/corrected targets and the deterministic answers used by the
offline demo and tests. Keeping the two apart lets the pure mode reuse the demonstration data
without teaching the protocol layer about any scenario.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from protocol.pure_a2a import RegexPureTargetPolicy
from shared.models import NegotiationDecision
from shared.run_constants import LANGUAGE_EN, LANGUAGE_ZH

from demo.scenario import KIND_INFORMATION, KIND_TARGET

#: Complete target the information scenario supplies, per language.
_BASE_TARGETS: Final[dict[str, str]] = {
    LANGUAGE_EN: "{00:00-17:30,10Mbps}, {17:30-23:00,20Mbps}, {23:00-24:00,10Mbps}",
    LANGUAGE_ZH: "{00:00-17:30,10Mbps}、{17:30-23:00,20Mbps}、{23:00-24:00,10Mbps}",
}

#: Corrected target the target scenario agrees on, per language.
_CORRECTED_TARGETS: Final[dict[str, str]] = {
    LANGUAGE_EN: "{00:00-17:30,10Mbps}, {17:30-23:00,20Mbps}, {23:00-24:00,10Mbps}",
    LANGUAGE_ZH: "{00:00-17:30,10Mbps}、{17:30-23:00,20Mbps}、{23:00-24:00,10Mbps}",
}

#: Alternative non-overlapping target used as the first counter-proposal of the target scenario.
_ALTERNATIVE_TARGETS: Final[dict[str, str]] = {
    LANGUAGE_EN: "{00:00-17:30,10Mbps}, {17:30-20:00,20Mbps}, {20:00-00:00,10Mbps}",
    LANGUAGE_ZH: "{00:00-17:30,10Mbps}、{17:30-20:00,20Mbps}、{20:00-00:00,10Mbps}",
}


def _target_for(kind: str, language: str) -> str:
    """Return one accepted target of ``kind`` for ``language``."""
    if kind == KIND_TARGET:
        return _CORRECTED_TARGETS.get(language, _CORRECTED_TARGETS[LANGUAGE_EN])
    return _BASE_TARGETS.get(language, _BASE_TARGETS[LANGUAGE_EN])


def pure_base_target(language: str = LANGUAGE_EN) -> str:
    """Return the complete, non-overlapping target of the night scenario."""
    return _target_for(KIND_INFORMATION, language)


def pure_corrected_target(language: str = LANGUAGE_EN) -> str:
    """Return the target the server proposes for overlapping segments."""
    return _target_for(KIND_TARGET, language)


def build_demo_pure_policy(language: str = LANGUAGE_EN) -> RegexPureTargetPolicy:
    """Build the pure target policy backed by the demonstration dataset."""
    return RegexPureTargetPolicy(
        base_targets={
            LANGUAGE_EN: pure_corrected_target(LANGUAGE_EN),
            LANGUAGE_ZH: pure_corrected_target(LANGUAGE_ZH),
        }
    )


def pure_answer_for(kind: str, round_number: int, language: str = LANGUAGE_EN) -> str:
    """Return the deterministic client answer of one pure negotiation round.

    ``information`` supplies the omitted target. ``target`` first counter-proposes a different
    valid target and then accepts the server's proposal, so the demo shows a real multi-round
    exchange rather than an immediate accept.
    """
    if kind == KIND_INFORMATION:
        return pure_base_target(language)
    if kind == KIND_TARGET:
        if round_number <= 1:
            return _ALTERNATIVE_TARGETS.get(language, _ALTERNATIVE_TARGETS[LANGUAGE_EN])
        return pure_corrected_target(language)
    # An OSS-relayed vendor question is tagged ``phase='question'`` and must be answered with the
    # operator's value (or not at all), never with the demonstration dataset's canned target.
    return ""


def pure_answer_from_event(
    event: Mapping[str, object], language: str = LANGUAGE_EN
) -> str:
    """Answer one normalized pure ``INPUT_REQUIRED`` event from its ``pure`` metadata."""
    metadata = event.get("metadata")
    if not isinstance(metadata, Mapping):
        return ""
    raw = metadata.get("pure")
    if not isinstance(raw, Mapping):
        return ""
    kind = raw.get("phase")
    if not isinstance(kind, str) or not kind:
        return ""
    try:
        round_number = int(raw.get("round", 1) or 1)
    except (TypeError, ValueError):
        round_number = 1
    return pure_answer_for(kind, round_number, language)


#: One deterministic sentence per Task-T parameter, in reading order. The wording follows the
#: answers the OSP evaluation set records for the vendor's plain-A2A dialogue ("The region is X.",
#: "Set the guaranteed throughput targets: ..."), so the vendor can parse the value it asked for.
_ANSWER_TEMPLATES: Final[tuple[tuple[str, str], ...]] = (
    ("region", "The region is {value}."),
    ("operationType", "The operation type is {value}."),
    ("energySavingGoal", "The energy saving goal is {value}."),
    ("guaranteedThroughputTarget", "Set the guaranteed throughput targets: {value}."),
    ("siteList", "The sites are {value}."),
    ("backgroundEvent", "Background event: {value}."),
)


def _answer_value(value: object) -> str:
    """Render one filled value as text; supported shapes only, empty otherwise."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        items = [
            str(item).strip()
            for item in value
            if isinstance(item, (str, int, float)) and not isinstance(item, bool)
        ]
        return ", ".join(item for item in items if item)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _filled_answer_text(filled: Mapping[str, object] | None) -> str:
    """Render every supplied value as one plain-text answer.

    The pure hop has no structured ``filledParams`` channel: the only way to answer more than one
    required item is a single message, so every non-empty value must be rendered. A first-match
    helper silently dropped the second supplied value -- the reported field bug where the answer
    carried the throughput target but not the region the vendor had asked for.

    Known Task-T parameters get a natural-language sentence (the vendor parses the value it asked
    for); any other parameter is still rendered as ``name: value`` rather than dropped.
    """
    if not filled:
        return ""
    sentences: list[str] = []
    rendered: set[str] = set()
    for key, template in _ANSWER_TEMPLATES:
        value = _answer_value(filled.get(key))
        if value:
            sentences.append(template.format(value=value))
            rendered.add(key)
    for key in sorted(filled):
        if key in rendered:
            continue
        value = _answer_value(filled[key])
        if value:
            sentences.append(f"{key}: {value}.")
    return " ".join(sentences)


def render_pure_answer(decision: NegotiationDecision) -> str:
    """Render one decision as the natural-language answer a pure A2A server expects.

    An accept carries the values the operator supplied; every one of them is rendered, because the
    vendor only sees this one text and cannot recover a dropped value. A counter carries its own
    prose (falling back to its structured target). A reject/abort carries the rationale so the
    vendor still receives a meaningful reply.
    """
    if decision.action == "counter":
        if decision.counter_proposal:
            return decision.counter_proposal
        target = _answer_value((decision.counter_params or {}).get("guaranteedThroughputTarget"))
        if target:
            return target
        filled = _filled_answer_text(decision.filled_params)
        return filled or decision.rationale
    if decision.action == "accept":
        filled = _filled_answer_text(decision.filled_params)
        return filled or decision.rationale or "Accepted. Please proceed."
    return decision.rationale or ("Rejected." if decision.action == "reject" else "Aborted.")


def recommendation_decision(value: object) -> NegotiationDecision | None:
    """Build the advisory decision from one OSS recommendation payload.

    The OSS Agent sends a structured recommendation (action plus ``filledParams``); older/scripted
    relays may send a plain string. A reject/abort recommendation is not an answer, so it returns
    ``None`` and the operator decides without advice.
    """
    if isinstance(value, Mapping):
        action = str(value.get("action") or "accept")
        if action not in ("accept", "counter"):
            return None
        filled = value.get("filledParams")
        counter = value.get("counterProposal")
        return NegotiationDecision(
            action=action,
            rationale=str(value.get("rationale") or ""),
            filled_params=dict(filled) if isinstance(filled, Mapping) else None,
            counter_proposal=counter if isinstance(counter, str) else None,
        )
    if isinstance(value, str) and value.strip():
        return NegotiationDecision(action="accept", rationale=value.strip())
    return None


def pure_answer_from_recommendation(
    event: Mapping[str, object],
    language: str = LANGUAGE_EN,
) -> str:
    """Answer one OSS-relayed question with its AI recommendation.

    The OSS Agent asks its decision model for a draft before relaying the downstream question, so
    automatic mode can answer without a second model turn. The live OSS tags the recommendation as
    a structured dict; it must be rendered here through the same deterministic renderer, otherwise
    automatic mode falls back to the scenario default and silently drops the operator's values. A
    structured local negotiation has no recommendation and keeps the deterministic policy answer.
    """
    metadata = event.get("metadata")
    if isinstance(metadata, Mapping):
        raw = metadata.get("pure")
        if isinstance(raw, Mapping):
            recommendation = raw.get("recommendation")
            if isinstance(recommendation, str) and recommendation.strip():
                return recommendation.strip()
            if isinstance(recommendation, Mapping):
                decision = recommendation_decision(recommendation)
                if decision is None:
                    # A reject/abort recommendation is not an answer: do not invent one.
                    return ""
                return render_pure_answer(decision)
    return pure_answer_from_event(event, language)


__all__ = [
    "build_demo_pure_policy",
    "pure_answer_for",
    "pure_answer_from_event",
    "pure_answer_from_recommendation",
    "pure_base_target",
    "pure_corrected_target",
    "recommendation_decision",
    "render_pure_answer",
]
