"""Client decision-quality appendix of the OSP tester.

The appendix reads the case's expected negotiation reply (the Q column) and the evidence the run
collected, and reports whether the client's decision was *good* — not whether the request was
accepted. It is deliberately separate from the tester's acceptance status: a quality row may fail
while the protocol result stays authoritative.

Checks are split by what can be decided deterministically and what needs semantics:

* action — the Q-column text names accept/reject; the decision must match it;
* structure — an accepted decision must have dispatched the matching performative;
* review — a pre-send interception (invalid input / ungrounded decision) failed before a decision;
* value — an independent judge compares the expected reply with the observed one;
* coverage — the same judge call asks whether the decision stays within the client's conditions.

Every semantic check goes to the independent judge in one call per round. A missing judge, or one
that fails, leaves the semantic checks ``unjudged`` instead of inventing a verdict.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from shared.judge_llm import JudgeUnavailable
from shared.run_constants import FINAL_STATE_DECISION_UNGROUNDED, FINAL_STATE_INPUT_INVALID

from eval.osp.conditions import case_client_conditions

#: Per-check / per-row / per-result verdict labels.
CHECK_PASS: Final[str] = "pass"
CHECK_FAIL: Final[str] = "fail"
CHECK_UNJUDGED: Final[str] = "unjudged"
#: How a row was decided.
METHOD_MODEL: Final[str] = "model"
METHOD_UNJUDGED: Final[str] = "unjudged"

#: A pre-send interception ends the run before any negotiation decision exists.
_PRE_SEND_GATE_STATES: Final[frozenset[str]] = frozenset(
    {FINAL_STATE_INPUT_INVALID, FINAL_STATE_DECISION_UNGROUNDED}
)

#: The Q-column wording that marks a refusal. Anything else is read as an acceptance.
_REJECT_MARKERS: Final[tuple[str, ...]] = ("reject", "not accept", "decline", "refuse")

#: Structured answer the judge must return for one round.
JUDGE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["equal", "not_equal", "unclear"]},
        "covered": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "covered", "reason"],
    "additionalProperties": False,
}

#: System prompt of the judge call. It must not name a protocol or an implementation.
_JUDGE_SYSTEM: Final[str] = (
    "You are an independent reviewer of a client agent's negotiation decision. Compare the "
    "expected negotiation reply with what the client actually decided. Decide whether the "
    "observed reply conveys the same substantive agreement (verdict), whether it stays within "
    "the client's stated conditions (covered), and explain the answer briefly. Never assume "
    "hidden context: if the evidence is insufficient, answer unclear."
)


@dataclass(frozen=True)
class ClientQualityObservation:
    """One observed client decision, reduced to what the quality checks need.

    Attributes:
        round: 1-based negotiation round of the decision.
        kind: negotiation type (``information`` / ``target`` / ``feasibility``).
        proposal: the server proposal the client decided on.
        decision: decision action (``accept`` / ``reject`` / ``abort`` / ``counter``).
        accepted_text: the client's own reply text, when the run recorded one.
        structure_ok: whether the run dispatched the matching performative; ``None`` when the
            evidence does not say.
    """

    round: int = 1
    kind: str = ""
    proposal: str = ""
    decision: str = ""
    accepted_text: str = ""
    structure_ok: bool | None = None


#: Design alias: the client decision the quality checks observe.
ObservedDecision = ClientQualityObservation


@dataclass(frozen=True)
class QualityCheck:
    """One check's verdict and the reason shown in the report.

    Attributes:
        status: ``pass`` / ``fail`` / ``unjudged``.
        reason: short explanation, empty when the check passed without comment.
    """

    status: str = CHECK_UNJUDGED
    reason: str = ""


@dataclass
class QualityRound:
    """One (case, protocol, round) row of the quality appendix.

    Attributes:
        round: 1-based negotiation round.
        proposal: server proposal the client decided on.
        expected: expected reply of the Q column.
        decision: observed decision action.
        action: action check verdict.
        value: semantic value-equivalence verdict.
        structure: dispatched-performative verdict.
        review: pre-send gate verdict.
        coverage: decision-basis coverage verdict.
        quality: aggregate verdict of the row.
        method: ``model`` when a judge answered, ``unjudged`` otherwise.
        reason: explanation shown in the report.
    """

    round: int
    proposal: str = ""
    expected: str = ""
    decision: str = ""
    action: str = CHECK_UNJUDGED
    value: str = CHECK_UNJUDGED
    structure: str = CHECK_UNJUDGED
    review: str = CHECK_PASS
    coverage: str = CHECK_UNJUDGED
    quality: str = CHECK_UNJUDGED
    method: str = METHOD_UNJUDGED
    reason: str = ""

    def checks(self) -> tuple[str, ...]:
        """Return every status that decides the row's aggregate verdict."""
        return (self.action, self.value, self.structure, self.review, self.coverage)

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation the report sheet reads."""
        return {
            "round": self.round,
            "proposal": self.proposal,
            "expected": self.expected,
            "decision": self.decision,
            "action": self.action,
            "value": self.value,
            "structure": self.structure,
            "review": self.review,
            "coverage": self.coverage,
            "quality": self.quality,
            "method": self.method,
            "reason": self.reason,
        }


@dataclass
class ClientQualityResult:
    """Aggregated quality result of one negotiation item.

    Attributes:
        quality: ``pass`` / ``fail`` / ``unjudged``.
        method: ``model`` when at least one judge answered, ``unjudged`` otherwise.
        needs_human_review: whether a semantic mismatch is worth a human inspection.
        rounds: one row per observed or expected round.
    """

    quality: str
    method: str
    needs_human_review: bool
    rounds: list[QualityRound] = field(default_factory=list)
    #: Judge model label, empty when no judge answered; the report names it for D8/D14.
    judge_model: str = ""
    #: Checker queries recorded as decision evidence (D19); they never affect ``quality``.
    checker_calls: list[dict[str, object] | str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation the report and the item snapshot read."""
        return {
            "quality": self.quality,
            "method": self.method,
            "needsHumanReview": self.needs_human_review,
            "judgeModel": self.judge_model,
            "checkerCalls": self.checker_calls,
            "rounds": [row.to_dict() for row in self.rounds],
        }


def _expected_rounds(case: Mapping[str, Any], protocol: str) -> list[str]:
    """Return the recorded replies of one case's negotiation on ``protocol``.

    A2A-T keeps its expectations in the Q column (``a2a-t``); A2A keeps them in the P column
    (``a2a``). Judging a pure item against the Q column would compare the agent with another
    protocol's expected dialogue.
    """
    expect = case.get("expect")
    if not isinstance(expect, Mapping):
        return []
    protocols = expect.get("protocols")
    if not isinstance(protocols, Mapping):
        return []
    protocol_block = protocols.get(protocol)
    if not isinstance(protocol_block, Mapping):
        return []
    negotiation = protocol_block.get("negotiation")
    if not isinstance(negotiation, Mapping):
        return []
    rounds = negotiation.get("rounds")
    if not isinstance(rounds, Sequence) or isinstance(rounds, str | bytes):
        return []
    return [str(round_text) for round_text in rounds]


def _expected_action(expected: str) -> str:
    """Return the action the Q-column text names: ``accept`` unless it refuses."""
    lowered = expected.lower()
    return "reject" if any(marker in lowered for marker in _REJECT_MARKERS) else "accept"


def _aggregate(statuses: Sequence[str]) -> str:
    """Aggregate check verdicts: any fail fails, any unjudged leaves the row unjudged."""
    if any(status == CHECK_FAIL for status in statuses):
        return CHECK_FAIL
    if any(status == CHECK_UNJUDGED for status in statuses):
        return CHECK_UNJUDGED
    return CHECK_PASS


def _judge_prompt(expected: str, observation: ClientQualityObservation, conditions: str) -> str:
    """Render one judge prompt: the expected reply, the observed decision and the conditions."""
    parts: list[str] = []
    if conditions:
        parts.append(f"Client conditions:\n{conditions}")
    parts.append(f"Expected negotiation reply:\n{expected}")
    parts.append(f"Server proposal:\n{observation.proposal}")
    parts.append(f"Observed client decision: {observation.decision}")
    if observation.accepted_text:
        parts.append(f"Observed client reply:\n{observation.accepted_text}")
    return "\n\n".join(parts)


def _judge_verdict(answer: Mapping[str, object]) -> tuple[str, str, str]:
    """Read one judge answer as ``(value, coverage, reason)`` verdicts."""
    verdict = str(answer.get("verdict") or "").strip().lower()
    value = {
        "equal": CHECK_PASS,
        "not_equal": CHECK_FAIL,
        "unclear": CHECK_UNJUDGED,
    }.get(verdict, CHECK_UNJUDGED)
    covered = answer.get("covered")
    coverage = (
        CHECK_PASS if covered is True else CHECK_FAIL if covered is False else CHECK_UNJUDGED
    )
    reason = str(answer.get("reason") or "").strip()
    return value, coverage, reason


def _row_for_round(
    round_number: int,
    expected: str,
    observation: ClientQualityObservation | None,
    *,
    gate_failed: bool,
    gate_reason: str,
) -> QualityRound:
    """Build one row from an expected reply and, when present, the observed decision."""
    row = QualityRound(
        round=round_number,
        proposal=observation.proposal if observation is not None else "",
        expected=expected,
        decision=observation.decision if observation is not None else "",
    )
    if gate_failed:
        # The request never reached a negotiation decision, so nothing about the reply can be
        # judged; the gate reason is the whole story of this row.
        row.review = CHECK_FAIL
        row.reason = gate_reason or "请求在发送前被拦截，未产生协商决策。"
        row.quality = CHECK_FAIL
        return row
    if observation is not None and observation.structure_ok is not None:
        row.structure = CHECK_PASS if observation.structure_ok else CHECK_FAIL
    if expected and observation is not None:
        row.action = (
            CHECK_PASS
            if observation.decision.strip().lower() == _expected_action(expected)
            else CHECK_FAIL
        )
    return row


async def judge_client_quality(
    *,
    case: Mapping[str, Any],
    observations: Sequence[ClientQualityObservation],
    judge: Any | None,
    final_state: str | None = None,
    gate_reason: str = "",
    client_conditions: str | None = None,
    judge_label: str = "",
    checker_queries: Sequence[object] = (),
    protocol: str = "a2a-t",
) -> ClientQualityResult:
    """Judge the client's negotiation decisions of one case.

    Args:
        case: the OSP case document (the recorded replies live in ``expect.protocols.a2a-t``
            for A2A-T and ``expect.protocols.a2a`` for pure A2A).
        protocol: catalog protocol of the item; selects the recorded reply column.
        observations: observed decisions, one per recorded decision.
        judge: independent judge client (``complete_json``) or ``None``.
        final_state: terminal state of the run; a pre-send interception fails every round.
        gate_reason: operator-facing reason of the pre-send interception.
        client_conditions: explicit conditions; the case's own field is used when omitted.
        judge_label: label of the judge model, recorded for the report.
        checker_queries: reserved for the future checker evidence. It is record-only (D19): no
            checker exists yet, and an empty list must be accepted.

    Returns:
        The aggregated quality result. It never raises for a judge failure: an unavailable judge
        leaves the semantic checks unjudged.
    """
    recorded_queries: list[dict[str, object] | str] = [
        dict(query) if isinstance(query, Mapping) else str(query) for query in checker_queries
    ]
    conditions = (
        client_conditions
        if client_conditions is not None
        else case_client_conditions(case)
    )
    expected_rounds = _expected_rounds(case, protocol)
    by_round: dict[int, ClientQualityObservation] = {}
    for observation in observations:
        by_round.setdefault(observation.round, observation)
    round_numbers = sorted(set(range(1, len(expected_rounds) + 1)) | set(by_round))
    gate_failed = final_state in _PRE_SEND_GATE_STATES

    judge_available = judge is not None and bool(getattr(judge, "configured", True))
    rows: list[QualityRound] = []
    for round_number in round_numbers:
        expected = (
            expected_rounds[round_number - 1] if round_number <= len(expected_rounds) else ""
        )
        observation = by_round.get(round_number)
        row = _row_for_round(
            round_number,
            expected,
            observation,
            gate_failed=gate_failed,
            gate_reason=gate_reason,
        )
        if not gate_failed and expected and observation is not None and judge_available:
            prompt = _judge_prompt(expected, observation, conditions)
            try:
                answer = await judge.complete_json(
                    prompt, system=_JUDGE_SYSTEM, json_schema=JUDGE_SCHEMA
                )
            except JudgeUnavailable:
                answer = None
            except Exception:  # noqa: BLE001 - a judge failure must stay "unjudged"
                answer = None
            if isinstance(answer, Mapping):
                row.method = METHOD_MODEL
                value, coverage, reason = _judge_verdict(answer)
                row.value = value
                row.coverage = coverage
                if reason:
                    row.reason = reason
        row.quality = _aggregate(row.checks())
        rows.append(row)

    if any(row.quality == CHECK_FAIL for row in rows):
        quality = CHECK_FAIL
    elif any(row.quality == CHECK_UNJUDGED for row in rows):
        quality = CHECK_UNJUDGED
    else:
        quality = CHECK_PASS
    method = METHOD_MODEL if any(row.method == METHOD_MODEL for row in rows) else METHOD_UNJUDGED
    needs_human_review = any(
        row.method == METHOD_MODEL and CHECK_FAIL in (row.value, row.coverage) for row in rows
    )
    return ClientQualityResult(
        quality=quality,
        method=method,
        needs_human_review=needs_human_review,
        rounds=rows,
        judge_model=judge_label if method == METHOD_MODEL else "",
        checker_calls=recorded_queries,
    )
