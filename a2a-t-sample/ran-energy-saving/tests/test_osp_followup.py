"""Tests of the blocked-case follow-up request derived case documents."""

from __future__ import annotations

from typing import Any

import pytest
from eval.osp.followup import (
    followup_case,
    followup_input,
    has_followup,
    interception_case,
    is_blocked_case,
)


def _blocked_case() -> dict[str, Any]:
    return {
        "schema": "osp-eval-case/v1",
        "id": 900,
        "name": "blocked sample",
        "operation": "Create",
        "user_input": "ambiguous request",
        "expect": {
            "intent": {"Operation type": "Create", "Energy saving region": "A2ATestRegion"},
            "protocols": {
                "a2a-t": {
                    "validate": "blocked",
                    "validate_label": "拦截",
                    "send": False,
                    "task_t": "## Operation Type\nCreate",
                    "negotiation": {
                        "expected": False,
                        "expected_rounds": 0,
                        "kinds": [],
                        "rounds": [],
                    },
                    "final_state": "input_invalid",
                    "final_state_derived": True,
                },
                "a2a": {
                    "negotiation": {
                        "expected": False,
                        "expected_rounds": 0,
                        "kinds": [],
                        "rounds": [],
                    },
                    "final_state": None,
                },
            },
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
        "runner": {"scenario": "night"},
    }


def _with_followup(**overrides: Any) -> dict[str, Any]:
    case = _blocked_case()
    followup: dict[str, Any] = {"input": "Implement energy-saving in A2ATestRegion."}
    followup.update(overrides)
    case["blocked_followup"] = followup
    return case


def test_a_case_without_the_field_has_no_followup() -> None:
    case = _blocked_case()

    assert followup_input(case) is None
    assert has_followup(case) is False


def test_a_blank_input_is_not_a_followup() -> None:
    case = _with_followup(input="   \n")

    assert followup_input(case) is None
    assert has_followup(case) is False


def test_a_blocked_case_with_an_input_has_a_followup() -> None:
    case = _with_followup()

    assert is_blocked_case(case) is True
    assert followup_input(case) == "Implement energy-saving in A2ATestRegion."
    assert has_followup(case) is True


def test_an_unblocked_case_is_not_a_blocked_case() -> None:
    case = _with_followup()
    case["expect"]["protocols"]["a2a-t"]["validate"] = "pass"

    assert is_blocked_case(case) is False


def test_the_interception_case_records_intent_and_task_t_without_judging_them() -> None:
    derived = interception_case(_blocked_case())

    assert derived["assert"]["intent"] is False
    assert derived["assert"]["a2a-t"]["task_t"] is False
    assert derived["assert"]["a2a-t"]["validate"] is True
    assert derived["expect"]["protocols"]["a2a-t"]["validate"] == "blocked"
    assert derived["user_input"] == "ambiguous request"


def test_the_followup_case_swaps_the_input_and_expects_a_send() -> None:
    derived = followup_case(_with_followup())

    assert derived["user_input"] == "Implement energy-saving in A2ATestRegion."
    block = derived["expect"]["protocols"]["a2a-t"]
    assert block["validate"] == "pass"
    assert block["send"] is True
    assert block["final_state"] == "task_completed"
    assert block["final_state_derived"] is True
    # The intent, Task-T and negotiation expectations describe the follow-up request as they are.
    assert derived["expect"]["intent"] == _blocked_case()["expect"]["intent"]
    assert block["task_t"] == "## Operation Type\nCreate"
    assert block["negotiation"]["expected_rounds"] == 0


def test_the_followup_case_merges_expectation_and_assertion_overrides() -> None:
    case = _with_followup(
        expect={
            "protocols": {
                "a2a-t": {
                    "negotiation": {"expected": True, "expected_rounds": 1, "kinds": ["feasibility"]}
                }
            }
        }
    )
    case["blocked_followup"]["assert"] = {"intent": True, "a2a-t": {"task_t": True}}

    derived = followup_case(case)

    block = derived["expect"]["protocols"]["a2a-t"]
    assert block["send"] is True
    assert block["negotiation"]["expected"] is True
    assert block["negotiation"]["expected_rounds"] == 1
    assert block["negotiation"]["kinds"] == ["feasibility"]
    assert derived["assert"]["intent"] is True
    assert derived["assert"]["a2a-t"]["task_t"] is True
    # Untouched assertion flags survive the merge.
    assert derived["assert"]["a2a-t"]["validate"] is True


def test_the_followup_case_does_not_mutate_the_source_case() -> None:
    case = _with_followup()

    followup_case(case)

    assert case["user_input"] == "ambiguous request"
    assert case["expect"]["protocols"]["a2a-t"]["validate"] == "blocked"
    assert case["expect"]["protocols"]["a2a-t"]["send"] is False
    assert case["expect"]["protocols"]["a2a-t"]["final_state"] == "input_invalid"


def test_the_followup_case_requires_an_input() -> None:
    with pytest.raises(ValueError, match="blocked_followup"):
        followup_case(_blocked_case())


def test_the_followup_case_requires_a_blocked_expectation() -> None:
    case = _with_followup()
    case["expect"]["protocols"]["a2a-t"]["validate"] = "pass"

    with pytest.raises(ValueError, match="blocked"):
        followup_case(case)
