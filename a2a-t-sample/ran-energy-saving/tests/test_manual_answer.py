"""Tests of the proposal-text parser used by the manual-answer preview."""

from __future__ import annotations

from protocol.manual_answer import (
    missing_item_labels,
    repeats_already_sent,
    requires_operator_decision,
    ungrounded_answer_fields,
)


def test_inline_missing_items_are_split() -> None:
    content = "Please provide the following missing items: Rate, Region and Site list."

    assert missing_item_labels(content) == ("Rate", "Region", "Site list")


def test_numbered_missing_item_block_is_read() -> None:
    content = "## Required Information Items\n1. Rate guarantee goal\n2. Region\n3. Site list\n"

    assert missing_item_labels(content) == ("Rate guarantee goal", "Region", "Site list")


def test_chinese_missing_items_are_read() -> None:
    content = "需要补充以下信息：保障速率、区域。"

    assert missing_item_labels(content) == ("保障速率", "区域")


def test_a_proposal_without_missing_items_returns_empty() -> None:
    assert missing_item_labels("The target is not feasible.") == ()
    assert missing_item_labels("") == ()


def test_correct_item_name_phrasing_is_canonicalized() -> None:
    content = "Energy saving region is not recognized. Please provide the correct region name."

    assert missing_item_labels(content) == ("Region",)


def test_correct_item_name_phrasing_uses_the_bounded_vocabulary() -> None:
    assert missing_item_labels("Please provide the correct cell name.") == ("Cell",)
    assert missing_item_labels("Please provide the correct colour name.") == ()


def test_required_information_block_wins_over_the_inline_pattern() -> None:
    content = "Required Information Items:\n1. Region\n2. Rate"

    assert missing_item_labels(content) == ("Region", "Rate")


def test_an_invented_region_is_not_grounded_in_the_answer() -> None:
    # The reported run: the operator answered a region question, the model returned a value that
    # never appeared in the answer text.
    assert ungrounded_answer_fields(
        "Please use the region the NMS already rejected.",
        {"region": "Region A"},
    ) == ("region",)


def test_values_named_in_the_answer_are_grounded_case_insensitively() -> None:
    assert (
        ungrounded_answer_fields(
            "the region is a2atestregion and the target is 20 mbps",
            {"region": "A2ATestRegion", "guaranteedThroughputTarget": "20Mbps"},
        )
        == ()
    )


def test_an_explicit_selection_counts_as_an_operator_supplied_value() -> None:
    assert (
        ungrounded_answer_fields(
            "Use the selected area.",
            {"region": "A2ATestRegion"},
            allow=("A2ATestRegion",),
        )
        == ()
    )


def test_every_item_of_a_list_value_must_be_grounded() -> None:
    assert ungrounded_answer_fields(
        "The sites are NRES_XCS_6102937.",
        {"siteList": ["NRES_XCS_6102937", "LTE_26441_LNR_OCL"]},
    ) == ("siteList",)


def test_structured_target_notation_matches_its_plain_spelling() -> None:
    assert (
        ungrounded_answer_fields(
            "Set the target to 00:00-06:00 with 2 Mbps and 06:00-24:00 with 10 Mbps.",
            {"guaranteedThroughputTarget": "{00:00~06:00,2Mbps}, {06:00~24:00,10Mbps}"},
        )
        == ()
    )


def test_the_vendor_missing_item_sentence_is_recognized() -> None:
    # The reported yellow-zone question: neither "missing items:" nor "provide the correct X name",
    # so the page and the structuring prompt used to show no asked item at all.
    content = (
        "Energy saving region is missing. Please provide the region name for implementing "
        "energy-saving."
    )

    assert missing_item_labels(content) == ("Region",)


def test_provide_the_item_name_without_correct_is_recognized() -> None:
    assert missing_item_labels("Please provide the site name.") == ("Site",)


#: The exact ASK_USER body of the yellow-zone run ``3a5cf691`` (§2.1 of the evidence file): an
#: infeasibility comparison, a recommended alternative and a question in one envelope.
_YELLOW_ASK_USER = (
    '{"messageType": "ASK_USER", "message": [{"text": "Power outage duration 10h > 4h with '
    'rate 10.0Mbps > 2Mbps; recommend 2Mbps or less, or reduce the outage to 4h or less. '
    'What rate and outage duration would you like to use?"}]}'
)


def test_the_hybrid_feasibility_question_requires_the_operator() -> None:
    """A rejection plus a recommended alternative is a business decision, not missing data."""
    assert requires_operator_decision(_YELLOW_ASK_USER) is True


def test_a_confirmation_question_requires_the_operator() -> None:
    assert requires_operator_decision("Not feasible, please confirm.") is True


def test_a_plain_information_request_stays_automatic() -> None:
    assert requires_operator_decision("Which region?") is False
    assert requires_operator_decision(
        "Energy saving region is missing. Please provide the region name."
    ) is False


def test_a_limit_mention_without_an_alternative_is_not_a_decision() -> None:
    assert requires_operator_decision(
        "Rate 10Mbps exceeds the configured limit. Please provide the correct rate."
    ) is False


def test_an_answer_that_only_restates_a_sent_rate_is_a_repeat() -> None:
    assert repeats_already_sent(
        "Set the guaranteed throughput targets: {08:00~18:00,10Mbps}.",
        ["Set the guaranteed throughput targets: {08:00~18:00,10.0Mbps}."],
    )


def test_an_answer_with_a_new_rate_is_not_a_repeat() -> None:
    assert repeats_already_sent(
        "Set the guaranteed throughput targets: {08:00~18:00,2Mbps}.",
        ["Set the guaranteed throughput targets: {08:00~18:00,10Mbps}."],
    ) is False


def test_a_time_only_sentence_is_never_a_repeat() -> None:
    """The vendor may ask for the window; restating it carries no rejected value."""
    assert repeats_already_sent(
        "Background event: Power outage from 08:00 to 18:00 on Day 2.",
        ["Set the guaranteed throughput targets: {08:00~18:00,10Mbps}."],
    ) is False


def test_common_vendor_rejection_phrasings_require_the_operator() -> None:
    """The gate is a denylist of shapes; every known rejection phrasing must be covered."""
    for content in (
        "The requested outage window cannot be supported. What would you prefer?",
        "The requested 10h outage is too long for this cell.",
        "Please lower the rate for the 08:00-18:00 window.",
        "Please reduce the outage duration to four hours.",
        "该停电窗口无法支持，请调整速率。",
    ):
        assert requires_operator_decision(content) is True, content


def test_an_information_request_without_a_plan_change_stays_automatic() -> None:
    for content in (
        "Please provide the region name.",
        "Which cell should the plan apply to?",
        "Energy saving region is missing.",
    ):
        assert requires_operator_decision(content) is False, content


def test_a_vendor_unavailable_window_requires_the_operator() -> None:
    """The simulator's vendor-shaped wording must not slip through the prose gate."""
    assert requires_operator_decision(
        "The activation time 00:00~06:00 is not available. Available test window: 00:00~24:00."
    ) is True


def test_mbit_per_second_rates_are_compared_like_mbps() -> None:
    """The OSP corpus spells rates as Mbit/s; the same number must count as already sent."""
    assert repeats_already_sent(
        "Set the guaranteed throughput targets: {08:00~18:00,10Mbps}.",
        ["Set the guaranteed throughput targets: {08:00~18:00,10 Mbit/s}."],
    )
