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

"""Input strategies of the hypothesis property layer (port of Java ``PropertyArbitraries``).

Legal negotiation inputs per design §8.3, as :mod:`hypothesis.strategies` composables mirroring the
jqwik arbitraries field for field:

* session ids come from a fixed UUID pool so the validate-side rule gate always passes on the id
  dimension;
* ``round`` is always within ``[1, maxRounds]`` and ``maxRounds`` within ``[1, 10]``;
* every typed content strategy only produces shapes the generators accept — non-blank required
  descriptions, non-empty item lists where a generator requires them, and null-or-non-empty
  optional lists elsewhere;
* the target and feasibility propose strategies cover both message categories: the round-driven
  clarification rounds and the confirm-request rounds (whose mutual exclusion the category
  requires), in the Java 2:1 frequency.
"""

from __future__ import annotations

import uuid
from typing import Final

from hypothesis import strategies as st

from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.negotiation.content.enums import NegotiationAction, NegotiationConclusion
from a2a_t.negotiation.content.models import (
    FeasibilityEndingContent,
    FeasibilityProposeContent,
    InformationEndingContent,
    InformationProposeContent,
    NegotiationAbortContent,
    NegotiationEndingContent,
    NegotiationItem,
    NegotiationProposeContent,
    TargetEndingContent,
    TargetProposeContent,
)

__all__ = [
    "ITEM_NAMES",
    "ITEM_VALUES",
    "SESSION_ID_POOL",
    "abort_contents",
    "any_language",
    "any_session_id",
    "contexts",
    "ending_contents",
    "feasibility_propose_contents",
    "information_propose_contents",
    "item_lists",
    "items",
    "languages",
    "optional_item_lists",
    "performatives",
    "propose_contents",
    "random_session_id",
    "target_propose_contents",
]

#: Fixed pool of rule-gate-valid session ids (Java ``SESSION_ID_POOL``).
SESSION_ID_POOL: Final[tuple[str, ...]] = (
    "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3",
    "0f5e9c1a-8b3d-4e7f-9a2c-6d1b4e8f0a53",
    "7c2a1e9b-4f6d-4a3b-8e5c-2f7a9d1b3e64",
    "1a8b3c5d-7e9f-4d2a-b6c4-e8f0a2d4b6c8",
    "9e7d5b3a-1c4f-4a8b-9d2e-6f8a0c2e4d6a",
    "5f3b7d9e-2a4c-4e6b-8a0d-3f5e7c9b1d38",
    "b9d1f3a5-7c2e-4b8d-9f1a-5e7c3b9d1f42",
    "e4c6a8b0-d2f4-4a9c-8e6b-2d0f4a6c8e10",
    "2b6d8f0a-c4e2-4a8c-b0d2-f4a6c8e0b2d4",
    "d8f0b2a4-6e8c-4a2b-8d0f-2b4d6f8a0c3e",
    "6a4c2e0b-8a0d-4f6c-9e1b-3d5f7a9c1e50",
    "0c2e4a6b-9d1f-4b3a-8f5c-7e9b1d3f5a72",
    "f1a3c5e7-2b4d-4f6a-8c0e-b2d4f6a8c0e2",
    "8e0b2d4f-6a8c-4e2b-9d1f-3a5c7e9b1d34",
    "4a6c8e0b-2d4f-4b8a-9c1e-5f7a3b9d1e56",
    "c5e7a9b1-d3f5-4a7c-8e0b-4d6f8a2c0e78",
)

#: The two bundled message languages.
LANGUAGES: Final[tuple[str, ...]] = ("zh-CN", "en-US")

#: Item name pool of the typed content strategies.
ITEM_NAMES: Final[tuple[str, ...]] = (
    "access_port",
    "complaint_category",
    "fault_time",
    "event_serial_no",
    "fault_detail",
    "private_line_id",
    "service_name",
    "access_vlan_id",
    "board_slot",
    "port_bandwidth",
    "latency_target",
    "packet_loss_rate",
)

#: Item value pool of the typed content strategies (50% of the item values are ``None``).
ITEM_VALUES: Final[tuple[str, ...]] = (
    "P533-Zhujiang Old Town-PTN3900-23-TPA1EG24-1",
    "P781-前海-PTN7900-5-TPA1EG24-09(cvlan=100)",
    "dedicated-line quality degradation",
    "private line interruption",
    "2026-05-11T08:21:46Z",
    "event-id-20260511-09013",
    "fault-id-1-017-20260516-11234",
    "within 20ms",
    "no higher than 1%",
    "within 48 hours",
)

#: Non-blank description pool of the required-summary fields.
NON_BLANK_TEXTS: Final[tuple[str, ...]] = (
    "Private line complaint diagnosis negotiation for the Shenzhen-to-Guangzhou dedicated line",
    "Latency repair target negotiation after the quality degradation complaint",
    "Access port expansion feasibility assessment within the cutover window",
    "Complaint information supplement negotiation between the workbench and the OMC",
)

#: Fixed wording pool of the confirm-request category, one entry per language.
CONFIRM_REQUEST_TEXTS: Final[tuple[str, ...]] = (
    "目标已经澄清，是否同意按照此目标继续执行？",
    "The target has been clarified. Do you agree to proceed with this target?",
    "目标评估为可行，是否同意按照此目标继续执行？",
    "The target is assessed as feasible. Do you agree to proceed with this target?",
)


def languages() -> st.SearchStrategy[str]:
    """Strategy of the two bundled message languages.

    :returns: strategy over ``zh-CN`` and ``en-US``.
    """
    return st.sampled_from(LANGUAGES)


def performatives() -> st.SearchStrategy[NegotiationPerformative]:
    """Strategy of the four negotiation performatives.

    :returns: strategy over every :class:`NegotiationPerformative` member.
    """
    return st.sampled_from(tuple(NegotiationPerformative))


@st.composite
def contexts(draw: st.DrawFn) -> NegotiationContext:
    """Strategy of rule-gate-valid negotiation contexts.

    Pooled UUID id, ``round`` in ``[1, maxRounds]``, ``maxRounds`` in ``[1, 10]`` and any of the
    four performatives — the generation pipeline stamps the operation's performative onto the
    emitted context, so the input performative is free.

    :param draw: the hypothesis draw callable.
    :returns: the drawn negotiation context.
    """
    max_rounds = draw(st.integers(min_value=1, max_value=10))
    round_ = draw(st.integers(min_value=1, max_value=max_rounds))
    session_id = draw(st.sampled_from(SESSION_ID_POOL))
    performative = draw(performatives())
    return NegotiationContext(session_id, round_, max_rounds, performative)


@st.composite
def items(draw: st.DrawFn) -> NegotiationItem:
    """Strategy of single items whose value is ``None`` half of the time.

    :param draw: the hypothesis draw callable.
    :returns: the drawn negotiation item.
    """
    name = draw(st.sampled_from(ITEM_NAMES))
    value = draw(st.one_of(st.none(), st.sampled_from(ITEM_VALUES)))
    return NegotiationItem(name, value)


def item_lists(min_size: int, max_size: int) -> st.SearchStrategy[list[NegotiationItem]]:
    """Strategy of item lists of the given size range.

    :param min_size: minimum list size.
    :param max_size: maximum list size.
    :returns: strategy over item lists.
    """
    return st.lists(items(), min_size=min_size, max_size=max_size)


def optional_item_lists() -> st.SearchStrategy[list[NegotiationItem] | None]:
    """Strategy of optional item lists: ``None`` or a non-empty list (the 50% non-empty sections).

    :returns: strategy over ``None`` or item lists of one to four entries.
    """
    return st.one_of(st.none(), item_lists(1, 4))


@st.composite
def information_propose_contents(draw: st.DrawFn) -> InformationProposeContent:
    """Strategy of information propose contents.

    Items in ``[1, 5]`` with 50% ``None`` item values, relationship ``None`` or present. The
    information propose generator requires at least one requested item, so the empty list is not a
    valid input.

    :param draw: the hypothesis draw callable.
    :returns: the drawn information propose content.
    """
    requested = draw(item_lists(1, 5))
    relationship = draw(st.one_of(st.none(), st.sampled_from(ITEM_VALUES)))
    return InformationProposeContent(requested, relationship)


@st.composite
def target_propose_contents(draw: st.DrawFn) -> TargetProposeContent:
    """Strategy of target propose contents covering both message categories.

    The round-driven clarification rounds (non-blank description, each optional list ``None`` or
    non-empty, no confirm request) and the confirm-request rounds (non-blank description and
    confirm request with all three conditional lists ``None``), in the Java 2:1 frequency.

    :param draw: the hypothesis draw callable.
    :returns: the drawn target propose content.
    """
    description = draw(st.sampled_from(NON_BLANK_TEXTS))
    if draw(st.integers(min_value=0, max_value=2)) < 2:  # frequency 2:1 — clarification rounds
        intent = draw(optional_item_lists())
        alignment = draw(optional_item_lists())
        clarification = draw(optional_item_lists())
        return TargetProposeContent(description, intent, alignment, clarification, None)
    confirm_request = draw(st.sampled_from(CONFIRM_REQUEST_TEXTS))
    return TargetProposeContent(description, None, None, None, confirm_request)


@st.composite
def feasibility_propose_contents(draw: st.DrawFn) -> FeasibilityProposeContent:
    """Strategy of feasibility propose contents covering all three message categories.

    Both action branches (the action-selected item list always non-empty, the other one ``None``
    or non-empty) and the derived confirm-request category (``REQUEST_FEASIBILITY_EVALUATION``
    action with both lists ``None`` and a non-blank confirm request, which the mutual exclusion of
    the category requires), in the Java 2:1 frequency.

    :param draw: the hypothesis draw callable.
    :returns: the drawn feasibility propose content.
    """
    description = draw(st.sampled_from(NON_BLANK_TEXTS))
    if draw(st.integers(min_value=0, max_value=2)) < 2:  # frequency 2:1 — action-driven rounds
        action = draw(st.sampled_from(tuple(NegotiationAction)))
        selected = draw(item_lists(1, 4))
        other = draw(optional_item_lists())
        return FeasibilityProposeContent(
            description,
            action,
            selected if action is NegotiationAction.REQUEST_FEASIBILITY_EVALUATION else other,
            selected if action is NegotiationAction.PROPOSE_ALTERNATIVE_ON_FAILURE else other,
            None,
        )
    confirm_request = draw(st.sampled_from(CONFIRM_REQUEST_TEXTS))
    return FeasibilityProposeContent(
        description, NegotiationAction.REQUEST_FEASIBILITY_EVALUATION, None, None, confirm_request
    )


def propose_contents() -> st.SearchStrategy[NegotiationProposeContent]:
    """Strategy over the three propose content types.

    :returns: strategy over information, target and feasibility propose contents.
    """
    return st.one_of(
        information_propose_contents(),
        target_propose_contents(),
        feasibility_propose_contents(),
    )


@st.composite
def ending_contents(draw: st.DrawFn, conclusion: NegotiationConclusion) -> NegotiationEndingContent:
    """Strategy of ending contents carrying the given conclusion across all three types.

    :param draw: the hypothesis draw callable.
    :param conclusion: terminal conclusion the contents carry.
    :returns: the drawn ending content.
    """
    variant = draw(st.integers(min_value=0, max_value=2))
    if variant == 0:  # information ending: delivered items
        return InformationEndingContent(conclusion, draw(item_lists(1, 4)))
    text = draw(st.sampled_from(NON_BLANK_TEXTS))
    if variant == 1:  # target ending: confirmed intent on accept, failure reason on reject
        if conclusion is NegotiationConclusion.ACCEPT:
            return TargetEndingContent(conclusion, text, None)
        return TargetEndingContent(conclusion, None, text)
    return FeasibilityEndingContent(conclusion, text)


def abort_contents() -> st.SearchStrategy[NegotiationAbortContent]:
    """Strategy of abort contents with a non-blank termination reason.

    :returns: strategy over abort contents.
    """
    return st.sampled_from(NON_BLANK_TEXTS).map(NegotiationAbortContent)


def any_session_id() -> str:
    """Pick one session id from the pool outside of the strategy layer.

    :returns: a fixed valid session id.
    """
    return SESSION_ID_POOL[0]


def any_language() -> str:
    """Pick one language outside of the strategy layer.

    :returns: a fixed language.
    """
    return LANGUAGES[0]


def random_session_id() -> str:
    """Build a fresh random session id; used where the pool semantics do not matter.

    :returns: a new UUID string.
    """
    return str(uuid.uuid4())
