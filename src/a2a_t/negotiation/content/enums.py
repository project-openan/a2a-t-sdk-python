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

"""Enums of the negotiation content model (port of the Java ``content`` enums).

:class:`NegotiationType`, :class:`NegotiationAction` and :class:`NegotiationConclusion` mirror
their Java counterparts value-for-value. The performative is deliberately absent: it is a core
model concept owned by :class:`a2a_t.core.metadata.NegotiationPerformative` and reused by this
package (a performative is a speech act, not a negotiation-type-specific phase — port plan 7.5).

Decision D2 is pinned here: :class:`NegotiationConclusion` keeps all three Java values including
``ABORT`` even though the typed generators reject it — the abort message is generated through the
type-independent common abort template instead, and the rejection is a generator concern (P5),
not an enum concern.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

__all__ = ["NegotiationAction", "NegotiationConclusion", "NegotiationType"]


class NegotiationType(StrEnum):
    """Kind of negotiation a negotiation message belongs to.

    The enum also owns the hyphenated URI segment used both in template URIs and in template
    resource paths, so that URIs and on-disk locations can never drift apart.
    """

    #: Negotiation about missing information the requester needs.
    INFORMATION = "INFORMATION"

    #: Negotiation about aligning the target and intent of a request.
    TARGET = "TARGET"

    #: Negotiation about whether a request can be fulfilled at all.
    FEASIBILITY = "FEASIBILITY"

    @property
    def type_segment(self) -> str:
        """Hyphenated URI segment identifying this negotiation type.

        Returns:
            URI segment such as ``information-negotiation``.
        """
        return _TYPE_SEGMENTS[self]


#: Hyphenated URI segment of each negotiation type (the Java enum's constructor argument).
_TYPE_SEGMENTS: Final[dict[NegotiationType, str]] = {
    NegotiationType.INFORMATION: "information-negotiation",
    NegotiationType.TARGET: "target-negotiation",
    NegotiationType.FEASIBILITY: "feasibility-negotiation",
}


class NegotiationAction(StrEnum):
    """Action a feasibility negotiation propose message performs.

    The action is not rendered into the message; it selects which conditional sections of the
    feasibility propose template are emitted. It belongs to the feasibility family only: no other
    propose content carries an action (verified against the Java sources — the enum javadoc says
    feasibility and its only usages are ``FeasibilityProposeContent.action`` and the feasibility
    extraction path).
    """

    #: Asks the counterpart to evaluate whether the request is feasible.
    REQUEST_FEASIBILITY_EVALUATION = "REQUEST_FEASIBILITY_EVALUATION"

    #: Reports that the request is infeasible and proposes an alternative.
    PROPOSE_ALTERNATIVE_ON_FAILURE = "PROPOSE_ALTERNATIVE_ON_FAILURE"


class NegotiationConclusion(StrEnum):
    """Terminal outcome of a negotiation as expressed in negotiation result sections.

    Only ``ACCEPT`` and ``REJECT`` are renderable conclusions of the typed negotiation templates;
    the ``ABORT`` outcome is rendered through the type-independent common abort template, whose
    fixed conclusion section carries no conclusion slot, so the typed generation methods reject it
    as a programming error (D2: all three Java values are kept; the rejection lives in the
    generator layer, not here).

    The enum value is the literal text filled into negotiation result slots, so
    ``NegotiationConclusion("Accept")`` parses a wire literal the way the Java extractor compares
    ``literal()``.
    """

    #: The negotiation parties reached an agreement.
    ACCEPT = "Accept"

    #: The negotiation parties did not reach an agreement.
    REJECT = "Reject"

    #: The negotiation was abandoned outside the template outcome model.
    ABORT = "Abort"

    @property
    def literal(self) -> str:
        """Literal text filled into negotiation result slots.

        Returns:
            literal conclusion text such as ``Accept``.
        """
        return self.value
