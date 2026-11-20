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

"""Typed content model of negotiation messages (port of the Java ``content`` records).

The Java records are pure data carriers by design: no compact constructor validates any component.
Blank descriptions, ``None`` conclusions and ``None`` actions stay representable so the generation
pipeline can translate them into the coded business failure ``negotiation.content_invalid``, the
extractor failure ``negotiation.field_missing`` / ``negotiation.content_extract_failed`` or the
programming-error :class:`TypeError` at the exact call site that requires them (the Java
``FromDataProgrammingErrorMatrixTest`` pins the same split). Mirroring that contract exactly,
these dataclasses perform no ``__post_init__`` validation either — the one piece of content
validation Java duplicated in the generators and the extractor lives in
:mod:`a2a_t.negotiation.content.confirm_request` (D14), and the addressing validation lives in
:class:`a2a_t.negotiation.resources.reference.NegotiationReference`.

Field nullability follows the Java ``@Nullable`` annotations: fields declared nullable there are
``| None`` here, and fields declared non-null there (``conclusion``, ``action``, the required
summaries) keep their non-optional annotations while still accepting ``None`` at runtime, exactly
like an unvalidated Java record. A ``None`` list and an empty list are distinct values; both omit
the section they drive when rendered.

One deliberate divergence: Java records hash structurally (``List.hashCode``), while these
dataclasses carry real lists and are therefore unhashable whenever they hold items. Equality is
the supported identity operation; callers needing a hash key should key on an explicit projection.
"""

from __future__ import annotations

from dataclasses import dataclass

from a2a_t.core.metadata import NegotiationContext

from .enums import NegotiationAction, NegotiationConclusion

__all__ = [
    "FeasibilityEndingContent",
    "FeasibilityProposeContent",
    "InformationEndingContent",
    "InformationProposeContent",
    "NegotiationAbortContent",
    "NegotiationAbortData",
    "NegotiationContent",
    "NegotiationEndingContent",
    "NegotiationEndingData",
    "NegotiationItem",
    "NegotiationProposeContent",
    "NegotiationProposeData",
    "TargetEndingContent",
    "TargetProposeContent",
]


@dataclass(frozen=True, slots=True)
class NegotiationContent:
    """Marker for the typed content of any negotiation message, regardless of phase.

    The concrete families are :class:`NegotiationProposeContent` for propose-phase messages,
    :class:`NegotiationEndingContent` for terminal accept/reject messages and
    :class:`NegotiationAbortContent` for the type-independent abort message. Generators and
    content extractors accept this common supertype and dispatch on the exact runtime type.
    """


@dataclass(frozen=True, slots=True)
class NegotiationProposeContent(NegotiationContent):
    """Marker for the typed content of a propose-phase negotiation message."""


@dataclass(frozen=True, slots=True)
class NegotiationEndingContent(NegotiationContent):
    """Marker for the typed content of a terminal (accept or reject) negotiation message.

    Carries the shared ``conclusion`` component (the Java interface's ``conclusion()`` accessor):
    every terminal content is constructed with its conclusion first, followed by its own fields.

    Attributes:
        conclusion: terminal outcome of the negotiation; ``ACCEPT`` and ``REJECT`` are renderable,
            ``ABORT`` is rejected by the typed generators (D2).
    """

    conclusion: NegotiationConclusion


@dataclass(frozen=True, slots=True)
class NegotiationItem:
    """One named entry of a negotiation item list.

    Attributes:
        name: item name such as a field path or identifier; never blank in valid content.
        value: free-form explanation of the item such as meaning, format, example or reason; may
            be ``None``.
    """

    name: str
    value: str | None


@dataclass(frozen=True, slots=True)
class InformationProposeContent(NegotiationProposeContent):
    """Content of an information negotiation propose message.

    Attributes:
        items: information items the requester is missing.
        relationship: free-form description of how the missing items relate to each other;
            ``None`` when there is none.
    """

    items: list[NegotiationItem]
    relationship: str | None


@dataclass(frozen=True, slots=True)
class InformationEndingContent(NegotiationEndingContent):
    """Content of an information negotiation terminal message.

    Attributes:
        conclusion: terminal conclusion of the information negotiation.
        items: information items delivered with the terminal message.
    """

    items: list[NegotiationItem]


@dataclass(frozen=True, slots=True)
class TargetProposeContent(NegotiationProposeContent):
    """Content of a target negotiation propose message.

    Attributes:
        target_negotiation_description: required summary of what the target negotiation is about.
        intent_understanding: restatement of the counterpart's intent; ``None`` or empty omits the
            section (first round only).
        alignment_and_clarification: alignment statements and clarifications; ``None`` or empty
            omits the section (later rounds only).
        request_for_clarification: open clarification requests; ``None`` or empty omits the
            section.
        target_confirm_request: non-blank when this round's message category is "target clarified
            and requesting confirmation from the counterpart"; the three conditional lists above
            must then all be ``None`` or empty, because a confirm-request round carries only the
            summary and the confirm request (the fixed wording is not validated and passes through
            verbatim).
    """

    target_negotiation_description: str
    intent_understanding: list[NegotiationItem] | None
    alignment_and_clarification: list[NegotiationItem] | None
    request_for_clarification: list[NegotiationItem] | None
    target_confirm_request: str | None


@dataclass(frozen=True, slots=True)
class TargetEndingContent(NegotiationEndingContent):
    """Content of a target negotiation terminal message.

    Exactly one of ``confirmed_intent`` and ``failure_reason`` is meaningful, selected by the
    conclusion: an accept message must carry the confirmed intent, a reject message must carry the
    failure reason.

    Attributes:
        conclusion: terminal conclusion of the target negotiation.
        confirmed_intent: confirmed intent for an accept conclusion; ``None`` otherwise.
        failure_reason: reason for a reject conclusion; ``None`` otherwise.
    """

    confirmed_intent: str | None
    failure_reason: str | None


@dataclass(frozen=True, slots=True)
class FeasibilityProposeContent(NegotiationProposeContent):
    """Content of a feasibility negotiation propose message.

    Attributes:
        feasibility_negotiation_description: required summary describing the nature of the
            message.
        action: action this propose message performs; selects which conditional section is
            rendered.
        contents_to_evaluate: contents the counterpart should evaluate; ``None`` or empty omits
            the section (only for :attr:`NegotiationAction.REQUEST_FEASIBILITY_EVALUATION`).
        infeasibility_details_and_proposal: infeasibility details and an alternative proposal;
            ``None`` or empty omits the section (only for
            :attr:`NegotiationAction.PROPOSE_ALTERNATIVE_ON_FAILURE`).
        feasibility_confirm_request: non-blank when this round's message category is "assessed as
            feasible and requesting confirmation" — the third category is derived and the action
            stays at two values: it requires the
            :attr:`NegotiationAction.REQUEST_FEASIBILITY_EVALUATION` action with both lists above
            ``None`` or empty, so a confirm-request round carries only the summary and the confirm
            request (the fixed wording is not validated and passes through verbatim).
    """

    feasibility_negotiation_description: str
    action: NegotiationAction
    contents_to_evaluate: list[NegotiationItem] | None
    infeasibility_details_and_proposal: list[NegotiationItem] | None
    feasibility_confirm_request: str | None


@dataclass(frozen=True, slots=True)
class FeasibilityEndingContent(NegotiationEndingContent):
    """Content of a feasibility negotiation terminal message.

    Attributes:
        conclusion: terminal conclusion of the feasibility negotiation.
        feasibility_summary: required summary of the feasibility evaluation result.
    """

    feasibility_summary: str


@dataclass(frozen=True, slots=True)
class NegotiationAbortContent(NegotiationContent):
    """Content of an abort negotiation message.

    Abort messages are type-independent: the single common abort template carries the fixed
    ``Abort`` conclusion section and this content only fills the termination reason.

    Attributes:
        termination_reason: human-readable reason for terminating the negotiation, such as
            reaching the round limit, a timeout or a token budget exhaustion.
    """

    termination_reason: str


@dataclass(frozen=True, slots=True)
class NegotiationProposeData:
    """Input bundle for generating a propose-phase negotiation message from typed data.

    Attributes:
        context: negotiation session context.
        content: typed propose content matching the negotiation type addressed by the template
            URI.
    """

    context: NegotiationContext
    content: NegotiationProposeContent


@dataclass(frozen=True, slots=True)
class NegotiationEndingData:
    """Input bundle for generating a terminal (accept or reject) negotiation message from typed data.

    Both accept and reject generation methods take this same bundle. The conclusion constraint is
    carried by the method, not the type: ``generate_accept_from_data`` requires
    ``content.conclusion == ACCEPT`` and ``generate_reject_from_data`` requires ``REJECT``;
    generation methods enforce it and reject a mismatched conclusion (including ``ABORT``) as a
    content error.

    Attributes:
        context: negotiation session context.
        content: typed terminal content matching the negotiation type addressed by the template
            URI.
    """

    context: NegotiationContext
    content: NegotiationEndingContent


@dataclass(frozen=True, slots=True)
class NegotiationAbortData:
    """Input bundle for generating an abort negotiation message from typed data.

    The abort message is type-independent and terminates a negotiation outside the accept/reject
    outcome model; the ``Abort`` conclusion is fixed template text, so the content carries only
    the termination reason.

    Attributes:
        context: negotiation session context.
        content: typed abort content carrying the termination reason.
    """

    context: NegotiationContext
    content: NegotiationAbortContent
