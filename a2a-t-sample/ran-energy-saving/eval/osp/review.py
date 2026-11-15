"""Review-time judging of stored tester evidence against the current case corpus.

The tester already judges every run against the corpus *as it was when the run started*. A review
console needs the opposite question: **does this stored evidence satisfy today's corpus?** The two
questions differ as soon as an expectation is added or corrected (the whole point of the annotation
console), so re-running every case would be the only way to use a new `slot_errors` or
`negotiation.messages` block.

All the evidence a re-judge needs is durable:

* the downstream's structured reading (``serverIntent``) and the SDK extraction (``intent``);
* the generated Task-T text (``task_t.actual``);
* the intercepted slot errors (``validate.detail.slotErrors``);
* the server's negotiation messages, joined from ``negotiation_events`` by runId.

This module turns that evidence plus one case document into per-dimension verdicts. It deliberately
reuses :mod:`eval.osp.compare` and :mod:`eval.osp.expectations`, so the review page cannot drift
from the runner and the tester.

Two case shapes need care:

* a ``blocked`` case with a ``blocked_followup`` runs twice. The interception segment is judged
  against the *original* case (it is the block the case expects), while the follow-up run is judged
  against :func:`eval.osp.followup.followup_case`, exactly like the tester;
* a case that negotiates is judged on rounds/kinds and (when the corpus states them) the exact
  messages, but the final intent is still the downstream's reading of the **original** Task-T.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from eval.osp.compare import INTENT_ROW_SAME, compare_task_t, flatten_intent, flatten_task_t
from eval.osp.expectations import (
    A2A_T_PROTOCOL,
    match_negotiation_messages,
    negotiation_message_expectation,
    slot_error_evidence,
)
from eval.osp.followup import followup_case, has_followup, is_blocked_case

#: Review dimensions. ``intent`` and ``task_t`` are the two the acceptance cares about most.
DIMENSION_INTENT: Final[str] = "intent"
DIMENSION_TASK_T: Final[str] = "task_t"
DIMENSION_VALIDATE: Final[str] = "validate"
DIMENSION_NEGOTIATION: Final[str] = "negotiation"
DIMENSION_FINAL: Final[str] = "final_state"

DIMENSIONS: Final[tuple[str, ...]] = (
    DIMENSION_INTENT,
    DIMENSION_TASK_T,
    DIMENSION_VALIDATE,
    DIMENSION_NEGOTIATION,
    DIMENSION_FINAL,
)

#: One dimension's result. ``recorded`` means "evidence exists but this run cannot judge it"
#: (simulator, proxy hop, a blocked-before-Task-T segment); ``missing`` means the evidence needed to
#: judge it was never stored, which is a prompt to re-run rather than a pass.
VERDICT_PASS: Final[str] = "pass"
VERDICT_FAIL: Final[str] = "fail"
VERDICT_PARTIAL: Final[str] = "partial"
VERDICT_RECORDED: Final[str] = "recorded"
VERDICT_MISSING: Final[str] = "missing"
VERDICT_NOT_EXPECTED: Final[str] = "not_expected"
VERDICT_NOT_APPLICABLE: Final[str] = "not_applicable"

_VERDICTS: Final[tuple[str, ...]] = (
    VERDICT_PASS,
    VERDICT_FAIL,
    VERDICT_PARTIAL,
    VERDICT_RECORDED,
    VERDICT_MISSING,
    VERDICT_NOT_EXPECTED,
    VERDICT_NOT_APPLICABLE,
)


@dataclass(frozen=True)
class DimensionResult:
    """One dimension of one protocol task, judged against the current corpus."""

    dimension: str
    status: str
    expected: object = None
    actual: object = None
    diffs: tuple[dict[str, object], ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation used by the page and the report."""
        return {
            "dimension": self.dimension,
            "status": self.status,
            "expected": self.expected,
            "actual": self.actual,
            "diffs": [dict(diff) for diff in self.diffs],
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ProtocolJudgement:
    """The review verdict of one case × protocol, with the evidence the page renders."""

    case_id: int
    protocol: str
    status: str
    dimensions: tuple[DimensionResult, ...]
    detail: Mapping[str, object]

    def dimension(self, name: str) -> DimensionResult | None:
        """Return one dimension result by name."""
        for row in self.dimensions:
            if row.dimension == name:
                return row
        return None

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation used by the page and the store."""
        return {
            "caseId": self.case_id,
            "protocol": self.protocol,
            "status": self.status,
            "dimensions": [row.to_dict() for row in self.dimensions],
            "detail": dict(self.detail),
        }


def judge_protocol(
    case: Mapping[str, Any],
    protocol: str,
    evidence: Mapping[str, Any],
) -> ProtocolJudgement:
    """Judge one stored observation against one case document.

    Args:
        case: the current case document (never a frozen copy).
        protocol: ``a2a-t`` or ``a2a``.
        evidence: one tester item, plus ``messages`` when the caller joined the run store.
    """
    effective = _effective_case(case, protocol)
    dimensions = (
        _judge_intent(effective, protocol, evidence),
        _judge_task_t(effective, protocol, evidence),
        _judge_validate(case, effective, protocol, evidence),
        _judge_negotiation(effective, protocol, evidence),
        _judge_final(effective, protocol, evidence),
    )
    asserted = _asserted_dimensions(case, protocol)
    return ProtocolJudgement(
        case_id=_int_value(case.get("id")),
        protocol=protocol,
        status=_overall_status(dimensions, asserted),
        dimensions=dimensions,
        detail=_detail_payload(case, effective, protocol, evidence),
    )


# ---------------------------------------------------------------------------
# Case helpers
# ---------------------------------------------------------------------------


def case_signature(case: Mapping[str, Any]) -> str:
    """Return a stable fingerprint of the parts of a case a verdict depends on.

    A decision or observation made against a different signature is stale: the acceptance must be
    re-confirmed when the input or any expectation changes, even if the files still parse.
    """
    payload = {
        "userInput": case.get("user_input"),
        "expect": case.get("expect"),
        "assert": case.get("assert"),
        "blockedFollowup": case.get("blocked_followup"),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def input_signature(case: Mapping[str, Any]) -> str:
    """Return the fingerprint of just the operator request a run was produced from."""
    return hashlib.sha256(
        str(case.get("user_input") or "").encode("utf-8")
    ).hexdigest()[:16]


def evidence_input_signature(evidence: Mapping[str, Any]) -> str:
    """Return the input fingerprint of the case a stored run was produced from.

    ``refs.userInput`` is frozen by the tester at run time, so it survives a catalog edit and is
    the only reliable way to tell "same input, updated expectations" from "different input, the
    evidence is no longer about this case".
    """
    refs = evidence.get("refs")
    value = refs.get("userInput") if isinstance(refs, Mapping) else None
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def evidence_is_current_input(
    case: Mapping[str, Any],
    protocol: str,
    evidence: Mapping[str, Any],
) -> bool:
    """Report whether the stored run used the input the current case would dispatch.

    A blocked case's evidence comes from its follow-up request, so the comparison is against the
    effective case, not the operator's original text.
    """
    return evidence_input_signature(evidence) == input_signature(
        _effective_case(case, protocol)
    )


def _effective_case(case: Mapping[str, Any], protocol: str) -> Mapping[str, Any]:
    """Return the case a run of this protocol should be judged against.

    A blocked case with a follow-up dispatches the replacement request, whose derived document
    carries the follow-up's validation/final-state expectations. The interception segment is still
    judged against the original case by :func:`_judge_validate`.
    """
    if protocol != A2A_T_PROTOCOL:
        return case
    if is_blocked_case(case) and has_followup(case):
        return followup_case(case)
    return case


def _protocol_block(case: Mapping[str, Any], protocol: str) -> Mapping[str, Any]:
    """Return one protocol's expectation block of one case."""
    expect = case.get("expect")
    protocols = expect.get("protocols") if isinstance(expect, Mapping) else None
    block = protocols.get(protocol) if isinstance(protocols, Mapping) else None
    return block if isinstance(block, Mapping) else {}


def _asserted_dimensions(case: Mapping[str, Any], protocol: str) -> set[str]:
    """Return the dimensions the case asks to judge, mirroring its ``assert`` block."""
    flags = _mapping(case.get("assert"))
    nested = _mapping(flags.get(protocol))
    asserted: set[str] = set()
    if protocol == A2A_T_PROTOCOL:
        if _flag(flags.get("intent"), True):
            asserted.add(DIMENSION_INTENT)
    for dimension in (DIMENSION_TASK_T, DIMENSION_VALIDATE, DIMENSION_NEGOTIATION, DIMENSION_FINAL):
        if _flag(nested.get(dimension), True):
            asserted.add(dimension)
    return asserted


def _overall_status(
    dimensions: Sequence[DimensionResult],
    asserted: set[str],
) -> str:
    """Derive the protocol verdict from its asserted dimensions."""
    relevant = [
        row
        for row in dimensions
        if row.dimension in asserted
        and row.status not in (VERDICT_NOT_APPLICABLE, VERDICT_NOT_EXPECTED)
    ]
    if any(row.status == VERDICT_FAIL for row in relevant):
        return VERDICT_FAIL
    if any(row.status in (VERDICT_MISSING, VERDICT_RECORDED) for row in relevant):
        return VERDICT_PARTIAL
    if relevant and all(row.status == VERDICT_PASS for row in relevant):
        return VERDICT_PASS
    return VERDICT_NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------


def _judge_intent(
    case: Mapping[str, Any],
    protocol: str,
    evidence: Mapping[str, Any],
) -> DimensionResult:
    """Judge the downstream's structured reading of the Task-T against the corpus."""
    expected = _mapping(_mapping(case.get("expect")).get("intent"))
    server = _mapping_or_none(evidence.get("serverIntent"))
    if protocol != A2A_T_PROTOCOL:
        # The pure-A2A hop is a proxy: its "intent" is a record, never an acceptance criterion.
        return DimensionResult(
            DIMENSION_INTENT,
            VERDICT_NOT_APPLICABLE,
            expected=expected,
            actual=server,
            reason="A2A 链路的意图仅记录，不作为验收判定",
        )
    if not expected:
        return DimensionResult(DIMENSION_INTENT, VERDICT_NOT_APPLICABLE)
    if server is None:
        return DimensionResult(
            DIMENSION_INTENT,
            VERDICT_MISSING,
            expected=expected,
            reason="未采集到下游服务端的结构化意图",
        )
    stage = _stage(evidence, "intent")
    if str(stage.get("status") or "") == "recorded":
        return DimensionResult(
            DIMENSION_INTENT,
            VERDICT_RECORDED,
            expected=expected,
            actual=server,
            reason=str(stage.get("message") or "该运行不能判定意图（模拟/代理/无直连 payload）"),
        )
    rows = [row.to_dict() for row in flatten_intent(expected, server)]
    diffs = tuple(row for row in rows if row.get("verdict") != INTENT_ROW_SAME)
    return DimensionResult(
        DIMENSION_INTENT,
        VERDICT_PASS if not diffs else VERDICT_FAIL,
        expected=expected,
        actual=server,
        diffs=diffs,
        reason="" if not diffs else f"{len(diffs)} 个意图字段与预期不一致",
    )


def _judge_task_t(
    case: Mapping[str, Any],
    protocol: str,
    evidence: Mapping[str, Any],
) -> DimensionResult:
    """Judge the generated Task-T against the corpus section by section."""
    if protocol != A2A_T_PROTOCOL:
        return DimensionResult(DIMENSION_TASK_T, VERDICT_NOT_APPLICABLE)
    expected = _protocol_block(case, A2A_T_PROTOCOL).get("task_t")
    if not isinstance(expected, str) or not expected.strip():
        return DimensionResult(DIMENSION_TASK_T, VERDICT_NOT_EXPECTED)
    stage = _stage(evidence, "task_t")
    actual = stage.get("actual")
    if not isinstance(actual, str) or not actual.strip() or actual.startswith("<blocked"):
        return DimensionResult(
            DIMENSION_TASK_T,
            VERDICT_MISSING,
            expected=expected,
            actual=actual,
            reason=str(stage.get("message") or "该运行没有生成的 Task-T 证据"),
        )
    diffs = tuple(diff.to_dict() for diff in compare_task_t(expected, actual))
    return DimensionResult(
        DIMENSION_TASK_T,
        VERDICT_PASS if not diffs else VERDICT_FAIL,
        expected=expected,
        actual=actual,
        diffs=diffs,
        reason="" if not diffs else f"{len(diffs)} 个 Task-T 小节与预期不一致",
    )


def _judge_validate(
    case: Mapping[str, Any],
    effective: Mapping[str, Any],
    protocol: str,
    evidence: Mapping[str, Any],
) -> DimensionResult:
    """Judge the interception: verdict plus the expected slot errors."""
    if protocol != A2A_T_PROTOCOL:
        return DimensionResult(DIMENSION_VALIDATE, VERDICT_NOT_APPLICABLE)
    stage = _stage(evidence, "validate")
    owner = effective
    if is_blocked_case(case) and has_followup(case):
        # The case's slot errors describe the original request; the follow-up's validation is a
        # consequence of passing the interception, not the assertion itself.
        interception = _interception_stage(evidence)
        if interception is not None:
            stage = interception
            owner = case
    expected = _protocol_block(owner, A2A_T_PROTOCOL).get("validate")
    if not isinstance(expected, str):
        return DimensionResult(DIMENSION_VALIDATE, VERDICT_NOT_EXPECTED)
    detail = _mapping(stage.get("detail"))
    actual = str(detail.get("verdict") or stage.get("actual") or "")
    if not actual:
        return DimensionResult(
            DIMENSION_VALIDATE,
            VERDICT_MISSING,
            expected=expected,
            reason="该运行没有校验结论",
        )
    errors = detail.get("slotErrors")
    observed = [dict(error) for error in errors if isinstance(error, Mapping)] if isinstance(errors, list) else []
    slot_evidence = slot_error_evidence(owner, expected, actual, observed)
    verdict_ok = _verdict_matches(expected, actual)
    matched = slot_evidence is None or slot_evidence.matched()
    diffs = list(slot_evidence.diffs()) if slot_evidence is not None else []
    if not verdict_ok:
        diffs.insert(
            0,
            {
                "path": "validate.verdict",
                "expected": expected,
                "actual": actual,
                "reason": "校验结论与预期不一致",
            },
        )
    return DimensionResult(
        DIMENSION_VALIDATE,
        VERDICT_PASS if verdict_ok and matched else VERDICT_FAIL,
        expected=expected,
        actual=actual,
        diffs=tuple(diffs),
        reason="" if verdict_ok and matched else "拦截结论或 slot 错误与预期不一致",
    )


def _judge_negotiation(
    case: Mapping[str, Any],
    protocol: str,
    evidence: Mapping[str, Any],
) -> DimensionResult:
    """Judge rounds, kinds and (when the corpus states them) the server messages."""
    negotiation = _mapping(_protocol_block(case, protocol).get("negotiation"))
    expected_rounds = _int_value(negotiation.get("expected_rounds"))
    expected = bool(negotiation.get("expected")) or expected_rounds > 0
    if not expected:
        return DimensionResult(DIMENSION_NEGOTIATION, VERDICT_NOT_EXPECTED)
    stage = _stage(evidence, "negotiate")
    actual = _mapping(stage.get("actual"))
    observed_rounds = _int_value(actual.get("rounds"))
    observed_kinds = _strings(actual.get("kinds"))
    kinds = negotiation.get("kinds")
    expected_kinds = _strings(kinds) if isinstance(kinds, list) else None
    rounds_ok = observed_rounds == expected_rounds
    kinds_ok = expected_kinds is None or all(kind in observed_kinds for kind in expected_kinds)
    diffs: list[dict[str, object]] = []
    if not rounds_ok:
        diffs.append(
            {
                "path": "negotiation.rounds",
                "expected": expected_rounds,
                "actual": observed_rounds,
                "reason": "协商轮数与预期不一致",
            }
        )
    if not kinds_ok:
        diffs.append(
            {
                "path": "negotiation.kinds",
                "expected": expected_kinds,
                "actual": observed_kinds,
                "reason": "协商类型与预期不一致",
            }
        )
    messages = _observed_messages(evidence, actual)
    expectation = negotiation_message_expectation(case, protocol)
    if expectation is None:
        return DimensionResult(
            DIMENSION_NEGOTIATION,
            VERDICT_PASS if rounds_ok and kinds_ok else VERDICT_FAIL,
            expected={"rounds": expected_rounds, "kinds": expected_kinds},
            actual={"rounds": observed_rounds, "kinds": observed_kinds},
            diffs=tuple(diffs),
            reason="" if rounds_ok and kinds_ok else "协商轮次或类型与预期不一致",
        )
    if not messages:
        return DimensionResult(
            DIMENSION_NEGOTIATION,
            VERDICT_MISSING,
            expected={"rounds": expected_rounds, "messages": [list(r) for r in expectation.rounds]},
            actual={"rounds": observed_rounds, "kinds": observed_kinds, "messages": []},
            diffs=tuple(diffs),
            reason="该批次没有保存协商报文证据（旧批次或缺少 run 存储），需重跑",
        )
    message_match = match_negotiation_messages(expectation, messages)
    if not message_match.matched:
        diffs.extend(
            {
                "path": f"negotiation.messages[{round_match.round}]",
                "expected": list(round_match.expected),
                "actual": round_match.actual,
                "reason": round_match.reason,
            }
            for round_match in message_match.failed_rounds()
        )
    ok = rounds_ok and kinds_ok and message_match.matched
    return DimensionResult(
        DIMENSION_NEGOTIATION,
        VERDICT_PASS if ok else VERDICT_FAIL,
        expected={
            "rounds": expected_rounds,
            "kinds": expected_kinds,
            "messages": [list(r) for r in expectation.rounds],
        },
        actual={
            "rounds": observed_rounds,
            "kinds": observed_kinds,
            "messages": messages,
        },
        diffs=tuple(diffs),
        reason="" if ok else "协商轮次、类型或报文与预期不一致",
    )


def _judge_final(
    case: Mapping[str, Any],
    protocol: str,
    evidence: Mapping[str, Any],
) -> DimensionResult:
    """Judge the final task state, when the case states one."""
    expected = _protocol_block(case, protocol).get("final_state")
    if expected is None:
        return DimensionResult(DIMENSION_FINAL, VERDICT_NOT_EXPECTED)
    stage = _stage(evidence, "final")
    actual = stage.get("actual")
    if actual is None or actual == "":
        return DimensionResult(
            DIMENSION_FINAL,
            VERDICT_MISSING,
            expected=expected,
            reason="该运行没有终态证据",
        )
    return DimensionResult(
        DIMENSION_FINAL,
        VERDICT_PASS if str(actual) == str(expected) else VERDICT_FAIL,
        expected=expected,
        actual=actual,
        diffs=(
            ()
            if str(actual) == str(expected)
            else (
                {
                    "path": "final_state",
                    "expected": expected,
                    "actual": actual,
                    "reason": "终态与预期不一致",
                },
            )
        ),
        reason="" if str(actual) == str(expected) else "终态与预期不一致",
    )


# ---------------------------------------------------------------------------
# Evidence / detail payloads
# ---------------------------------------------------------------------------


def _detail_payload(
    case: Mapping[str, Any],
    effective: Mapping[str, Any],
    protocol: str,
    evidence: Mapping[str, Any],
) -> dict[str, object]:
    """Build the page payload: the three-way intent table and each evidence view."""
    expected_intent = _mapping(_mapping(effective.get("expect")).get("intent"))
    server = _mapping_or_none(evidence.get("serverIntent"))
    sdk = _mapping_or_none(evidence.get("intent"))
    intent_rows = (
        [row.to_dict() for row in flatten_intent(expected_intent, server)]
        if expected_intent and server
        else []
    )
    sdk_rows = (
        [row.to_dict() for row in flatten_intent(expected_intent, sdk)]
        if protocol == A2A_T_PROTOCOL and expected_intent and sdk
        else []
    )
    expected_task_t = _protocol_block(effective, A2A_T_PROTOCOL).get("task_t")
    actual_task_t = _stage(evidence, "task_t").get("actual")
    task_t_rows = (
        [row.to_dict() for row in flatten_task_t(expected_task_t, actual_task_t)]
        if isinstance(expected_task_t, str) and isinstance(actual_task_t, str)
        else []
    )
    validate_stage = (
        _interception_stage(evidence) or _stage(evidence, "validate")
        if protocol == A2A_T_PROTOCOL and is_blocked_case(case) and has_followup(case)
        else _stage(evidence, "validate")
    )
    validate_detail = _mapping(validate_stage.get("detail"))
    errors = validate_detail.get("slotErrors")
    negotiation = _mapping(_protocol_block(effective, protocol).get("negotiation"))
    negotiate_actual = _mapping(_stage(evidence, "negotiate").get("actual"))
    messages = _observed_messages(evidence, negotiate_actual)
    expectation = negotiation_message_expectation(effective, protocol)
    message_match: object = None
    if expectation is not None and messages:
        message_match = match_negotiation_messages(expectation, messages).to_dict()
    return {
        "blocked": is_blocked_case(case),
        "followup": effective is not case,
        "caseSignature": case_signature(case),
        "intent": {
            "expected": expected_intent,
            "sdk": sdk,
            "server": server,
            "rows": intent_rows,
            "sdkRows": sdk_rows,
        },
        "taskT": {
            "expected": expected_task_t,
            "actual": actual_task_t,
            "rows": task_t_rows,
        },
        "validate": {
            "expected": _protocol_block(case if is_blocked_case(case) else effective, A2A_T_PROTOCOL).get("validate"),
            "actual": validate_detail.get("verdict") or validate_stage.get("actual"),
            "errors": [dict(error) for error in errors if isinstance(error, Mapping)]
            if isinstance(errors, list)
            else [],
            "detail": dict(validate_detail),
        },
        "negotiation": {
            "expectedRounds": _int_value(negotiation.get("expected_rounds")),
            "kinds": negotiation.get("kinds"),
            "expectedMessages": [list(r) for r in expectation.rounds] if expectation else [],
            "actual": dict(negotiate_actual),
            "messages": messages,
            "messageMatch": message_match,
        },
        "runtime": {
            "send": _stage_value(evidence, "send"),
            "accepted": _stage_value(evidence, "accepted"),
            "working": _stage_value(evidence, "working"),
            "final": _stage_value(evidence, "final"),
        },
    }


def _interception_stage(evidence: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return the interception segment's validate stage, when the item carries one."""
    interception = evidence.get("interception")
    stages = interception.get("stages") if isinstance(interception, Mapping) else None
    stage = stages.get("validate") if isinstance(stages, Mapping) else None
    return stage if isinstance(stage, Mapping) else None


def _stage(evidence: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    """Return one stage of the stored item, or an empty mapping."""
    stages = evidence.get("stages")
    stage = stages.get(name) if isinstance(stages, Mapping) else None
    return stage if isinstance(stage, Mapping) else {}


def _stage_value(evidence: Mapping[str, Any], name: str) -> object:
    """Return one stage's actual value, or ``None``."""
    return _stage(evidence, name).get("actual")


def _observed_messages(
    evidence: Mapping[str, Any],
    negotiate_actual: Mapping[str, Any],
) -> list[dict[str, object]]:
    """Return the observed server messages, preferring the run-store join.

    The tester only stored the messages when the case already expected them; the review console
    joins ``negotiation_events`` by runId instead, which makes batches that predate the message
    expectation usable without a re-run.
    """
    joined = evidence.get("messages")
    if isinstance(joined, Sequence) and not isinstance(joined, (str, bytes)):
        rows = [dict(row) for row in joined if isinstance(row, Mapping)]
        if rows:
            return rows
    stored = negotiate_actual.get("messages")
    if isinstance(stored, Sequence) and not isinstance(stored, (str, bytes)):
        return [dict(row) for row in stored if isinstance(row, Mapping)]
    return []


def _verdict_matches(expected: str, actual: str) -> bool:
    """Return whether an observed validation verdict satisfies the expected one."""
    if expected == "pass_with_terminology":
        return actual in {"pass", "pass_with_terminology"}
    return expected == actual


# ---------------------------------------------------------------------------
# Small typed helpers
# ---------------------------------------------------------------------------


def _mapping(value: object) -> dict[str, Any]:
    """Return a plain mapping, or an empty one."""
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_or_none(value: object) -> dict[str, Any] | None:
    """Return a plain mapping, or ``None``."""
    return dict(value) if isinstance(value, Mapping) else None


def _strings(value: object) -> list[str]:
    """Return a list of non-empty strings, or an empty list."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _int_value(value: object) -> int:
    """Return an integer, or zero."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _flag(value: object, default: bool) -> bool:
    """Return a boolean assertion flag, keeping the default for anything that is not a boolean."""
    return value if isinstance(value, bool) else default


__all__ = [
    "DIMENSIONS",
    "DIMENSION_FINAL",
    "DIMENSION_INTENT",
    "DIMENSION_NEGOTIATION",
    "DIMENSION_TASK_T",
    "DIMENSION_VALIDATE",
    "DimensionResult",
    "ProtocolJudgement",
    "VERDICT_FAIL",
    "VERDICT_MISSING",
    "VERDICT_NOT_APPLICABLE",
    "VERDICT_NOT_EXPECTED",
    "VERDICT_PARTIAL",
    "VERDICT_PASS",
    "VERDICT_RECORDED",
    "case_signature",
    "evidence_input_signature",
    "evidence_is_current_input",
    "input_signature",
    "judge_protocol",
]
