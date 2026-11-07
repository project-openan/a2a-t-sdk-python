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
