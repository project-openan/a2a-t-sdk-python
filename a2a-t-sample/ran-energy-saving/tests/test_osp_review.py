"""Tests of the review-time judging of stored tester evidence."""

from __future__ import annotations

from typing import Any

from eval.osp.review import (
    VERDICT_FAIL,
    VERDICT_MISSING,
    VERDICT_NOT_APPLICABLE,
    VERDICT_PARTIAL,
    VERDICT_PASS,
    VERDICT_RECORDED,
    case_signature,
    evidence_is_current_input,
    judge_protocol,
)

INTENT: dict[str, object] = {
    "Operation type": "Modify",
    "Energy saving cell RAT": "ALL",
    "Energy saving aggressiveness": "",
    "Energy saving region": "A2ATestRegion",
    "Whether Experience-Preserving Energy Saving is Required": "",
    "Special Requirements for Carrier Shutdown Frequencies": "",
    "Energy saving time": "",
    "Frequencies of carriers that can be shut down": [],
    "Gain target": "",
    "Guaranteed throughput target by zone": [],
    "Scenario-based energy saving": [],
}

TASK_T = """## Operation Type
Modify

## Task Type
RAN energy saving

## Task Description
Execute the energy-saving task.

## Task Object
A2ATestRegion

## Task Target

## Task Context
Site list: ["LNR_KPI_7745"], rate target: 2Mbps.
"""

MESSAGE = """## Feasibility Negotiation
Reduce the throughput target from 10Mbps to 2Mbps for 08:00-18:00.
"""


def _case(
    *,
    protocol: str = "a2a-t",
    blocked: bool = False,
    rounds: int = 1,
    messages: object = None,
    slot_errors: object = None,
    intent: dict[str, object] | None = None,
    followup: str | None = None,
) -> dict[str, Any]:
    """Return a minimal case document the judge understands."""
    negotiation: dict[str, object] = {
        "expected": rounds > 0,
        "expected_rounds": rounds,
        "kinds": None,
        "rounds": [],
    }
    if messages is not None:
        negotiation["messages"] = messages
    a2at: dict[str, object] = {
        "validate": "blocked" if blocked else "pass",
        "validate_label": "",
        "send": True,
        "task_t": TASK_T,
        "negotiation": negotiation,
        "final_state": "task_completed",
    }
    if slot_errors is not None:
        a2at["slot_errors"] = slot_errors
    a2a: dict[str, object] = {
        "negotiation": {
            "expected": False,
            "expected_rounds": 0,
            "kinds": None,
            "rounds": [],
        },
        "final_state": None,
    }
    case: dict[str, Any] = {
        "id": 1,
        "name": "case",
        "user_input": "do it",
        "expect": {
            "intent": dict(intent or INTENT),
            "protocols": {"a2a-t": a2at, "a2a": a2a},
        },
        "assert": {
            "intent": True,
            "a2a-t": {
                "validate": True,
                "task_t": True,
                "send": True,
                "negotiation": True,
                "final_state": True,
            },
            "a2a": {"negotiation": True, "final_state": False},
        },
    }
    if followup is not None:
        case["blocked_followup"] = {"input": followup}
    return case


def _evidence(
    *,
    server_intent: dict[str, object] | None = None,
    sdk_intent: dict[str, object] | None = None,
    task_t: str = TASK_T,
    validate_verdict: str = "pass",
    slot_errors: list[dict[str, object]] | None = None,
    action: str = "passed",
    rounds: int = 1,
    messages: list[dict[str, object]] | None = None,
    final_state: str = "task_completed",
    refs_input: str = "do it",
    interception: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return one stored tester item (trimmed like the review corpus stores it)."""
    return {
        "refs": {"userInput": refs_input},
        "stages": {
            "intent": {"stage": "intent", "status": action, "actual": dict(INTENT)},
            "task_t": {"stage": "task_t", "status": "passed", "actual": task_t},
            "validate": {
                "stage": "validate",
                "status": "passed",
                "actual": validate_verdict,
                "detail": {
                    "verdict": validate_verdict,
                    "slotErrors": slot_errors or [],
                },
            },
            "negotiate": {
                "stage": "negotiate",
                "status": "passed",
                "actual": {"rounds": rounds, "kinds": ["feasibility"]},
            },
            "final": {"stage": "final", "status": "passed", "actual": final_state},
        },
        "intent": dict(sdk_intent or INTENT),
        "serverIntent": dict(server_intent or INTENT),
        "messages": messages or [],
        "interception": interception,
    }


def _dimension(judgement: Any, name: str) -> Any:
    row = judgement.dimension(name)
    assert row is not None
    return row


def test_a_matching_final_intent_passes() -> None:
    judgement = judge_protocol(_case(), "a2a-t", _evidence())

    assert _dimension(judgement, "intent").status == VERDICT_PASS
    assert judgement.status == VERDICT_PASS


def test_a_changed_final_intent_fails() -> None:
    server = dict(INTENT)
    server["Energy saving region"] = "OtherRegion"

    judgement = judge_protocol(_case(), "a2a-t", _evidence(server_intent=server))

    intent = _dimension(judgement, "intent")
    assert intent.status == VERDICT_FAIL
    assert [diff["path"] for diff in intent.diffs] == ["intent.Energy saving region"]
    assert judgement.status == VERDICT_FAIL


def test_an_unjudgeable_intent_is_recorded_and_makes_the_task_partial() -> None:
    """A simulator/proxy run keeps the reading but cannot be accepted on it."""
    judgement = judge_protocol(_case(), "a2a-t", _evidence(action="recorded"))

    assert _dimension(judgement, "intent").status == VERDICT_RECORDED
    assert judgement.status == VERDICT_PARTIAL


def test_pure_a2a_never_judges_the_intent() -> None:
    judgement = judge_protocol(_case(protocol="a2a", rounds=0), "a2a", _evidence())

    assert _dimension(judgement, "intent").status == VERDICT_NOT_APPLICABLE
    assert judgement.status == VERDICT_NOT_APPLICABLE


def test_a_changed_task_t_section_fails() -> None:
    actual = TASK_T.replace("Site list: [\"LNR_KPI_7745\"]", "Site list: [\"NES_27A_321071\"]")

    judgement = judge_protocol(_case(), "a2a-t", _evidence(task_t=actual))

    task_t = _dimension(judgement, "task_t")
    assert task_t.status == VERDICT_FAIL
    assert all(diff["path"] != "task_t.Operation Type" for diff in task_t.diffs)


def test_the_blocked_case_judges_the_interception_segment() -> None:
    case = _case(
        blocked=True,
        slot_errors={
            "mode": "any",
            "expect": [{"code": "slot.constraint_violated", "contains": ["gain"]}],
        },
        followup="do it properly",
    )
    evidence = _evidence(
        refs_input="do it properly",
        interception={
            "stages": {
                "validate": {
                    "stage": "validate",
                    "status": "failed",
                    "detail": {
                        "verdict": "blocked",
                        "slotErrors": [
                            {
                                "slotName": "Gain target",
                                "code": "slot.constraint_violated",
                                "message": "gain target is not allowed",
                                "facts": {"actual": "20%"},
                            }
                        ],
                    },
                }
            }
        },
    )

    judgement = judge_protocol(case, "a2a-t", evidence)

    validate = _dimension(judgement, "validate")
    assert validate.status == VERDICT_PASS
    assert validate.actual == "blocked"
    assert judgement.status == VERDICT_PASS


def test_a_blocked_case_without_the_expected_slot_error_fails() -> None:
    case = _case(
        blocked=True,
        slot_errors={"mode": "any", "expect": [{"code": "slot.not_provided"}]},
        followup="do it properly",
    )
    evidence = _evidence(
        refs_input="do it properly",
        interception={
            "stages": {
                "validate": {
                    "stage": "validate",
                    "detail": {
                        "verdict": "blocked",
                        "slotErrors": [
                            {
                                "slotName": "Gain target",
                                "code": "slot.constraint_violated",
                                "message": "gain target is not allowed",
                                "facts": {},
                            }
                        ],
                    },
                }
            }
        },
    )

    judgement = judge_protocol(case, "a2a-t", evidence)

    assert _dimension(judgement, "validate").status == VERDICT_FAIL


def test_the_negotiation_messages_are_judged_from_the_joined_evidence() -> None:
    case = _case(rounds=1, messages=[[MESSAGE]])
    matching = _evidence(messages=[{"round": 1, "kind": "feasibility", "content": MESSAGE}])

    judgement = judge_protocol(case, "a2a-t", matching)

    assert _dimension(judgement, "negotiation").status == VERDICT_PASS

    other = MESSAGE.replace("2Mbps", "1Mbps")
    failing = _evidence(messages=[{"round": 1, "kind": "feasibility", "content": other}])
    failed = judge_protocol(case, "a2a-t", failing)

    assert _dimension(failed, "negotiation").status == VERDICT_FAIL
    assert "negotiation.messages[1]" in [diff["path"] for diff in _dimension(failed, "negotiation").diffs]


def test_an_expected_message_without_evidence_is_partial() -> None:
    case = _case(rounds=1, messages=[[MESSAGE]])

    judgement = judge_protocol(case, "a2a-t", _evidence(messages=[]))

    assert _dimension(judgement, "negotiation").status == VERDICT_MISSING
    assert judgement.status == VERDICT_PARTIAL


def test_a_wrong_round_count_fails() -> None:
    judgement = judge_protocol(_case(rounds=2), "a2a-t", _evidence(rounds=1))

    assert _dimension(judgement, "negotiation").status == VERDICT_FAIL


def test_a_wrong_final_state_fails() -> None:
    judgement = judge_protocol(_case(), "a2a-t", _evidence(final_state="task_failed"))

    assert _dimension(judgement, "final_state").status == VERDICT_FAIL


def test_evidence_from_another_input_is_detected() -> None:
    case = _case()

    assert evidence_is_current_input(case, "a2a-t", _evidence()) is True
    assert evidence_is_current_input(case, "a2a-t", _evidence(refs_input="old text")) is False


def test_the_blocked_followup_input_is_the_effective_input() -> None:
    case = _case(blocked=True, followup="do it properly")

    assert evidence_is_current_input(case, "a2a-t", _evidence(refs_input="do it properly")) is True
    assert evidence_is_current_input(case, "a2a-t", _evidence(refs_input="do it")) is False


def test_the_case_signature_tracks_expectations_and_the_followup() -> None:
    case = _case()
    before = case_signature(case)

    changed = _case()
    changed["expect"]["protocols"]["a2a-t"]["task_t"] = TASK_T + "\nextra\n"
    assert case_signature(changed) != before

    followup = _case(blocked=True, followup="do it properly")
    assert case_signature(followup) != case_signature({**followup, "blocked_followup": {"input": "x"}})
