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
  against :func:`eval.osp.followup.followup_case`, exactly like the tester. When the original
  request was not intercepted, the follow-up is never dispatched (``followupSkipped``): the
  original request runs instead (``originalDispatched``) or no run happens at all. The case is
  judged on its interception alone, and the follow-up dimensions are recorded or not applicable;
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
from eval.osp.tracecheck import (
    TRACE_REASONS,
    Fidelity,
    TraceApplicability,
    TraceContext,
    dispatched_task_t,
    intent_corroboration,
    intent_params,
    judge_trace,
    nms_kind,
    process_points,
    task_t_fidelity,
    trace_applicability,
)

#: Review dimensions. ``intent`` and ``task_t`` are the two the acceptance cares about most.
DIMENSION_INTENT: Final[str] = "intent"
DIMENSION_TASK_T: Final[str] = "task_t"
DIMENSION_VALIDATE: Final[str] = "validate"
DIMENSION_NEGOTIATION: Final[str] = "negotiation"
DIMENSION_FINAL: Final[str] = "final_state"
DIMENSION_TRACE: Final[str] = "trace"

DIMENSIONS: Final[tuple[str, ...]] = (
    DIMENSION_INTENT,
    DIMENSION_TASK_T,
    DIMENSION_VALIDATE,
    DIMENSION_NEGOTIATION,
    DIMENSION_FINAL,
    DIMENSION_TRACE,
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

#: Status of a real-machine run whose trace was never exported. A missing snapshot is missing
#: evidence, not a failed gate: reporting it as ``fail`` painted every real batch red before anyone
#: had collected a trace, which buried the actual mismatches. It is ``missing`` (partial) instead;
#: a snapshot that *was* collected and disagrees with the run still fails in :mod:`tracecheck`.
TRACE_NOT_EXPORTED_STATUS: Final[str] = VERDICT_MISSING


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
        _judge_trace(case, effective, protocol, evidence),
    )
    miss = _interception_miss(case, protocol, evidence)
    if miss is not None:
        # The corpus expectations describe the replacement request, which a missed interception
        # never dispatches. The case verdict is the interception: the replacement dimensions are
        # recorded when the original request went downstream instead, and not applicable when no
        # run happened at all. "missing" would make either read as an incomplete batch.
        dispatched = miss == "dispatched"
        dimensions = tuple(
            row
            if row.dimension == DIMENSION_VALIDATE
            else DimensionResult(
                row.dimension,
                VERDICT_RECORDED if dispatched else VERDICT_NOT_APPLICABLE,
                expected=row.expected,
                actual=row.actual,
                reason=_MISS_DISPATCHED_REASON if dispatched else _MISS_SKIPPED_REASON,
            )
            for row in dimensions
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


def evidence_input_signature(evidence: Mapping[str, Any]) -> str:
    """Return the fingerprint of the operator request a stored run was produced from.

    ``refs.userInput`` is frozen by the tester at run time. It is recorded as provenance of the
    evidence, not as a gate: a case may be edited in place between runs (the yellow zone does this),
    and a verdict is identified by the case id it was judged under.
    """
    refs = evidence.get("refs")
    value = refs.get("userInput") if isinstance(refs, Mapping) else None
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


#: Reason of every non-interception dimension when the original request ran instead.
_MISS_DISPATCHED_REASON: Final[str] = (
    "拦截未生效，已按原请求下发；用例期望描述的是补充请求，下游证据只记录、不判定。"
)
#: Reason of every non-interception dimension when a missed interception started no run.
_MISS_SKIPPED_REASON: Final[str] = (
    "原请求未被拦截且未下发，补充请求（blocked_followup.input）未执行；本用例只判定拦截段。"
)


def _interception_miss(
    case: Mapping[str, Any], protocol: str, evidence: Mapping[str, Any]
) -> str | None:
    """Return ``dispatched`` / ``skipped`` for a missed interception, else ``None``.

    ``dispatched`` is a full-flow item that sent the original request downstream instead of the
    replacement; ``skipped`` is an SDK-only item that started no run.
    """
    if protocol != A2A_T_PROTOCOL or not (is_blocked_case(case) and has_followup(case)):
        return None
    record = _mapping(evidence.get("interception"))
    if record.get("originalDispatched") is True:
        return "dispatched"
    return "skipped" if record.get("followupSkipped") is True else None


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
    # The intent expectation is shared by both protocols: the downstream's reading of the request
    # is the same business fact whether it arrived over A2A-T or pure A2A. A run whose evidence
    # cannot support the comparison stays ``not_applicable`` and is excluded by _overall_status.
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
    """Derive the protocol verdict from its asserted dimensions.

    The trace dimension is asserted by the *evidence*, not by the corpus: it applies to a run that
    actually dispatched to the real vendor NMS. Keeping it out of ``case_signature`` matters — a
    corpus-wide opt-in would invalidate every stored decision.
    """
    trace = next((row for row in dimensions if row.dimension == DIMENSION_TRACE), None)
    if trace is not None and trace.status not in (VERDICT_NOT_APPLICABLE, VERDICT_NOT_EXPECTED):
        asserted = asserted | {DIMENSION_TRACE}
    relevant = [
        row
        for row in dimensions
        if row.dimension in asserted and row.status not in (VERDICT_NOT_APPLICABLE, VERDICT_NOT_EXPECTED)
    ]
    if any(row.status == VERDICT_FAIL for row in relevant):
        return VERDICT_FAIL
    if any(row.status in (VERDICT_MISSING, VERDICT_RECORDED, VERDICT_PARTIAL) for row in relevant):
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
    """Judge the downstream's structured reading of the Task-T against the corpus.

    The reading is the downstream's own payload, not our SDK extraction, so it is judgeable on
    either protocol as long as the run actually got a direct payload. For pure A2A the tester
    decides that: a simulator or OSS-proxy hop stays ``recorded``, a direct vendor payload is
    judged. The review mirrors the stage instead of assuming every A2A hop is a proxy.
    """
    expected = _mapping(_mapping(case.get("expect")).get("intent"))
    server = _mapping_or_none(evidence.get("serverIntent"))
    stage = _stage(evidence, "intent")
    status = str(stage.get("status") or "")
    if not expected:
        return DimensionResult(DIMENSION_INTENT, VERDICT_NOT_APPLICABLE)
    if protocol == A2A_T_PROTOCOL:
        if server is None:
            return DimensionResult(
                DIMENSION_INTENT,
                VERDICT_MISSING,
                expected=expected,
                reason="未采集到下游服务端的结构化意图",
            )
        if status == "recorded":
            return DimensionResult(
                DIMENSION_INTENT,
                VERDICT_RECORDED,
                expected=expected,
                actual=server,
                reason=str(stage.get("message") or "该运行不能判定意图（模拟/代理/无直连 payload）"),
            )
        return _intent_verdict(expected, server)
    if status == "recorded":
        return DimensionResult(
            DIMENSION_INTENT,
            VERDICT_RECORDED,
            expected=expected,
            actual=server,
            reason=str(stage.get("message") or "A2A 链路的意图仅记录，不作为验收判定"),
        )
    if status in {"passed", "failed"} and server is not None:
        return _intent_verdict(expected, server)
    # Older batches and runs without a payload never got a comparison stage; there is nothing to
    # judge, and inventing a verdict from an absent reading would be worse than saying so.
    return DimensionResult(
        DIMENSION_INTENT,
        VERDICT_NOT_APPLICABLE,
        expected=expected,
        actual=server,
        reason="A2A 链路没有可判定的服务端意图证据",
    )


def _intent_verdict(
    expected: Mapping[str, object],
    server: Mapping[str, object],
) -> DimensionResult:
    """Compare the corpus reading with the downstream's reading, leaf by leaf."""
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
    """Judge the outcome of the task: the final state, or the acceptance that ends the batch.

    The automated batches stop at ``accepted``: a Create answers "Task created success" and a Modify
    "Task modify success", and that reply is the success point they are run for. The corpus
    ``final_state`` of a case is a derived default, not an authored expectation, so when the batch
    never ran the final stage the accepted stage decides this dimension instead of leaving it
    missing.
    """
    expected = _protocol_block(case, protocol).get("final_state")
    if expected is None:
        return DimensionResult(DIMENSION_FINAL, VERDICT_NOT_EXPECTED)
    stage = _stage(evidence, "final")
    actual = stage.get("actual")
    if actual is None or actual == "":
        if stage:
            # The batch did run the final stage but the run ended without a state: that is real
            # missing evidence, not a batch configured to stop at acceptance.
            return DimensionResult(
                DIMENSION_FINAL,
                VERDICT_MISSING,
                expected=expected,
                reason=str(stage.get("message") or "该运行没有终态证据"),
            )
        accepted = _stage(evidence, "accepted")
        accepted_status = str(accepted.get("status") or "")
        accepted_actual = accepted.get("actual")
        if accepted_status == "passed":
            return DimensionResult(
                DIMENSION_FINAL,
                VERDICT_PASS,
                expected=expected,
                actual=accepted_actual,
                reason="该批次停在受理阶段，以 accepted（Task created / modify success）作为成功点",
            )
        if accepted_status in ("failed", "error"):
            return DimensionResult(
                DIMENSION_FINAL,
                VERDICT_FAIL,
                expected=expected,
                actual=accepted_actual,
                reason=str(accepted.get("message") or "受理阶段没有成功"),
            )
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


def _judge_trace(
    case: Mapping[str, Any],
    effective: Mapping[str, Any],
    protocol: str,
    evidence: Mapping[str, Any],
) -> DimensionResult:
    """Judge the Langfuse trace of a dispatched run.

    Applicability is decided by the run itself: a run with a trace id is audited. The declared
    downstream is display only, so a batch produced before the tester recorded ``nms``/``nmsSim``
    still reaches the gate instead of silently abstaining. A declared simulator, an SDK-only or
    preflight item and a run without a usable trace id stay out of the gate with a reason. The
    point model, attribution and status mapping live in :mod:`eval.osp.tracecheck`; this function
    only turns the stored evidence into its context, so the console and the collector cannot drift
    apart.
    """
    batch = _mapping(evidence.get("batch"))
    applicability = trace_applicability(
        evidence.get("runId"),
        evidence.get("traceId"),
        simulated=batch.get("nmsSim") is True,
    )
    if not applicability.applicable:
        return DimensionResult(
            DIMENSION_TRACE,
            VERDICT_NOT_APPLICABLE,
            reason=applicability.text,
            diffs=(_trace_diff("trace.applicability", None, None, applicability.code),),
        )
    run_id = applicability.run_id
    trace = evidence.get("trace")
    payload: Mapping[str, Any] = trace if isinstance(trace, Mapping) else {"exportState": "not_exported"}
    judgement = judge_trace(
        payload,
        _trace_context(case, effective, protocol, evidence, run_id),
        not_exported_status=TRACE_NOT_EXPORTED_STATUS,
    )
    diffs: tuple[dict[str, object], ...] = tuple(
        {
            "path": f"trace.data.{finding.point}",
            "expected": finding.label,
            "actual": finding.status,
            "reason": judgement.reason,
            "detail": finding.detail,
        }
        for finding in judgement.findings
    )
    if not diffs and judgement.reason:
        diffs = (_trace_diff("trace", None, None, judgement.reason),)
    frames = _mapping(judgement.frames.get("nms"))
    return DimensionResult(
        DIMENSION_TRACE,
        judgement.status,
        expected=_optional_int(frames.get("expected")),
        actual=_optional_int(frames.get("observed")),
        diffs=diffs,
        reason=_TRACE_REASON_TEXT.get(judgement.reason, ""),
    )


#: Human text of the trace reason codes; the code itself travels in the diffs.
_TRACE_REASON_TEXT: Final[dict[str, str]] = {
    "trace_not_exported": "轨迹未导出",
    "trace_frames_mismatch": "NMS 跳的发送帧数不符",
    "trace_missing_client": "预期的轨迹点缺失（本机或客户端）",
    "trace_missing_ingest": "本地已发射但未入库",
    "trace_missing_vendor": "厂商侧轨迹点缺失",
    "trace_content_unavailable": "轨迹内容捕获关闭，仅判是否存在",
    "trace_nms_hop_unknown": "无法从轨迹确定 NMS 跳",
    "trace_data_missing": "必选关键数据在导出轨迹中缺失",
    "trace_optional_data_missing": "可选关键数据在导出轨迹中缺失",
}


def _trace_context(
    case: Mapping[str, Any],
    effective: Mapping[str, Any],
    protocol: str,
    evidence: Mapping[str, Any],
    run_id: str,
) -> TraceContext:
    """Return the run facts the pure trace judge needs.

    ``validation_on`` is deliberately conservative: the tester only runs the client validation
    gate when the case forces ``always`` (the process setting defaults to presentation-only), and
    expecting a point that was never emitted would fail a correct run. Under-expecting is safe;
    over-expecting is not.
    """
    runner = _mapping(case.get("runner"))
    stages = _mapping(evidence.get("stages"))
    rounds, _source = _trace_rounds(effective, protocol, evidence)
    return TraceContext(
        protocol=protocol,
        run_mode=str(case.get("operation") or "").strip().lower(),
        blocked=is_blocked_case(case),
        followup=has_followup(case),
        rounds=rounds,
        accepted=_run_accepted(stages),
        validation_on=runner.get("pre_send_validation") == "always",
        run_id=run_id,
    )


def _trace_rounds(
    case: Mapping[str, Any],
    protocol: str,
    evidence: Mapping[str, Any],
) -> tuple[int, str]:
    """Return the rounds the run actually negotiated, and where the number came from.

    The trace gate asks "did every span this run produced reach Langfuse", not "did the run
    negotiate the corpus' expected number of rounds" — that is the negotiation dimension's job.
    Using the expected count here would report a functional shortfall as missing spans.
    """
    actual = _mapping(_stage(evidence, "negotiate").get("actual"))
    value = actual.get("rounds")
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value), "observed"
    messages = evidence.get("messages")
    if isinstance(messages, list) and messages:
        numbers = [_int_value(message.get("round")) for message in messages if isinstance(message, Mapping)]
        return max(0, max(numbers, default=0)), "messages"
    return _expected_rounds(case, protocol), "expected"


def _run_accepted(stages: Mapping[str, Any]) -> bool:
    """Return whether the run was accepted (or reached a working/final state after it).

    The presence of an ``accepted`` stage is not enough: the tester records it as failed or
    waiting in some flows, and requiring a ``task_created`` point then fails a correct run.
    """
    for name in ("accepted", "working"):
        if str(_mapping(stages.get(name)).get("status") or "") == "passed":
            return True
    final = _mapping(stages.get("final"))
    return str(final.get("actual") or "") in {"task_completed", "task_modified"}


def _expected_rounds(case: Mapping[str, Any], protocol: str) -> int:
    """Return the negotiation rounds the corpus expects of one protocol."""
    negotiation = _mapping(_protocol_block(case, protocol).get("negotiation"))
    return _int_value(negotiation.get("expected_rounds"))


def _trace_diff(path: str, expected: object, actual: object, code: str) -> dict[str, object]:
    """Return one trace diff whose ``reason`` is the machine-readable code, not prose."""
    return {"path": path, "expected": expected, "actual": actual, "reason": code}


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
        [row.to_dict() for row in flatten_intent(expected_intent, server)] if expected_intent and server else []
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
    trace_value = evidence.get("trace")
    trace_map = trace_value if isinstance(trace_value, Mapping) else {}
    judged_task_t = actual_task_t if isinstance(actual_task_t, str) else ""
    exported_task_t, exported_truncated = dispatched_task_t(trace_map)
    fidelity = task_t_fidelity(judged_task_t, exported_task_t, truncated=exported_truncated)
    corroboration = intent_corroboration(server, intent_params(trace_map))
    validate_detail = _mapping(validate_stage.get("detail"))
    errors = validate_detail.get("slotErrors")
    negotiation = _mapping(_protocol_block(effective, protocol).get("negotiation"))
    negotiate_actual = _mapping(_stage(evidence, "negotiate").get("actual"))
    messages = _observed_messages(evidence, negotiate_actual)
    expectation = negotiation_message_expectation(effective, protocol)
    message_match: object = None
    if expectation is not None and messages:
        message_match = match_negotiation_messages(expectation, messages).to_dict()
    applicable_run = _trace_applicable(evidence)
    trace_context = (
        _trace_context(case, effective, protocol, evidence, applicable_run) if applicable_run is not None else None
    )
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
            "corroboration": _fidelity_dict(corroboration),
        },
        "taskT": {
            "expected": expected_task_t,
            "actual": actual_task_t,
            "judged": judged_task_t,
            "dispatched": exported_task_t,
            "dispatchedTruncated": exported_truncated,
            "rows": task_t_rows,
            "fidelity": _fidelity_dict(fidelity),
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
        "trace": _trace_detail(evidence, trace_context),
    }


def _trace_detail(
    evidence: Mapping[str, Any],
    context: TraceContext | None,
) -> dict[str, object]:
    """Return the compact trace summary the page and the report show.

    The full snapshot stays in the store (``trace_json``); embedding it in every round row would
    duplicate it into the export once per protocol. The block is returned even when no snapshot
    exists: the page must be able to say *why* the dimension abstains and offer the fetch, instead
    of the bare "不适用" the reviewer used to get.
    """
    trace = evidence.get("trace")
    batch = _mapping(evidence.get("batch"))
    applicability = trace_applicability(
        evidence.get("runId"),
        evidence.get("traceId"),
        simulated=batch.get("nmsSim") is True,
    )
    if not isinstance(trace, Mapping):
        return {
            "present": False,
            "traceId": applicability.trace_id,
            "applicability": applicability.to_dict(),
            "exportState": "not_exported" if applicability.applicable else "",
            "nmsKind": _nms_kind_of(evidence),
            "frames": {},
            "attribution": {},
            "points": [],
            "data": [],
            "warnings": [],
            "related": [],
            "blockers": _trace_blockers(applicability, None),
        }
    frames = trace.get("frames")
    attribution = trace.get("attribution")
    points: list[dict[str, object]] = []
    data_rows: list[dict[str, object]] = []
    computed_attribution: dict[str, object] = dict(attribution) if isinstance(attribution, Mapping) else {}
    if context is not None:
        # The point table is reference only: it explains an ingest gap, it no longer gates the
        # acceptance. The key-data table below is what the dimension reads.
        points = [
            {
                "point": result.point.id,
                "label": result.point.label,
                "expected": result.point.count,
                "observed": result.observed,
                "spans": list(result.names),
                "ok": result.ok,
            }
            for result in process_points(trace, context)
        ]
        # The snapshot carries the observations, not the verdict: the attribution and the key-data
        # presence are derived here so the page and the customer pack stay self-contained.
        judged = judge_trace(trace, context, not_exported_status=TRACE_NOT_EXPORTED_STATUS)
        data_rows = [result.to_dict(include_values=True) for result in judged.data]
        computed_attribution = {
            "status": judged.attribution,
            "findings": [
                {
                    "point": finding.point,
                    "label": finding.label,
                    "status": finding.status,
                    "detail": finding.detail,
                }
                for finding in judged.findings
            ],
        }
    warnings = trace.get("warnings")
    url = evidence.get("langfuseUrl")
    return {
        "present": True,
        "traceId": trace.get("traceId") or applicability.trace_id,
        "url": url if isinstance(url, str) and url.strip() else "",
        "applicability": applicability.to_dict(),
        "blockers": _trace_blockers(applicability, trace),
        "fetchedAt": trace.get("fetchedAt") or trace.get("capturedAt"),
        "exportState": trace.get("exportState") or trace.get("export_state"),
        "spanLevel": trace.get("spanLevel"),
        "captureContent": trace.get("captureContent"),
        "nmsKind": _nms_kind_of(evidence),
        "frames": dict(frames) if isinstance(frames, Mapping) else {},
        "attribution": computed_attribution,
        "points": points,
        "data": data_rows,
        "observations": _observation_rows(trace.get("observations")),
        "observationsTruncated": bool(trace.get("observationsTruncated")),
        "warnings": [str(item) for item in warnings] if isinstance(warnings, list) else [],
        "related": list(trace.get("related") or ()) if isinstance(trace.get("related"), list) else [],
    }


def _observation_rows(value: object) -> list[dict[str, object]]:
    """Return the compact observation list the page renders under "轨迹观测".

    The snapshot keeps the full row; the page only needs the fields that answer "which span is
    this and where does it sit". The list is capped by the snapshot itself, so a long run stays
    complete instead of being silently sampled.
    """
    if not isinstance(value, list):
        return []
    rows: list[dict[str, object]] = []
    for row in value:
        if not isinstance(row, Mapping):
            continue
        rows.append(
            {
                "name": row.get("name"),
                "type": row.get("type"),
                "kind": row.get("kind"),
                "stage": row.get("stage"),
                "round": row.get("round"),
                "side": row.get("side"),
                "runId": row.get("runId"),
                "status": row.get("status"),
            }
        )
    return rows


def _trace_blockers(
    applicability: TraceApplicability,
    trace: Mapping[str, Any] | None,
) -> list[dict[str, object]]:
    """Return why the trace dimension cannot pass yet, with the action the page offers.

    ``action`` is ``none`` (not auditable, nothing to do), ``fetch`` (no snapshot yet) or
    ``retry`` (the last fetch failed). The page turns it into the visibility and label of the
    fetch button, so an abstention always carries an explanation.
    """
    if not applicability.applicable:
        return [{"code": applicability.code, "text": applicability.text, "action": "none"}]
    if trace is None:
        return [
            {
                "code": "trace_snapshot_missing",
                "text": "尚未抓取 Langfuse 轨迹快照",
                "action": "fetch",
            }
        ]
    state = str(trace.get("exportState") or trace.get("export_state") or "")
    if state == "failed":
        warnings = trace.get("warnings")
        detail = next((str(item) for item in warnings if item), "") if isinstance(warnings, list) else ""
        return [
            {
                "code": "trace_snapshot_failed",
                "text": f"上次抓取失败：{detail}" if detail else "上次抓取失败",
                "action": "retry",
            }
        ]
    if state not in {"exported", "partial"}:
        return [
            {
                "code": "trace_snapshot_missing",
                "text": "尚未抓取 Langfuse 轨迹快照",
                "action": "fetch",
            }
        ]
    frames = trace.get("frames")
    nms = frames.get("nms") if isinstance(frames, Mapping) else None
    if isinstance(nms, Mapping) and nms.get("expected") is None:
        # The dimension abstains when no vendor hop can be identified; say it, so the badge is
        # not read as "nothing to check". A recognized hop with an unknown round count is a
        # different problem and must not be reported as "no hop".
        if nms.get("observed") is not None:
            return [
                {
                    "code": "trace_rounds_unknown",
                    "text": "已识别 NMS 发送跳，但无法确定应发帧数（轮次未知）",
                    "action": "none",
                }
            ]
        return [
            {
                "code": "trace_nms_hop_unknown",
                "text": "轨迹里没有可识别的 NMS（a2a-t-server）发送跳，无法判定",
                "action": "none",
            }
        ]
    return []


def _nms_kind_of(evidence: Mapping[str, Any]) -> str:
    """Return the downstream identity of the scoped batch (one shared rule, see tracecheck)."""
    batch = _mapping(evidence.get("batch"))
    declared = batch.get("nms")
    simulated = batch.get("nmsSim")
    return nms_kind(
        declared if isinstance(declared, bool) else None,
        simulated if isinstance(simulated, bool) else None,
    )


def _fidelity_dict(fidelity: Fidelity) -> dict[str, object]:
    """Return one fidelity comparison as a JSON-friendly block."""
    return {
        "comparable": fidelity.comparable,
        "equal": fidelity.equal,
        "truncated": fidelity.truncated,
        "rows": [dict(row) for row in fidelity.rows],
    }


def _trace_applicable(evidence: Mapping[str, Any]) -> str | None:
    """Return the run id when the trace dimension applies, else ``None``."""
    batch = _mapping(evidence.get("batch"))
    applicability = trace_applicability(
        evidence.get("runId"),
        evidence.get("traceId"),
        simulated=batch.get("nmsSim") is True,
    )
    return applicability.run_id if applicability.applicable else None


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


def _optional_int(value: object) -> int | None:
    """Return an integer when the value is one, else ``None``.

    Frame counts distinguish "not measured" from zero: an unidentified NMS hop must stay
    ``not_applicable`` instead of failing on ``0 vs 0``.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


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
    "DIMENSION_TRACE",
    "DIMENSION_VALIDATE",
    "DimensionResult",
    "ProtocolJudgement",
    "TRACE_NOT_EXPORTED_STATUS",
    "TRACE_REASONS",
    "VERDICT_FAIL",
    "VERDICT_MISSING",
    "VERDICT_NOT_APPLICABLE",
    "VERDICT_NOT_EXPECTED",
    "VERDICT_PARTIAL",
    "VERDICT_PASS",
    "VERDICT_RECORDED",
    "case_signature",
    "evidence_input_signature",
    "judge_protocol",
]
