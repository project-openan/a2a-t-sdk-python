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

"""Determinism property layer (port of Java ``FromDataDeterminismPropertyTest``, design §8.3).

The same from-data input produces fully equal output on repeated runs. Each property runs the same
typed input through two independently assembled services — proving there is no hidden per-instance
state — and asserts record equality of the two :class:`~a2a_t.core.metadata.MetadataContent`
results. The assertion-only client proves the runs never touch the LLM (the from-data family is
pure deterministic rendering).
"""

from __future__ import annotations

from hypothesis import given

from a2a_t.core.metadata import MetadataContent, NegotiationContext
from a2a_t.negotiation.content.enums import NegotiationConclusion
from a2a_t.negotiation.content.models import (
    InformationEndingContent,
    InformationProposeContent,
    NegotiationAbortContent,
    NegotiationAbortData,
    NegotiationEndingContent,
    NegotiationEndingData,
    NegotiationProposeContent,
    NegotiationProposeData,
    TargetEndingContent,
    TargetProposeContent,
)
from a2a_t.negotiation.generation import NegotiationContentService
from tests.corpus.llm_stub import ScriptedNegotiationLlmClient
from tests.corpus.property.arbitraries import (
    abort_contents,
    contexts,
    ending_contents,
    languages,
    propose_contents,
)
from tests.corpus.property.harness import service, template_uri

INFORMATION_PROPOSE_URI = "Negotiation-T/information-negotiation/propose/v1"
TARGET_PROPOSE_URI = "Negotiation-T/target-negotiation/propose/v1"
FEASIBILITY_PROPOSE_URI = "Negotiation-T/feasibility-negotiation/propose/v1"
INFORMATION_ACCEPT_REJECT_URI = "Negotiation-T/information-negotiation/accept-reject/v1"
TARGET_ACCEPT_REJECT_URI = "Negotiation-T/target-negotiation/accept-reject/v1"
FEASIBILITY_ACCEPT_REJECT_URI = "Negotiation-T/feasibility-negotiation/accept-reject/v1"
COMMON_ABORT_URI = "Negotiation-T/common/abort/v1"


def _service(language: str) -> NegotiationContentService:
    """Assemble one fresh service whose assertion-only client doubles as the zero-call proof."""
    return service(language, ScriptedNegotiationLlmClient.assertion_only())


@given(language=languages(), context=contexts(), content=propose_contents())
def test_propose_generation_is_deterministic(
    language: str,
    context: NegotiationContext,
    content: NegotiationProposeContent,
) -> None:
    """The same propose input renders byte-identically through two independent services."""
    data = NegotiationProposeData(context, content)
    uri = template_uri(_propose_uri_of(content))
    first: MetadataContent = _service(language).generate_propose_from_data(data, uri)
    second: MetadataContent = _service(language).generate_propose_from_data(data, uri)
    assert first == second


@given(language=languages(), context=contexts(), content=ending_contents(NegotiationConclusion.ACCEPT))
def test_accept_generation_is_deterministic(
    language: str,
    context: NegotiationContext,
    content: NegotiationEndingContent,
) -> None:
    """The same accept input renders byte-identically through two independent services."""
    data = NegotiationEndingData(context, content)
    uri = template_uri(_accept_reject_uri_of(content))
    first: MetadataContent = _service(language).generate_accept_from_data(data, uri)
    second: MetadataContent = _service(language).generate_accept_from_data(data, uri)
    assert first == second


@given(language=languages(), context=contexts(), content=ending_contents(NegotiationConclusion.REJECT))
def test_reject_generation_is_deterministic(
    language: str,
    context: NegotiationContext,
    content: NegotiationEndingContent,
) -> None:
    """The same reject input renders byte-identically through two independent services."""
    data = NegotiationEndingData(context, content)
    uri = template_uri(_accept_reject_uri_of(content))
    first: MetadataContent = _service(language).generate_reject_from_data(data, uri)
    second: MetadataContent = _service(language).generate_reject_from_data(data, uri)
    assert first == second


@given(language=languages(), context=contexts(), content=abort_contents())
def test_abort_generation_is_deterministic(
    language: str,
    context: NegotiationContext,
    content: NegotiationAbortContent,
) -> None:
    """The same abort input renders byte-identically through two independent services."""
    data = NegotiationAbortData(context, content)
    uri = template_uri(COMMON_ABORT_URI)
    first: MetadataContent = _service(language).generate_abort_from_data(data, uri)
    second: MetadataContent = _service(language).generate_abort_from_data(data, uri)
    assert first == second


def _propose_uri_of(content: NegotiationProposeContent) -> str:
    """Resolve the propose URI of one propose content variant."""
    if isinstance(content, InformationProposeContent):
        return INFORMATION_PROPOSE_URI
    if isinstance(content, TargetProposeContent):
        return TARGET_PROPOSE_URI
    return FEASIBILITY_PROPOSE_URI


def _accept_reject_uri_of(content: NegotiationEndingContent) -> str:
    """Resolve the accept-reject URI of one ending content variant."""
    if isinstance(content, InformationEndingContent):
        return INFORMATION_ACCEPT_REJECT_URI
    if isinstance(content, TargetEndingContent):
        return TARGET_ACCEPT_REJECT_URI
    return FEASIBILITY_ACCEPT_REJECT_URI
