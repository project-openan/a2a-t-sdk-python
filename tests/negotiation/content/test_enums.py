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

"""Tests of the negotiation content model enums (port of the Java enum surface, D2 pinned)."""

from __future__ import annotations

import pytest

from a2a_t.negotiation.content import NegotiationAction, NegotiationConclusion, NegotiationType

ALL_TYPES = list(NegotiationType)
ALL_ACTIONS = list(NegotiationAction)
ALL_CONCLUSIONS = list(NegotiationConclusion)


def test_negotiation_type_pins_the_three_values_in_declaration_order() -> None:
    assert [member.name for member in ALL_TYPES] == ["INFORMATION", "TARGET", "FEASIBILITY"]


@pytest.mark.parametrize(
    ("member", "segment"),
    [
        (NegotiationType.INFORMATION, "information-negotiation"),
        (NegotiationType.TARGET, "target-negotiation"),
        (NegotiationType.FEASIBILITY, "feasibility-negotiation"),
    ],
    ids=lambda value: value if isinstance(value, str) else value.name,
)
def test_negotiation_type_segment_is_hyphenated(member: NegotiationType, segment: str) -> None:
    assert member.type_segment == segment


@pytest.mark.parametrize("member", ALL_TYPES, ids=lambda m: m.name)
def test_negotiation_type_wire_value_is_the_upper_case_name(member: NegotiationType) -> None:
    assert member.value == member.name
    assert str(member) == member.name


def test_negotiation_action_pins_the_two_feasibility_values_in_declaration_order() -> None:
    # The action belongs to the feasibility family only (Java javadoc + sole usages).
    assert [member.name for member in ALL_ACTIONS] == [
        "REQUEST_FEASIBILITY_EVALUATION",
        "PROPOSE_ALTERNATIVE_ON_FAILURE",
    ]


@pytest.mark.parametrize("member", ALL_ACTIONS, ids=lambda m: m.name)
def test_negotiation_action_wire_value_is_the_upper_case_name(member: NegotiationAction) -> None:
    assert member.value == member.name


def test_negotiation_conclusion_keeps_all_three_java_values_including_abort() -> None:
    # D2: the enum is ported verbatim; ABORT stays a value even though the typed generators
    # reject it as a programming error (the rejection is a generator concern, not an enum one).
    assert [member.name for member in ALL_CONCLUSIONS] == ["ACCEPT", "REJECT", "ABORT"]
    assert set(NegotiationConclusion) == {
        NegotiationConclusion.ACCEPT,
        NegotiationConclusion.REJECT,
        NegotiationConclusion.ABORT,
    }


@pytest.mark.parametrize(
    ("member", "literal"),
    [
        (NegotiationConclusion.ACCEPT, "Accept"),
        (NegotiationConclusion.REJECT, "Reject"),
        (NegotiationConclusion.ABORT, "Abort"),
    ],
    ids=lambda value: value if isinstance(value, str) else value.name,
)
def test_negotiation_conclusion_literal_is_the_slot_fill_text(member: NegotiationConclusion, literal: str) -> None:
    assert member.literal == literal
    assert member.value == literal
    assert NegotiationConclusion(literal) is member


def test_wire_values_stay_unique_across_each_enum() -> None:
    assert len({member.value for member in ALL_TYPES}) == len(ALL_TYPES)
    assert len({member.value for member in ALL_ACTIONS}) == len(ALL_ACTIONS)
    assert len({member.value for member in ALL_CONCLUSIONS}) == len(ALL_CONCLUSIONS)
