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
    LANGUAGE_EN: "{00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~24:00,10Mbps}",
    LANGUAGE_ZH: "{00:00~17:30,10Mbps}、{17:30~23:00,20Mbps}、{23:00~24:00,10Mbps}",
}

#: Corrected target the target scenario agrees on, per language.
_CORRECTED_TARGETS: Final[dict[str, str]] = {
    LANGUAGE_EN: "{00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~24:00,10Mbps}",
    LANGUAGE_ZH: "{00:00~17:30,10Mbps}、{17:30~23:00,20Mbps}、{23:00~24:00,10Mbps}",
}

#: Alternative non-overlapping target used as the first counter-proposal of the target scenario.
_ALTERNATIVE_TARGETS: Final[dict[str, str]] = {
    LANGUAGE_EN: "{00:00~17:30,10Mbps}, {17:30~20:00,20Mbps}, {20:00~00:00,10Mbps}",
    LANGUAGE_ZH: "{00:00~17:30,10Mbps}、{17:30~20:00,20Mbps}、{20:00~00:00,10Mbps}",
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
    return pure_base_target(language)


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


def _filled_answer_text(filled: Mapping[str, object] | None) -> str:
    """Render the values of one decision as a plain-text answer.

    The pure hop may ask for any task parameter, not only the throughput target: a vendor question
    such as "provide a real region" is answered with the region value. Prefer the free-text-ish
    fields, then join whatever else the decision supplied.
    """
    if not filled:
        return ""
    for key in ("guaranteedThroughputTarget", "region"):
        value = filled.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    values = [
        str(value).strip()
        for value in filled.values()
        if isinstance(value, str) and value.strip()
    ]
    return ", ".join(values)


def render_pure_answer(decision: NegotiationDecision) -> str:
    """Render one decision as the natural-language answer a pure A2A server expects.

    A counter/accept decision carries the negotiated target or the values the operator supplied;
    fall back to the rationale so the vendor still receives a meaningful reply when the model chose
    a different action.
    """
    if decision.action == "counter":
        if decision.counter_proposal:
            return decision.counter_proposal
        target = (decision.counter_params or {}).get("guaranteedThroughputTarget")
        if isinstance(target, str) and target:
            return target
        filled = _filled_answer_text(decision.filled_params)
        return filled or decision.rationale
    if decision.action == "accept":
        target = (decision.filled_params or {}).get("guaranteedThroughputTarget")
        if isinstance(target, str) and target:
            return target
        filled = _filled_answer_text(decision.filled_params)
        return filled or decision.rationale or "Accepted. Please proceed."
    return decision.rationale or ("Rejected." if decision.action == "reject" else "Aborted.")


def pure_answer_from_recommendation(
    event: Mapping[str, object],
    language: str = LANGUAGE_EN,
) -> str:
    """Answer one OSS-relayed question with its AI recommendation.

    The OSS Agent asks its decision model for a draft before relaying the downstream question, so
    automatic mode can answer without a second model turn. A structured local negotiation has no
    recommendation and keeps the deterministic policy answer.
    """
    metadata = event.get("metadata")
    if isinstance(metadata, Mapping):
        raw = metadata.get("pure")
        if isinstance(raw, Mapping):
            recommendation = raw.get("recommendation")
            if isinstance(recommendation, str) and recommendation.strip():
                return recommendation.strip()
    return pure_answer_from_event(event, language)


__all__ = [
    "build_demo_pure_policy",
    "pure_answer_for",
    "pure_answer_from_event",
    "pure_answer_from_recommendation",
    "pure_base_target",
    "pure_corrected_target",
    "render_pure_answer",
]
