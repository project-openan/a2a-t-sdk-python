"""Tests of the proposal-text parser used by the manual-answer preview."""

from __future__ import annotations

from protocol.manual_answer import missing_item_labels


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
