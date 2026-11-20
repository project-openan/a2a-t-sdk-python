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

"""Tests of the negotiation content model dataclasses (port of the Java ``content`` records).

The Java records are pure data carriers: no compact constructor validates any component, so these
frozen dataclasses run no ``__post_init__`` validation either. The construction matrix below pins
exactly that contract — blank descriptions, ``None`` conclusions and ``None`` actions stay
representable (mirroring the Java rows of ``FromDataProgrammingErrorMatrixTest`` whose failures
are raised by the generation pipeline, not the records), while ``None`` and empty lists remain
distinct values because both omit the section they drive. The coded validation the model layer
does own lives in ``confirm_request`` (D14) and the reference (see tests/negotiation/resources).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.negotiation.content import (
    FeasibilityEndingContent,
    FeasibilityProposeContent,
    InformationEndingContent,
    InformationProposeContent,
    NegotiationAbortContent,
    NegotiationAbortData,
    NegotiationAction,
    NegotiationConclusion,
    NegotiationContent,
    NegotiationEndingContent,
    NegotiationEndingData,
    NegotiationItem,
    NegotiationProposeContent,
    NegotiationProposeData,
    TargetEndingContent,
    TargetProposeContent,
)

SESSION_ID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"

CONTEXT = NegotiationContext.of(SESSION_ID, 2, NegotiationPerformative.PROPOSE)
ACCEPT_CONTEXT = NegotiationContext.of(SESSION_ID, 2, NegotiationPerformative.ACCEPT)
ITEMS = [NegotiationItem("目标", "2Mbps")]
EMPTY_ITEMS: list[NegotiationItem] = []

PROPOSE_CONTENTS = [
    InformationProposeContent(ITEMS, "缺失项之间的关系。"),
    TargetProposeContent("目标协商概述。", ITEMS, None, None, None),
    FeasibilityProposeContent(
        "可行性协商概述。",
        NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
        ITEMS,
        None,
        None,
    ),
]
ENDING_CONTENTS = [
    InformationEndingContent(NegotiationConclusion.ACCEPT, ITEMS),
    TargetEndingContent(NegotiationConclusion.ACCEPT, "已确认的意图。", None),
    FeasibilityEndingContent(NegotiationConclusion.REJECT, "可行性评估结论。"),
]


def test_item_holds_a_required_name_and_an_optional_value() -> None:
    assert NegotiationItem("目标", "2Mbps") == NegotiationItem("目标", "2Mbps")
    assert NegotiationItem("目标", "2Mbps") != NegotiationItem("目标", "4Mbps")
    assert NegotiationItem("字段", None).value is None


@pytest.mark.parametrize(
    ("content", "fields"),
    [
        (
            InformationProposeContent(ITEMS, "缺失项之间的关系。"),
            {"items": ITEMS, "relationship": "缺失项之间的关系。"},
        ),
        (
            TargetProposeContent("目标协商概述。", ITEMS, None, EMPTY_ITEMS, "请确认。"),
            {
                "target_negotiation_description": "目标协商概述。",
                "intent_understanding": ITEMS,
                "alignment_and_clarification": None,
                "request_for_clarification": EMPTY_ITEMS,
                "target_confirm_request": "请确认。",
            },
        ),
        (
            FeasibilityProposeContent(
                "可行性协商概述。",
                NegotiationAction.PROPOSE_ALTERNATIVE_ON_FAILURE,
                None,
                EMPTY_ITEMS,
                None,
            ),
            {
                "feasibility_negotiation_description": "可行性协商概述。",
                "action": NegotiationAction.PROPOSE_ALTERNATIVE_ON_FAILURE,
                "contents_to_evaluate": None,
                "infeasibility_details_and_proposal": EMPTY_ITEMS,
                "feasibility_confirm_request": None,
            },
        ),
        (NegotiationAbortContent("达到轮次上限。"), {"termination_reason": "达到轮次上限。"}),
    ],
    ids=["information-propose", "target-propose", "feasibility-propose", "abort"],
)
def test_propose_and_abort_contents_round_trip_every_field(
    content: NegotiationContent, fields: dict[str, object]
) -> None:
    for name, value in fields.items():
        assert getattr(content, name) == value


@pytest.mark.parametrize(
    ("content", "conclusion"),
    [
        (InformationEndingContent(NegotiationConclusion.REJECT, ITEMS), NegotiationConclusion.REJECT),
        (TargetEndingContent(NegotiationConclusion.ACCEPT, None, "原因。"), NegotiationConclusion.ACCEPT),
        (FeasibilityEndingContent(NegotiationConclusion.ACCEPT, "总结。"), NegotiationConclusion.ACCEPT),
    ],
    ids=["information-ending", "target-ending", "feasibility-ending"],
)
def test_ending_contents_carry_the_shared_conclusion_component_first(
    content: NegotiationEndingContent, conclusion: NegotiationConclusion
) -> None:
    assert content.conclusion == conclusion
    assert isinstance(content, NegotiationEndingContent)
    assert isinstance(content, NegotiationContent)


def test_ending_construction_order_matches_the_java_record_order() -> None:
    # Every Java ending record declares its conclusion component first.
    ending = InformationEndingContent(NegotiationConclusion.ACCEPT, ITEMS)
    assert (ending.conclusion, ending.items) == (NegotiationConclusion.ACCEPT, ITEMS)


@pytest.mark.parametrize("content", PROPOSE_CONTENTS, ids=lambda c: type(c).__name__)
def test_propose_family_membership(content: NegotiationProposeContent) -> None:
    assert isinstance(content, NegotiationProposeContent)
    assert isinstance(content, NegotiationContent)
    assert not isinstance(content, NegotiationEndingContent)


@pytest.mark.parametrize("content", ENDING_CONTENTS, ids=lambda c: type(c).__name__)
def test_ending_family_membership(content: NegotiationEndingContent) -> None:
    assert isinstance(content, NegotiationEndingContent)
    assert isinstance(content, NegotiationContent)
    assert not isinstance(content, NegotiationProposeContent)


def test_abort_content_is_neither_propose_nor_ending() -> None:
    abort = NegotiationAbortContent("达到轮次上限。")
    assert isinstance(abort, NegotiationContent)
    assert not isinstance(abort, NegotiationProposeContent)
    assert not isinstance(abort, NegotiationEndingContent)


@pytest.mark.parametrize(
    ("data", "context", "content"),
    [
        (NegotiationProposeData(CONTEXT, PROPOSE_CONTENTS[0]), CONTEXT, PROPOSE_CONTENTS[0]),
        (NegotiationEndingData(ACCEPT_CONTEXT, ENDING_CONTENTS[0]), ACCEPT_CONTEXT, ENDING_CONTENTS[0]),
        (NegotiationAbortData(CONTEXT, NegotiationAbortContent("超时。")), CONTEXT, NegotiationAbortContent("超时。")),
    ],
    ids=["propose", "ending", "abort"],
)
def test_data_bundles_carry_the_context_and_the_typed_content(
    data: object, context: NegotiationContext, content: NegotiationContent
) -> None:
    assert data.context == context  # type: ignore[attr-defined]
    assert data.content == content  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("content", "field"),
    [
        (InformationProposeContent(ITEMS, None), "relationship"),
        (TargetProposeContent("描述。", None, None, None, None), "target_confirm_request"),
        (
            FeasibilityProposeContent("描述。", NegotiationAction.REQUEST_FEASIBILITY_EVALUATION, ITEMS, None, None),
            "feasibility_confirm_request",
        ),
        (InformationEndingContent(NegotiationConclusion.ACCEPT, ITEMS), "items"),
        (TargetEndingContent(NegotiationConclusion.ACCEPT, "意图。", None), "confirmed_intent"),
        (FeasibilityEndingContent(NegotiationConclusion.ACCEPT, "总结。"), "feasibility_summary"),
        (NegotiationAbortContent("达到轮次上限。"), "termination_reason"),
        (NegotiationItem("目标", "2Mbps"), "name"),
    ],
    ids=lambda value: value if isinstance(value, str) else type(value).__name__,
)
def test_contents_are_frozen(content: NegotiationContent, field: str) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(content, field, "mutated")


@pytest.mark.parametrize(
    "content",
    [
        NegotiationItem("目标", "2Mbps"),
        NegotiationAbortContent("达到轮次上限。"),
        TargetEndingContent(NegotiationConclusion.REJECT, None, "原因。"),
    ],
    ids=["item", "abort", "ending-without-items"],
)
def test_contents_without_item_lists_are_hashable(content: NegotiationContent) -> None:
    # Java records hash structurally; contents holding item lists stay unhashable (documented
    # divergence — equality is the supported identity operation).
    assert hash(content) == hash(content)


@pytest.mark.parametrize(
    ("content", "other"),
    [
        (InformationProposeContent(ITEMS, None), InformationProposeContent(ITEMS, None)),
        (
            FeasibilityProposeContent("描述。", NegotiationAction.REQUEST_FEASIBILITY_EVALUATION, ITEMS, None, None),
            FeasibilityProposeContent("描述。", NegotiationAction.REQUEST_FEASIBILITY_EVALUATION, ITEMS, None, None),
        ),
    ],
    ids=["structural-equality", "action-participates-in-equality"],
)
def test_structural_equality(content: NegotiationContent, other: NegotiationContent) -> None:
    assert content == other


def test_contents_never_equal_foreign_types() -> None:
    content = TargetEndingContent(NegotiationConclusion.REJECT, None, "原因。")
    assert content != NegotiationItem("目标", None)
    assert content != "not a content"
    assert content is not None


@pytest.mark.parametrize(
    ("content", "mutated"),
    [
        (InformationProposeContent(ITEMS, None), InformationProposeContent([], None)),
        (
            TargetProposeContent("描述。", None, None, None, None),
            TargetProposeContent("描述。", None, None, None, "确认。"),
        ),
        (
            FeasibilityEndingContent(NegotiationConclusion.ACCEPT, "总结。"),
            FeasibilityEndingContent(NegotiationConclusion.REJECT, "总结。"),
        ),
        (
            TargetEndingContent(NegotiationConclusion.ACCEPT, "意图。", None),
            TargetEndingContent(NegotiationConclusion.ACCEPT, None, "原因。"),
        ),
    ],
    ids=["items", "confirm-request", "conclusion", "conclusion-selected-field"],
)
def test_field_differences_break_equality(content: NegotiationContent, mutated: NegotiationContent) -> None:
    assert content != mutated


@pytest.mark.parametrize(
    ("content", "field", "expected"),
    [
        (InformationProposeContent(None, "关系。"), "items", None),  # type: ignore[list-item]
        (InformationProposeContent(ITEMS, None), "relationship", None),
        (TargetProposeContent(" ", None, None, None, None), "target_negotiation_description", " "),
        (FeasibilityProposeContent(" ", None, None, None, None), "feasibility_negotiation_description", " "),  # type: ignore[arg-type]
        (FeasibilityEndingContent(NegotiationConclusion.ACCEPT, " "), "feasibility_summary", " "),
        (NegotiationAbortContent(" "), "termination_reason", " "),
        (NegotiationItem(" ", "值"), "name", " "),
        (NegotiationItem("目标", None), "value", None),
    ],
    ids=lambda value: value if isinstance(value, str) else type(value).__name__,
)
def test_the_records_are_unvalidated_carriers(content: NegotiationContent, field: str, expected: object) -> None:
    """Blank and None components construct and read back — the pipeline, not the record, rejects them.

    Mirrors the Java design pinned by ``FromDataProgrammingErrorMatrixTest``: blank propose
    descriptions and ending summaries become coded ``negotiation.content_invalid`` failures inside
    the generators, and null actions or conclusions become programming errors there.
    """
    assert getattr(content, field) == expected


@pytest.mark.parametrize(
    "content",
    [
        TargetEndingContent(None, "已确认的意图。", None),  # type: ignore[arg-type]
        InformationEndingContent(None, ITEMS),  # type: ignore[arg-type]
        FeasibilityEndingContent(None, "总结。"),  # type: ignore[arg-type]
        FeasibilityProposeContent("请评估。", None, ITEMS, None, None),  # type: ignore[arg-type]
    ],
    ids=[
        "target-ending-null-conclusion",
        "information-ending-null-conclusion",
        "feasibility-ending-null-conclusion",
        "feasibility-propose-null-action",
    ],
)
def test_null_conclusions_and_actions_stay_representable(content: NegotiationContent) -> None:
    """The matrix rows whose NPE the orchestrator raises: the model itself carries them."""
    assert content is not None


def test_none_and_empty_lists_are_distinct_values() -> None:
    absent = InformationProposeContent(None, None)  # type: ignore[list-item]
    empty = InformationProposeContent(EMPTY_ITEMS, None)
    assert absent != empty
    assert absent.items is None
    assert empty.items == []
