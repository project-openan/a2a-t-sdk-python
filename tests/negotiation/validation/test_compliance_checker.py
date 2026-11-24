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

"""Tests of the rule-level compliance checker (port of Java ``ComplianceCheckerTest``).

Every case is deterministic and offline — the checker never calls an LLM. The rule table is pinned
per rule: the exact slot name, catalog code, rendered message and fact values of each violation.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.negotiation.validation.compliance_checker import (
    DefaultNegotiationComplianceChecker,
    NegotiationRuleCheckResult,
)

SESSION_ID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"

EN_US = "en-US"
ZH_CN = "zh-CN"


def context(id: str = SESSION_ID, round: int = 1, max_rounds: int = 5) -> NegotiationContext:
    """Build the negotiation context of one check under test."""
    return NegotiationContext(id, round, max_rounds, NegotiationPerformative.PROPOSE)


@pytest.mark.parametrize(
    ("case_name", "ctx", "expected_slots"),
    [
        ("non-uuid id", context(id="not-a-uuid"), ["id"]),
        ("short id", context(id="short"), ["id"]),
        ("non-hexadecimal id", context(id=SESSION_ID.replace("b", "z")), ["id"]),
        ("round above max_rounds", context(round=3, max_rounds=2), ["round"]),
    ],
    ids=["non-uuid-id", "short-id", "non-hexadecimal-id", "round-above-max-rounds"],
)
def test_context_violations_fail_with_the_expected_slot_names(
    case_name: str, ctx: NegotiationContext, expected_slots: list[str]
) -> None:
    result = DefaultNegotiationComplianceChecker().check(ctx)

    assert result.passed is False, case_name
    assert [error.slot_name for error in result.errors] == expected_slots, case_name


@pytest.mark.parametrize(("round", "max_rounds"), [(2, 5), (5, 5), (1, 1)], ids=["below", "boundary", "single"])
def test_valid_context_passes(round: int, max_rounds: int) -> None:
    result = DefaultNegotiationComplianceChecker().check(context(round=round, max_rounds=max_rounds))

    assert result.passed is True
    assert result.errors == ()


def test_uppercase_hex_uuid_is_accepted() -> None:
    result = DefaultNegotiationComplianceChecker().check(context(id=SESSION_ID.upper()))

    assert result.passed is True
    assert result.errors == ()


def test_both_rules_failing_reports_id_before_round() -> None:
    result = DefaultNegotiationComplianceChecker().check(context(id="short", round=6, max_rounds=5))

    assert result.passed is False
    assert [error.slot_name for error in result.errors] == ["id", "round"]


@pytest.mark.parametrize(
    ("id", "expected_code", "expected_facts"),
    [
        ("short", "negotiation.invalid_context_id", {"actual": "short"}),
        ("not-a-uuid", "negotiation.invalid_context_id", {"actual": "not-a-uuid"}),
        (SESSION_ID.replace("b", "z"), "negotiation.invalid_context_id", {"actual": SESSION_ID.replace("b", "z")}),
    ],
    ids=["short-id", "not-a-uuid", "non-hexadecimal-id"],
)
def test_id_rule_carries_its_exact_code_and_facts(id: str, expected_code: str, expected_facts: dict[str, str]) -> None:
    error = DefaultNegotiationComplianceChecker().check(context(id=id)).errors[0]

    assert error.slot_name == "id"
    assert error.code == expected_code
    assert error.facts == expected_facts
    assert error.message == "The negotiation context id '{actual}' is not a valid UUID".format(**expected_facts)


@pytest.mark.parametrize(
    ("round", "max_rounds"),
    [(6, 5), (2, 1), (9, 5)],
    ids=["one-over", "double", "matrix-row"],
)
def test_round_rule_carries_its_exact_code_and_facts(round: int, max_rounds: int) -> None:
    error = DefaultNegotiationComplianceChecker().check(context(round=round, max_rounds=max_rounds)).errors[0]

    assert error.slot_name == "round"
    assert error.code == "negotiation.round_exceeded"
    assert error.facts == {"round": str(round), "max_rounds": str(max_rounds)}
    assert error.message == "Negotiation round {round} exceeds the maximum of {max_rounds}".format(
        round=round, max_rounds=max_rounds
    )
    assert "maximum" in error.message


def test_rule_failure_messages_render_in_the_configured_language() -> None:
    id_error, round_error = (
        DefaultNegotiationComplianceChecker(ZH_CN).check(context(id="short", round=6, max_rounds=5)).errors
    )

    assert id_error.message == "协商上下文标识「short」不是合法的 UUID"
    assert round_error.message == "协商轮次 6 已超过上限 5"


def test_blank_language_falls_back_to_en_us() -> None:
    error = DefaultNegotiationComplianceChecker(None).check(context(id="short")).errors[0]

    assert error.message == "The negotiation context id 'short' is not a valid UUID"


def test_null_context_is_rejected() -> None:
    with pytest.raises(TypeError):
        DefaultNegotiationComplianceChecker().check(None)  # type: ignore[arg-type]


def test_result_record_has_exactly_the_two_pinned_components() -> None:
    assert [field.name for field in fields(NegotiationRuleCheckResult)] == ["passed", "errors"]


def test_result_normalizes_the_error_sequence() -> None:
    from a2a_t.core.errors.exceptions import SlotValidationError

    result = NegotiationRuleCheckResult(False, [SlotValidationError("id", "negotiation.invalid_context_id", "m")])

    assert isinstance(result.errors, tuple)
    assert len(result.errors) == 1


def test_rule_check_completion_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.DEBUG, logger="a2a_t.negotiation.validation.compliance_checker"):
        DefaultNegotiationComplianceChecker().check(context())
        DefaultNegotiationComplianceChecker().check(context(round=9, max_rounds=5))

    records = [record for record in caplog.records if record.message.startswith("negotiation_rule_checks_completed")]
    assert [(record.levelname, record.message) for record in records] == [
        ("DEBUG", "negotiation_rule_checks_completed passed=True error_count=0"),
        ("WARNING", "negotiation_rule_checks_completed passed=False error_count=1"),
    ]
