"""Tests of the two OSP expectation comparators (slot errors and negotiation messages)."""

from __future__ import annotations

import pytest
from eval.osp.expectations import (
    MESSAGE_DIMENSION,
    SLOT_MODE_ANY,
    SLOT_MODE_COVER,
    compare_negotiation_message,
    match_negotiation_messages,
    match_slot_errors,
    negotiation_message_expectation,
    slot_error_evidence,
    slot_error_expectation,
)

FEASIBILITY = """## Feasibility Negotiation
Please confirm the following revised target.

## Under Evaluation Description
Reduce the guaranteed throughput target from 5Mbps to 2Mbps for 08:00-18:00.

## Adjustment Proposal
The power outage lasts longer than the guaranteed window.
"""


def _case(
    *,
    slot_errors: object = None,
    messages: object = None,
    expected_rounds: int = 0,
    validate: str = "blocked",
) -> dict[str, object]:
    a2a_t: dict[str, object] = {"validate": validate}
    if slot_errors is not None:
        a2a_t["slot_errors"] = slot_errors
    negotiation: dict[str, object] = {
        "expected": messages is not None,
        "expected_rounds": expected_rounds,
        "kinds": ["feasibility"] if messages is not None else None,
        "rounds": [],
    }
    if messages is not None:
        negotiation[MESSAGE_DIMENSION] = messages
    a2a_t["negotiation"] = negotiation
    return {"expect": {"protocols": {"a2a-t": a2a_t}}}


def _error(
    code: str = "slot.not_provided",
    slot_name: str = "Area",
    *,
    message: str = "'Area' is not provided.",
    facts: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "slotName": slot_name,
        "code": code,
        "message": message,
        "facts": facts if facts is not None else {"slot_label": slot_name},
    }


# ---------------------------------------------------------------------------
# slot_errors
# ---------------------------------------------------------------------------


def test_a_bare_list_is_an_any_expectation() -> None:
    expectation = slot_error_expectation(_case(slot_errors=["slot.not_provided"]))

    assert expectation is not None
    assert expectation.mode == SLOT_MODE_ANY
    assert expectation.matchers[0].code == "slot.not_provided"


def test_a_mapping_states_the_mode_and_the_entries() -> None:
    expectation = slot_error_expectation(
        _case(slot_errors={"mode": "cover", "expect": [{"code": "slot.not_provided"}]})
    )

    assert expectation is not None
    assert expectation.mode == SLOT_MODE_COVER


def test_an_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown slot_errors.mode"):
        slot_error_expectation(_case(slot_errors={"mode": "all", "expect": ["x"]}))


def test_an_empty_expectation_is_not_an_assertion() -> None:
    assert slot_error_expectation(_case(slot_errors={"expect": []})) is None
    assert slot_error_expectation(_case()) is None


def test_a_matcher_requires_its_code_and_a_keyword_of_either_language() -> None:
    expectation = slot_error_expectation(
        _case(slot_errors=[{"code": "slot.constraint_violated", "contains": ["gain", "增益"]}])
    )
    assert expectation is not None

    english = match_slot_errors(expectation, [_error("slot.constraint_violated", "Gain target")])
    chinese = match_slot_errors(expectation, [_error("slot.constraint_violated", "节能增益")])
    wrong_code = match_slot_errors(expectation, [_error("slot.not_provided", "Gain target")])

    assert english.matched is True
    assert chinese.matched is True
    assert wrong_code.matched is False


def test_a_matcher_matches_a_facts_subset() -> None:
    expectation = slot_error_expectation(
        _case(slot_errors=[{"code": "slot.constraint_violated", "facts": {"actual": "20%"}}])
    )
    assert expectation is not None

    matching = _error("slot.constraint_violated", "Gain", facts={"actual": "20%", "slot_label": "Gain"})
    other = _error("slot.constraint_violated", "Gain", facts={"actual": "30%"})

    assert match_slot_errors(expectation, [matching]).matched is True
    assert match_slot_errors(expectation, [other]).matched is False


def test_a_matcher_can_freeze_the_whole_observed_error() -> None:
    """Adopting a candidate keeps code, slot name, message and facts; all are matched."""
    expectation = slot_error_expectation(
        _case(
            slot_errors=[
                {
                    "code": "slot.constraint_violated",
                    "slotName": "Gain target",
                    "message": "Gain target is not allowed.",
                    "facts": {"slot_label": "Gain target", "actual": "20%"},
                }
            ]
        )
    )
    assert expectation is not None

    observed = _error(
        "slot.constraint_violated",
        "Gain target",
        message="Gain  target is not  allowed.",
        facts={"slot_label": "Gain target", "actual": "20%"},
    )
    other_message = _error(
        "slot.constraint_violated",
        "Gain target",
        message="Gain target is rejected.",
        facts={"slot_label": "Gain target", "actual": "20%"},
    )
    other_slot = _error(
        "slot.constraint_violated",
        "Region",
        message="Gain target is not allowed.",
        facts={"slot_label": "Region", "actual": "20%"},
    )

    assert match_slot_errors(expectation, [observed]).matched is True
    assert match_slot_errors(expectation, [other_message]).matched is False
    assert match_slot_errors(expectation, [other_slot]).matched is False


def test_the_expectation_round_trips_through_its_dict_form() -> None:
    raw = {
        "code": "slot.constraint_violated",
        "slotName": "Gain target",
        "message": "not allowed",
        "facts": {"actual": "20%"},
    }
    expectation = slot_error_expectation(_case(slot_errors=[raw]))

    assert expectation is not None
    assert expectation.to_dict()["expect"] == [raw]


def test_any_mode_accepts_one_match_among_several_errors() -> None:
    expectation = slot_error_expectation(_case(slot_errors={"mode": "any", "expect": ["slot.not_provided"]}))
    assert expectation is not None

    match = match_slot_errors(
        expectation,
        [_error("slot.rule_violation", "Other"), _error("slot.not_provided", "Area")],
    )

    assert match.matched is True
    assert match.unmatched_observed == (0,)


def test_cover_mode_requires_every_seen_error_to_be_expected() -> None:
    expectation = slot_error_expectation(_case(slot_errors={"mode": "cover", "expect": ["slot.not_provided"]}))
    assert expectation is not None

    covered = match_slot_errors(expectation, [_error("slot.not_provided")])
    extra = match_slot_errors(
        expectation,
        [_error("slot.not_provided"), _error("slot.rule_violation", "Other")],
    )

    assert covered.matched is True
    assert extra.matched is False
    assert extra.unmatched_observed == (1,)


def test_cover_mode_fails_when_nothing_was_observed() -> None:
    expectation = slot_error_expectation(_case(slot_errors={"mode": "cover", "expect": ["slot.not_provided"]}))
    assert expectation is not None

    match = match_slot_errors(expectation, [])

    assert match.matched is False
    assert "未观测到" in match.reason


def test_evidence_is_attached_only_for_a_blocked_validation() -> None:
    case = _case(slot_errors=["slot.not_provided"])

    blocked = slot_error_evidence(case, "blocked", "blocked", [_error()])
    passed = slot_error_evidence(case, "blocked", "pass", [])
    absent = slot_error_evidence(_case(), "blocked", "blocked", [])

    assert blocked is not None
    assert blocked.matched() is True
    assert blocked.to_detail()["expectedSlotErrors"] is not None
    assert passed is not None
    assert passed.match is None
    assert passed.matched() is True
    assert absent is None


def test_a_failed_match_renders_one_diff_per_unexplained_error() -> None:
    evidence = slot_error_evidence(
        _case(slot_errors=["slot.not_provided"]),
        "blocked",
        "blocked",
        [_error("slot.rule_violation", "Other")],
    )
    assert evidence is not None

    diffs = evidence.diffs()

    assert [diff["path"] for diff in diffs] == ["validate.slotErrors[0]"]
    assert diffs[0]["actual"] == _error("slot.rule_violation", "Other")


def test_a_blocked_validation_without_slot_errors_reports_the_missing_evidence() -> None:
    evidence = slot_error_evidence(_case(slot_errors=["slot.not_provided"]), "blocked", "blocked", [])
    assert evidence is not None

    diffs = evidence.diffs()

    assert [diff["path"] for diff in diffs] == ["validate.slotErrors"]


# ---------------------------------------------------------------------------
# negotiation.messages
# ---------------------------------------------------------------------------


def test_messages_are_grouped_by_round() -> None:
    expectation = negotiation_message_expectation(
        _case(messages=[[FEASIBILITY]], expected_rounds=1),
        "a2a-t",
    )

    assert expectation is not None
    assert len(expectation.rounds) == 1
    assert expectation.rounds[0] == (FEASIBILITY,)


def test_a_flat_list_is_accepted_for_a_single_round_case() -> None:
    expectation = negotiation_message_expectation(
        _case(messages=["Not feasible, please confirm"], expected_rounds=1),
        "a2a-t",
    )

    assert expectation is not None
    assert expectation.rounds == (("Not feasible, please confirm",),)


def test_a_flat_list_is_rejected_for_a_multi_round_case() -> None:
    with pytest.raises(ValueError, match="must nest one group per round"):
        negotiation_message_expectation(
            _case(messages=["one", "two"], expected_rounds=2),
            "a2a-t",
        )


def test_the_message_count_must_match_the_expected_rounds() -> None:
    with pytest.raises(ValueError, match="describes 1 rounds"):
        negotiation_message_expectation(
            _case(messages=[[FEASIBILITY]], expected_rounds=2),
            "a2a-t",
        )


def test_a_message_is_judged_by_its_sections_and_facts() -> None:
    observed = FEASIBILITY.replace(
        "Reduce the guaranteed throughput target from 5Mbps to 2Mbps for 08:00-18:00.",
        "Please reduce the guaranteed throughput target to 2Mbps (was 5Mbps) between 08:00 and 18:00.",
    )

    matched, reason = compare_negotiation_message(FEASIBILITY, observed)

    assert matched is True, reason


def test_an_extra_observed_section_is_allowed() -> None:
    observed = FEASIBILITY + "\n## Feasible Evaluation Confirmation Request\nPlease confirm.\n"

    matched, reason = compare_negotiation_message(FEASIBILITY, observed)

    assert matched is True, reason


def test_a_missing_section_fails() -> None:
    observed = FEASIBILITY.split("## Adjustment Proposal")[0]

    matched, reason = compare_negotiation_message(FEASIBILITY, observed)

    assert matched is False
    assert "缺少小节 Adjustment Proposal" in reason


def test_a_wrong_business_fact_fails_even_when_the_wording_matches() -> None:
    observed = FEASIBILITY.replace("2Mbps", "4Mbps")

    matched, reason = compare_negotiation_message(FEASIBILITY, observed)

    assert matched is False
    assert "rate:2" in reason


def test_plain_language_is_compared_by_its_tokens() -> None:
    matched, reason = compare_negotiation_message(
        "Not feasible, please confirm",
        "Not feasible for the requested window, please confirm.",
    )
    rejected, rejected_reason = compare_negotiation_message(
        "Not feasible, please confirm",
        "The request was accepted.",
    )

    assert matched is True, reason
    assert rejected is False
    assert rejected_reason


def test_rounds_are_matched_independently() -> None:
    expectation = negotiation_message_expectation(
        _case(messages=[[FEASIBILITY], ["Please confirm the revised target."]], expected_rounds=2),
        "a2a-t",
    )
    assert expectation is not None

    match = match_negotiation_messages(
        expectation,
        [
            {"round": 1, "kind": "feasibility", "content": FEASIBILITY},
            {"round": 2, "kind": "target", "content": "Please confirm the revised target."},
        ],
    )

    assert match.matched is True


def test_a_missing_round_fails_the_dialogue() -> None:
    expectation = negotiation_message_expectation(
        _case(messages=[[FEASIBILITY], ["Please confirm the revised target."]], expected_rounds=2),
        "a2a-t",
    )
    assert expectation is not None

    match = match_negotiation_messages(
        expectation,
        [{"round": 1, "kind": "feasibility", "content": FEASIBILITY}],
    )

    assert match.matched is False
    assert [item.round for item in match.failed_rounds()] == [2]


def test_an_extra_round_fails_the_dialogue() -> None:
    expectation = negotiation_message_expectation(
        _case(messages=[[FEASIBILITY]], expected_rounds=1),
        "a2a-t",
    )
    assert expectation is not None

    match = match_negotiation_messages(
        expectation,
        [
            {"round": 1, "kind": "feasibility", "content": FEASIBILITY},
            {"round": 2, "kind": "target", "content": "Anything else?"},
        ],
    )

    assert match.matched is False
    assert [item.round for item in match.failed_rounds()] == [2]


def test_a_second_message_in_one_round_is_left_out_of_the_verdict() -> None:
    """The page shows every inbound frame; the verdict must name the one it actually used."""
    expectation = negotiation_message_expectation(
        _case(messages=[[FEASIBILITY]], expected_rounds=1),
        "a2a-t",
    )
    assert expectation is not None

    match = match_negotiation_messages(
        expectation,
        [
            {"round": 1, "kind": "feasibility", "content": FEASIBILITY},
            {"round": 1, "kind": "confirm", "content": "Please confirm the revised target."},
        ],
    )

    assert match.matched is True
    assert match.rounds[0].source_index == 0


def test_an_extra_round_keeps_its_message_for_the_page() -> None:
    expectation = negotiation_message_expectation(
        _case(messages=[[FEASIBILITY]], expected_rounds=1),
        "a2a-t",
    )
    assert expectation is not None

    match = match_negotiation_messages(
        expectation,
        [
            {"round": 1, "kind": "feasibility", "content": FEASIBILITY},
            {"round": 2, "kind": "target", "content": "Anything else?"},
        ],
    )

    assert match.rounds[1].source_index == 1
    assert match.rounds[1].actual == "Anything else?"


def test_any_alternative_of_a_round_satisfies_it() -> None:
    variant = FEASIBILITY.replace("5Mbps", "5 Mbps")
    expectation = negotiation_message_expectation(
        _case(messages=[[FEASIBILITY, variant]], expected_rounds=1),
        "a2a-t",
    )
    assert expectation is not None

    match = match_negotiation_messages(
        expectation,
        [{"round": 1, "kind": "feasibility", "content": variant}],
    )

    assert match.matched is True
    # The first acceptable rendering wins; ``5Mbps`` and ``5 Mbps`` state the same fact, so the
    # first alternative already matches.
    assert match.rounds[0].alternative == 0
