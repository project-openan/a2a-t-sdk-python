"""Final-intent judgement: structured first, one model call as the fallback.

The tester compares the corpus intent with the downstream's own structured reading. Most
differences are pure notation (``10 Mbps`` vs ``10Mbps``, ``4G`` vs ``LTE``); only a genuine
residual is worth a model call, and that call decides every residual field at once. A missing
reading or an unavailable judge stays ``unjudged``/``no_basis`` — this module never fabricates a
pass or a failure.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from shared.judge_llm import JudgeUnavailable

from eval.osp.compare import INTENT_ROW_SAME, CompareRow, flatten_intent

#: How the structured comparison classified one pair of intents.
KIND_EXACT: Final[str] = "exact"
KIND_NORMALIZED: Final[str] = "normalized"
KIND_RESIDUAL: Final[str] = "residual"

#: Verdict status of one intent comparison.
STATUS_PASS: Final[str] = "pass"
STATUS_MISMATCH: Final[str] = "mismatch"
STATUS_UNJUDGED: Final[str] = "unjudged"
STATUS_NO_BASIS: Final[str] = "no_basis"

#: How the verdict was reached.
METHOD_STRUCTURED: Final[str] = "structured"
METHOD_MODEL: Final[str] = "model"
METHOD_UNJUDGED: Final[str] = "unjudged"

#: Structured answer the judge must return for one residual comparison.
INTENT_JUDGE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "all_equal": {"type": "boolean"},
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "equal": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["path", "equal", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["all_equal", "fields"],
    "additionalProperties": False,
}

#: System prompt of the judge call. It deliberately names no protocol or implementation.
_JUDGE_SYSTEM: Final[str] = (
    "You are an independent reviewer of a structured business intent. For every listed field, "
    "decide whether the observed value conveys the same substantive business meaning as the "
    "expected value and give a short reason. Then set all_equal to true only when every field is "
    "equal. Judge business meaning, not notation. Never assume hidden context: if the evidence is "
    "insufficient, say why in the field reason."
)

#: Process-wide cache of residual verdicts, cleared by :func:`clear_intent_judge_cache`.
_CACHE: dict[str, "IntentVerdict"] = {}


@dataclass(frozen=True)
class IntentEvaluation:
    """Result of the deterministic comparison of two intent documents.

    Attributes:
        kind: ``exact`` when every verbatim leaf matches, ``normalized`` when every semantic
            leaf matches, otherwise ``residual``.
        rows: every flattened comparison row.
        residual: the rows whose semantic verdict is not ``same``.
    """

    kind: str
    rows: list[CompareRow]
    residual: list[CompareRow]

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation of the comparison."""
        return {
            "kind": self.kind,
            "rows": [row.to_dict() for row in self.rows],
            "residual": [row.to_dict() for row in self.residual],
        }


@dataclass(frozen=True)
class IntentVerdict:
    """One final-intent verdict.

    Attributes:
        status: ``pass`` / ``mismatch`` / ``unjudged`` / ``no_basis``.
        method: ``structured`` (deterministic), ``model`` (judge answered), ``unjudged``.
        equal: the judge's aggregate equality, ``None`` when no judge decided it.
        reason: short explanation for the report.
        judge_model: judge model label, empty unless a judge answered.
        residual: residual fields the judge was asked to decide.
        fields: the judge's per-field verdicts, empty when no judge answered.
    """

    status: str
    method: str
    equal: bool | None = None
    reason: str = ""
    judge_model: str = ""
    residual: tuple[dict[str, object], ...] = ()
    fields: tuple[dict[str, object], ...] = ()
    #: Digest of the expected intent this verdict was computed against. Review refuses to reuse a
    #: verdict after the case expectation was edited.
    expected_sha256: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation persisted with the tester item."""
        return {
            "status": self.status,
            "method": self.method,
            "equal": self.equal,
            "reason": self.reason,
            "judgeModel": self.judge_model,
            "expectedSha256": self.expected_sha256,
            "residual": [dict(row) for row in self.residual],
            "fields": [dict(row) for row in self.fields],
        }


def expected_intent_digest(expected: Mapping[str, object]) -> str:
    """Return the stable digest review uses to check a persisted verdict is still applicable."""
    canonical = json.dumps(expected, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate_intent(
    expected: Mapping[str, object],
    observed: Mapping[str, object],
) -> IntentEvaluation:
    """Classify two intent documents as exact, normalized or residual.

    Exact means every verbatim leaf matches. Normalized means every leaf agrees after the
    notation-aware normalization of :mod:`eval.osp.compare`. Anything else is residual, and the
    residual rows are exactly the fields a judge must decide.
    """
    rows = flatten_intent(expected, observed)
    residual = [row for row in rows if row.verdict != INTENT_ROW_SAME]
    if all(row.status == INTENT_ROW_SAME for row in rows):
        kind = KIND_EXACT
    elif not residual:
        kind = KIND_NORMALIZED
    else:
        kind = KIND_RESIDUAL
    return IntentEvaluation(kind=kind, rows=rows, residual=residual)


def clear_intent_judge_cache() -> None:
    """Clear the process-wide residual cache."""
    _CACHE.clear()


def _cache_key(
    expected: Mapping[str, object],
    observed: Mapping[str, object],
    *,
    user_request: str,
    proposal_summary: str,
    judge_label: str,
) -> str:
    """Return a stable key of one comparison and its business context."""
    payload = {
        "expected": expected,
        "observed": observed,
        "userRequest": user_request,
        "proposal": proposal_summary,
        "judge": judge_label,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _judge_prompt(
    evaluation: IntentEvaluation,
    *,
    user_request: str,
    proposal_summary: str,
) -> str:
    """Render one judge prompt carrying only business context and the residual fields."""
    parts: list[str] = []
    if user_request:
        parts.append(f"User request:\n{user_request}")
    if proposal_summary:
        parts.append(f"Proposal summary:\n{proposal_summary}")
    parts.append("Compare these business fields:")
    for row in evaluation.residual:
        parts.append(f"- {row.path}: expected {row.expected}, observed {row.actual}")
    return "\n\n".join(parts)


def _valid_answer(
    answer: object,
    paths: set[str],
) -> tuple[bool, list[dict[str, object]]] | None:
    """Validate one judge answer, returning ``(all_equal, fields)`` or ``None`` when invalid."""
    if not isinstance(answer, Mapping):
        return None
    all_equal = answer.get("all_equal")
    if not isinstance(all_equal, bool):
        return None
    raw_fields = answer.get("fields")
    if not isinstance(raw_fields, Sequence) or isinstance(raw_fields, str | bytes):
        return None
    fields: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in raw_fields:
        if not isinstance(entry, Mapping):
            return None
        path = entry.get("path")
        equal = entry.get("equal")
        reason = entry.get("reason")
        if not isinstance(path, str) or not isinstance(equal, bool) or not isinstance(reason, str):
            return None
        fields.append({"path": path, "equal": equal, "reason": reason})
        seen.add(path)
    if paths - seen or seen - paths:
        return None
    # The aggregate must agree with the fields: a contradictory answer is not usable evidence.
    derived = all(bool(field["equal"]) for field in fields)
    if derived != all_equal:
        return None
    return all_equal, fields


def _residual_payload(evaluation: IntentEvaluation) -> tuple[dict[str, object], ...]:
    """Return the residual rows as plain dictionaries for the persisted verdict."""
    return tuple(
        {
            "path": row.path,
            "expected": row.expected,
            "observed": row.actual,
        }
        for row in evaluation.residual
    )


async def judge_intent(
    expected: Mapping[str, object],
    observed: Mapping[str, object] | None,
    *,
    judge: Any | None,
    judge_label: str = "",
    user_request: str = "",
    proposal_summary: str = "",
    proposals: Sequence[str] = (),
    cache: dict[str, IntentVerdict] | None = None,
) -> IntentVerdict:
    """Judge two intent documents, calling the judge at most once and only for a residual.

    Args:
        expected: the corpus intent.
        observed: the downstream's structured reading, or ``None`` when the run collected none.
        judge: independent judge client (``complete_json``) or ``None``.
        judge_label: label of the judge model, recorded when it answers.
        user_request: the operator request, included in the judge prompt.
        proposal_summary: the negotiation proposal summary, included in the judge prompt.
        proposals: alternative proposal sequence; joined when ``proposal_summary`` is empty.
        cache: override of the process-wide residual cache.

    Returns:
        The intent verdict. A missing reading is ``no_basis``; an absent or failing judge leaves a
        residual ``unjudged``. This function never invents a pass or a failure.
    """
    if not proposal_summary and proposals:
        proposal_summary = "\n".join(str(item) for item in proposals)
    digest = expected_intent_digest(expected)
    if observed is None:
        return IntentVerdict(
            status=STATUS_NO_BASIS,
            method=METHOD_UNJUDGED,
            reason="未采集到下游服务端的结构化意图",
            expected_sha256=digest,
        )
    evaluation = evaluate_intent(expected, observed)
    if evaluation.kind in (KIND_EXACT, KIND_NORMALIZED):
        return IntentVerdict(
            status=STATUS_PASS,
            method=METHOD_STRUCTURED,
            expected_sha256=digest,
        )

    payload = _residual_payload(evaluation)
    judge_available = judge is not None and bool(getattr(judge, "configured", True))
    if not judge_available:
        return IntentVerdict(
            status=STATUS_UNJUDGED,
            method=METHOD_UNJUDGED,
            residual=payload,
            reason="没有可用的独立判定模型，转人工复核",
            expected_sha256=digest,
        )

    store = _CACHE if cache is None else cache
    # Two different judge clients with an empty label must not share a cached verdict.
    judge_identity = judge_label or f"judge-{id(judge)}"
    key = _cache_key(
        expected,
        observed,
        user_request=user_request,
        proposal_summary=proposal_summary,
        judge_label=judge_identity,
    )
    cached = store.get(key)
    if cached is not None:
        return cached

    prompt = _judge_prompt(
        evaluation,
        user_request=user_request,
        proposal_summary=proposal_summary,
    )
    try:
        answer = await judge.complete_json(
            prompt, system=_JUDGE_SYSTEM, json_schema=INTENT_JUDGE_SCHEMA
        )
    except JudgeUnavailable:
        answer = None
    except Exception:  # noqa: BLE001 - a judge failure must stay "unjudged"
        answer = None

    validated = _valid_answer(answer, {row.path for row in evaluation.residual})
    if validated is None:
        verdict = IntentVerdict(
            status=STATUS_UNJUDGED,
            method=METHOD_UNJUDGED,
            residual=payload,
            reason="独立判定模型未给出可用的逐字段结论，转人工复核",
            expected_sha256=digest,
        )
    else:
        all_equal, fields = validated
        verdict = IntentVerdict(
            status=STATUS_PASS if all_equal else STATUS_MISMATCH,
            method=METHOD_MODEL,
            equal=all_equal,
            reason="" if all_equal else "独立判定模型认为意图字段存在业务差异",
            judge_model=judge_label,
            residual=payload,
            fields=tuple(fields),
            expected_sha256=digest,
        )
    if verdict.status != STATUS_UNJUDGED:
        store[key] = verdict
    return verdict
