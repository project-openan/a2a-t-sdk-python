# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Tests of the shared confirm-request mutual-exclusion validation (D14).

Ports the full mutual-exclusion matrix expressed by the TWO Java implementations — the from-data
generators (``TargetProposeGenerator`` / ``FeasibilityProposeGenerator``) and the from-text
extractor (``DefaultNegotiationContentExtractor.mapTargetProposeContent`` /
``mapFeasibilityProposeContent``) — against the single shared function. Every violating cell raises
``negotiation.invalid_input`` carrying the ``reason`` fact with the exact Java sentence; every
non-violating cell returns cleanly. The catalog code ``negotiation.mutually_exclusive_sections``
exists but is never raised by these Java sites, so it is never raised here either.
"""

from __future__ import annotations

import pytest

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATBusinessError, A2ATError, NegotiationGenerationError
from a2a_t.negotiation.content import (
    NegotiationAction,
    NegotiationItem,
    has_items,
    present_text,
    validate_confirm_request,
)

ITEMS = [NegotiationItem("目标", "2Mbps")]
EMPTY_ITEMS: list[NegotiationItem] = []
CONFIRM = "请确认目标协商结果。"

TARGET_SECTIONS_OK = {"intent understanding": None, "alignment and clarification": None, "clarification request": None}
FEASIBILITY_SECTIONS_OK = {"contents to evaluate": None, "infeasibility details and proposal": None}

TARGET_COMBINED_REASON = (
    "Target confirm request must not be combined with the intent understanding, alignment and "
    "clarification or clarification request sections; a confirm-request round carries only the "
    "summary and the confirm request."
)
TARGET_EXTRACTED_REASON = (
    "Target confirm request extracted together with the intent understanding, alignment and "
    "clarification or clarification request sections; a confirm-request round carries only the "
    "summary and the confirm request."
)
FEASIBILITY_SECTIONS_COMBINED_REASON = (
    "Feasibility confirm request must not be combined with the contents to evaluate or "
    "infeasibility details and proposal sections; a confirm-request round carries only the summary "
    "and the confirm request."
)
FEASIBILITY_SECTIONS_EXTRACTED_REASON = (
    "Feasibility confirm request extracted together with the contents to evaluate or "
    "infeasibility details and proposal sections; a confirm-request round carries only the summary "
    "and the confirm request."
)
FEASIBILITY_ACTION_COMBINED_REASON = (
    "Feasibility confirm request requires the REQUEST_FEASIBILITY_EVALUATION action but the "
    "content carries PROPOSE_ALTERNATIVE_ON_FAILURE."
)
FEASIBILITY_ACTION_EXTRACTED_REASON = (
    "Feasibility confirm request requires the REQUEST_FEASIBILITY_EVALUATION action but the "
    "extracted action was PROPOSE_ALTERNATIVE_ON_FAILURE."
)


def target_sections(
    intent: list[NegotiationItem] | None,
    alignment: list[NegotiationItem] | None,
    clarification: list[NegotiationItem] | None,
) -> dict[str, list[NegotiationItem] | None]:
    """Build the target family's conditional sections in template order."""
    return {
        "intent understanding": intent,
        "alignment and clarification": alignment,
        "clarification request": clarification,
    }


def feasibility_sections(
    contents: list[NegotiationItem] | None, infeasibility: list[NegotiationItem] | None
) -> dict[str, list[NegotiationItem] | None]:
    """Build the feasibility family's conditional sections in template order."""
    return {"contents to evaluate": contents, "infeasibility details and proposal": infeasibility}


# ---------------------------------------------------------------------------
# Target family: confirm request x three conditional sections
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "confirm_request",
    [None, "", "  \t "],
    ids=["none", "empty", "blank"],
)
@pytest.mark.parametrize(
    ("intent", "alignment", "clarification"),
    [
        (None, None, None),
        (EMPTY_ITEMS, EMPTY_ITEMS, EMPTY_ITEMS),
        (ITEMS, None, None),
        (None, ITEMS, None),
        (None, None, ITEMS),
        (ITEMS, ITEMS, ITEMS),
    ],
    ids=["all-absent", "all-empty", "intent", "alignment", "clarification", "all-present"],
)
def test_target_confirm_request_absent_accepts_any_section_combination(
    confirm_request: str | None,
    intent: list[NegotiationItem] | None,
    alignment: list[NegotiationItem] | None,
    clarification: list[NegotiationItem] | None,
) -> None:
    validate_confirm_request(
        label="Target",
        confirm_request=confirm_request,
        conditional_sections=target_sections(intent, alignment, clarification),
    )


@pytest.mark.parametrize(
    ("intent", "alignment", "clarification"),
    [(None, None, None), (EMPTY_ITEMS, EMPTY_ITEMS, EMPTY_ITEMS)],
    ids=["all-absent", "all-empty"],
)
@pytest.mark.parametrize("confirm_request", [CONFIRM, "请确认。"], ids=["confirm", "another-confirm"])
def test_target_confirm_request_present_accepts_empty_rounds(
    confirm_request: str,
    intent: list[NegotiationItem] | None,
    alignment: list[NegotiationItem] | None,
    clarification: list[NegotiationItem] | None,
) -> None:
    validate_confirm_request(
        label="Target",
        confirm_request=confirm_request,
        conditional_sections=target_sections(intent, alignment, clarification),
    )


@pytest.mark.parametrize(
    ("intent", "alignment", "clarification"),
    [
        (ITEMS, None, None),
        (EMPTY_ITEMS, ITEMS, None),
        (None, EMPTY_ITEMS, ITEMS),
        (ITEMS, ITEMS, None),
        (ITEMS, ITEMS, ITEMS),
    ],
    ids=["intent-only", "alignment-only", "clarification-only", "two-sections", "all-three"],
)
@pytest.mark.parametrize("style", ["combined", "extracted"])
def test_target_confirm_request_with_any_section_fails_with_invalid_input(
    style: str,
    intent: list[NegotiationItem] | None,
    alignment: list[NegotiationItem] | None,
    clarification: list[NegotiationItem] | None,
) -> None:
    with pytest.raises(NegotiationGenerationError) as info:
        validate_confirm_request(
            label="Target",
            confirm_request=CONFIRM,
            conditional_sections=target_sections(intent, alignment, clarification),
            style=style,  # type: ignore[arg-type]
        )
    expected = TARGET_COMBINED_REASON if style == "combined" else TARGET_EXTRACTED_REASON
    _assert_invalid_input(info.value, expected)


def test_target_reason_names_all_three_sections_not_only_the_offending_one() -> None:
    # The Java reasons are static per family; the message is identical whichever section carries
    # the items (pinned above) and names every conditional section of the family.
    assert "intent understanding" in TARGET_COMBINED_REASON
    assert "alignment and clarification" in TARGET_COMBINED_REASON
    assert "clarification request" in TARGET_COMBINED_REASON


# ---------------------------------------------------------------------------
# Feasibility family: confirm request x action x two conditional sections
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("confirm_request", [None, "", "  "], ids=["none", "empty", "blank"])
@pytest.mark.parametrize("action", list(NegotiationAction), ids=lambda a: a.name)
@pytest.mark.parametrize(
    ("contents", "infeasibility"),
    [(None, None), (EMPTY_ITEMS, None), (ITEMS, ITEMS)],
    ids=["both-absent", "one-empty", "both-present"],
)
def test_feasibility_confirm_request_absent_accepts_any_combination(
    confirm_request: str | None,
    action: NegotiationAction,
    contents: list[NegotiationItem] | None,
    infeasibility: list[NegotiationItem] | None,
) -> None:
    validate_confirm_request(
        label="Feasibility",
        confirm_request=confirm_request,
        conditional_sections=feasibility_sections(contents, infeasibility),
        action=action,
        confirm_action=NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
    )


@pytest.mark.parametrize(
    ("contents", "infeasibility"),
    [(None, None), (EMPTY_ITEMS, EMPTY_ITEMS)],
    ids=["both-absent", "both-empty"],
)
def test_feasibility_confirm_request_with_evaluation_action_and_empty_lists_is_the_third_category(
    contents: list[NegotiationItem] | None, infeasibility: list[NegotiationItem] | None
) -> None:
    validate_confirm_request(
        label="Feasibility",
        confirm_request=CONFIRM,
        conditional_sections=feasibility_sections(contents, infeasibility),
        action=NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
        confirm_action=NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
    )


@pytest.mark.parametrize(
    ("contents", "infeasibility"),
    [(ITEMS, None), (None, ITEMS), (ITEMS, ITEMS)],
    ids=["contents-only", "infeasibility-only", "both"],
)
@pytest.mark.parametrize("style", ["combined", "extracted"])
def test_feasibility_confirm_request_with_any_section_fails_with_invalid_input(
    style: str, contents: list[NegotiationItem] | None, infeasibility: list[NegotiationItem] | None
) -> None:
    with pytest.raises(NegotiationGenerationError) as info:
        validate_confirm_request(
            label="Feasibility",
            confirm_request=CONFIRM,
            conditional_sections=feasibility_sections(contents, infeasibility),
            action=NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
            confirm_action=NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
            style=style,  # type: ignore[arg-type]
        )
    expected = FEASIBILITY_SECTIONS_COMBINED_REASON if style == "combined" else FEASIBILITY_SECTIONS_EXTRACTED_REASON
    _assert_invalid_input(info.value, expected)


@pytest.mark.parametrize("style", ["combined", "extracted"])
@pytest.mark.parametrize(
    ("contents", "infeasibility"), [(None, None), (ITEMS, None)], ids=["empty-round", "with-sections"]
)
def test_feasibility_confirm_request_with_the_wrong_action_fails_before_the_sections_check(
    style: str, contents: list[NegotiationItem] | None, infeasibility: list[NegotiationItem] | None
) -> None:
    """The action mismatch is reported first, exactly like the Java ordering."""
    with pytest.raises(NegotiationGenerationError) as info:
        validate_confirm_request(
            label="Feasibility",
            confirm_request=CONFIRM,
            conditional_sections=feasibility_sections(contents, infeasibility),
            action=NegotiationAction.PROPOSE_ALTERNATIVE_ON_FAILURE,
            confirm_action=NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
            style=style,  # type: ignore[arg-type]
        )
    expected = FEASIBILITY_ACTION_COMBINED_REASON if style == "combined" else FEASIBILITY_ACTION_EXTRACTED_REASON
    _assert_invalid_input(info.value, expected)


def test_feasibility_action_check_ignores_none_actions_when_no_confirm_action_is_given() -> None:
    # Target-style families pass no action at all; the action branch must stay inert for them.
    validate_confirm_request(
        label="Target",
        confirm_request=CONFIRM,
        conditional_sections=TARGET_SECTIONS_OK,
        action=None,
    )


def test_none_action_with_a_required_confirm_action_is_a_programming_error() -> None:
    # Both Java sites reject a missing action (NullPointerException) before reaching this
    # validation; the port's programming-error convention maps that to TypeError.
    with pytest.raises(TypeError, match="action must not be null"):
        validate_confirm_request(
            label="Feasibility",
            confirm_request=CONFIRM,
            conditional_sections=FEASIBILITY_SECTIONS_OK,
            action=None,
            confirm_action=NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
        )


# ---------------------------------------------------------------------------
# Failure contract and the micro-helpers
# ---------------------------------------------------------------------------


def test_the_failure_renders_its_message_from_the_catalog_template_per_language() -> None:
    with pytest.raises(NegotiationGenerationError) as info:
        validate_confirm_request(
            label="Target",
            confirm_request=CONFIRM,
            conditional_sections=target_sections(ITEMS, None, None),
            language="zh-CN",
        )
    assert str(info.value) == f"输入的协商内容无效:{TARGET_COMBINED_REASON}"

    with pytest.raises(NegotiationGenerationError) as en:
        validate_confirm_request(
            label="Target",
            confirm_request=CONFIRM,
            conditional_sections=target_sections(ITEMS, None, None),
            language="en-US",
        )
    assert str(en.value) == f"The negotiation input is invalid: {TARGET_COMBINED_REASON}"


def test_the_failure_stays_outside_the_default_language_rendering() -> None:
    with pytest.raises(NegotiationGenerationError) as info:
        validate_confirm_request(
            label="Feasibility",
            confirm_request=CONFIRM,
            conditional_sections=feasibility_sections(None, ITEMS),
            action=NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
            confirm_action=NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
        )
    assert str(info.value) == f"The negotiation input is invalid: {FEASIBILITY_SECTIONS_COMBINED_REASON}"


@pytest.mark.parametrize("confirm_request", [None, "", "  "], ids=["none", "empty", "blank"])
def test_a_blank_confirm_request_means_no_confirm_request_round(confirm_request: str | None) -> None:
    assert present_text(confirm_request) is None


@pytest.mark.parametrize("value", ["请确认。", " x "], ids=["plain", "padded"])
def test_present_text_returns_the_text_unchanged(value: str) -> None:
    assert present_text(value) == value


@pytest.mark.parametrize(
    ("items", "expected"),
    [(None, False), (EMPTY_ITEMS, False), (ITEMS, True)],
    ids=["none", "empty", "items"],
)
def test_has_items_treats_none_and_empty_as_absent(items: list[NegotiationItem] | None, expected: bool) -> None:
    assert has_items(items) is expected


def _assert_invalid_input(error: NegotiationGenerationError, reason: str) -> None:
    """Assert the exact failure contract both Java sites produce."""
    assert isinstance(error, NegotiationGenerationError)
    assert isinstance(error, A2ATBusinessError)
    assert isinstance(error, A2ATError)
    assert error.code is ErrorCatalog.NEGOTIATION_INVALID_INPUT
    assert error.code_str == "negotiation.invalid_input"
    assert error.facts == {"reason": reason}
    assert reason in str(error)
