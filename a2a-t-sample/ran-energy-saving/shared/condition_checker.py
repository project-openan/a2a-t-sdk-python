"""Client-side condition checker (L3 of the layered decision input).

The client agent may ask whether any candidate value is acceptable under its own decision basis
(the shared principles plus this case's conditions). The checker answers any candidate value, not
only the current proposal (D5), and its calls are decision evidence, never a pass criterion (D19).
It is independent of the negotiation model: an unavailable judge leaves the answer ``unknown``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from shared.judge_llm import JudgeUnavailable
from shared.models import DecisionBasis

#: Answer labels of one condition query.
ALLOWED_YES: Final[str] = "yes"
ALLOWED_NO: Final[str] = "no"
ALLOWED_UNKNOWN: Final[str] = "unknown"

#: Structured answer the checker asks the judge for.
CONDITION_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "allowed": {"type": "string", "enum": [ALLOWED_YES, ALLOWED_NO, ALLOWED_UNKNOWN]},
        "reason": {"type": "string"},
    },
    "required": ["allowed", "reason"],
    "additionalProperties": False,
}

_SYSTEM: Final[str] = (
    "You answer one question about a client's negotiation acceptance conditions. Use only the "
    "decision basis provided. Answer yes when the candidate value is acceptable, no when it "
    "violates a stated boundary, and unknown when the basis does not decide it. Never invent a "
    "boundary the basis does not state.\n"
    'Reply with exactly one JSON object and no other text: {"allowed": "yes"|"no"|"unknown", '
    '"reason": "<short explanation>"}.'
)


@dataclass(frozen=True)
class ConditionAnswer:
    """One condition query's answer.

    Attributes:
        allowed: ``yes`` / ``no`` / ``unknown``.
        reason: short explanation, empty when the answer needs none.
    """

    allowed: str
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation recorded with the decision evidence."""
        return {"allowed": self.allowed, "reason": self.reason}


class ConditionChecker:
    """Answer condition queries through the independent judge.

    Attributes:
        enabled: whether the checker is usable (operator switch, judge configured).
    """

    def __init__(self, judge: Any | None, *, enabled: bool = True) -> None:
        self._judge = judge
        self.enabled = enabled and judge is not None and bool(
            getattr(judge, "configured", True)
        )

    async def check(
        self,
        question: str,
        candidate_value: str,
        *,
        basis: DecisionBasis | None,
    ) -> ConditionAnswer:
        """Answer whether one candidate value is acceptable under the current basis.

        Args:
            question: the operator/agent question, e.g. "is a 2Mbps guarantee acceptable?".
            candidate_value: any value to test; it need not be the current proposal.
            basis: the frozen principles plus the case conditions.

        Returns:
            ``unknown`` when the checker is disabled, the basis is empty, or the judge fails.
        """
        judge = self._judge
        if not self.enabled or judge is None:
            return ConditionAnswer(ALLOWED_UNKNOWN, "条件检查器未启用")
        if basis is None or not (basis.client_conditions.strip() or basis.principles.strip()):
            return ConditionAnswer(ALLOWED_UNKNOWN, "没有可用的决策依据")
        prompt = "\n\n".join(
            part
            for part in (
                f"Shared principles (version {basis.principles_version or 'unknown'}):\n{basis.principles}"
                if basis.principles.strip()
                else "",
                f"Case-specific conditions:\n{basis.client_conditions}"
                if basis.client_conditions.strip()
                else "",
                f"Question:\n{question.strip()}",
                f"Candidate value:\n{candidate_value.strip()}",
            )
            if part
        )
        try:
            answer = await judge.complete_json(
                prompt, system=_SYSTEM, json_schema=CONDITION_SCHEMA
            )
        except JudgeUnavailable:
            return ConditionAnswer(ALLOWED_UNKNOWN, "条件检查器不可用")
        except Exception:  # noqa: BLE001 - evidence tooling must not fail the decision
            return ConditionAnswer(ALLOWED_UNKNOWN, "条件检查器不可用")
        if not isinstance(answer, dict):
            return ConditionAnswer(ALLOWED_UNKNOWN, "条件检查器未给出可用结论")
        allowed = str(answer.get("allowed") or "").strip().lower()
        if allowed not in {ALLOWED_YES, ALLOWED_NO, ALLOWED_UNKNOWN}:
            return ConditionAnswer(ALLOWED_UNKNOWN, "条件检查器未给出可用结论")
        return ConditionAnswer(allowed, str(answer.get("reason") or "").strip())


__all__ = [
    "ALLOWED_NO",
    "ALLOWED_UNKNOWN",
    "ALLOWED_YES",
    "CONDITION_SCHEMA",
    "ConditionAnswer",
    "ConditionChecker",
]
