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

"""Happy-path property layer (port of Java ``FromDataRoundTripPropertyTest``, design §8.3).

Round-trip over arbitrary legal typed content: every property generates a negotiation message from
typed data with the production wiring, feeds the rendered text into the matching validate API with
an accepting scripted semantic verdict, and asserts that the merged parameters carry the three
context keys with the original values surviving untouched — the context keys of the A2A-T
negotiation metadata are never lost or rewritten by a generation/validation round trip.
"""

from __future__ import annotations

from hypothesis import given

from a2a_t.core.metadata import MetadataContent, NegotiationContext, NegotiationPerformative
from a2a_t.core.template_uri import TemplateUri
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.negotiation.content.models import (
    FeasibilityProposeContent,
    InformationProposeContent,
    NegotiationAbortContent,
    NegotiationAbortData,
    NegotiationProposeData,
    TargetProposeContent,
)
from tests.corpus.property.arbitraries import (
    abort_contents,
    contexts,
    feasibility_propose_contents,
    information_propose_contents,
    languages,
    target_propose_contents,
)
from tests.corpus.property.harness import (
    object_schema,
    scripted,
    semantic_verdict,
    service,
    template_uri,
)

INFORMATION_PROPOSE_URI = "Negotiation-T/information-negotiation/propose/v1"
TARGET_PROPOSE_URI = "Negotiation-T/target-negotiation/propose/v1"
FEASIBILITY_PROPOSE_URI = "Negotiation-T/feasibility-negotiation/propose/v1"
COMMON_ABORT_URI = "Negotiation-T/common/abort/v1"

FLAT_SCHEMA: dict[str, object] = object_schema({})


@given(language=languages(), context=contexts(), content=information_propose_contents())
def test_information_propose_round_trips_through_validation(
    language: str,
    context: NegotiationContext,
    content: InformationProposeContent,
) -> None:
    """An information propose message survives its own validation with the context intact."""
    _round_trip(language, context, NegotiationProposeData(context, content), INFORMATION_PROPOSE_URI, "information")


@given(language=languages(), context=contexts(), content=target_propose_contents())
def test_target_propose_round_trips_through_validation(
    language: str,
    context: NegotiationContext,
    content: TargetProposeContent,
) -> None:
    """A target propose message survives its own validation with the context intact."""
    _round_trip(language, context, NegotiationProposeData(context, content), TARGET_PROPOSE_URI, "target")


@given(language=languages(), context=contexts(), content=feasibility_propose_contents())
def test_feasibility_propose_round_trips_through_validation(
    language: str,
    context: NegotiationContext,
    content: FeasibilityProposeContent,
) -> None:
    """A feasibility propose message survives its own validation with the context intact."""
    _round_trip(language, context, NegotiationProposeData(context, content), FEASIBILITY_PROPOSE_URI, "feasibility")


@given(language=languages(), context=contexts(), content=abort_contents())
def test_abort_round_trips_through_validation(
    language: str,
    context: NegotiationContext,
    content: NegotiationAbortContent,
) -> None:
    """An abort message survives its own validation with the context intact."""
    llm = scripted(semantic_verdict(None, {}))
    wired = service(language, llm)
    uri = template_uri(COMMON_ABORT_URI)
    message = wired.generate_abort_from_data(NegotiationAbortData(context, content), uri)
    _assert_message_invariants(message, uri, context, NegotiationPerformative.ABORT)
    filled = wired.validate_abort_prompt_and_data_filling(message.prompt_text, context, FLAT_SCHEMA, uri)
    _assert_context_survives(filled, context)
    assert llm.call_count == 1


def _round_trip(
    language: str,
    context: NegotiationContext,
    data: NegotiationProposeData,
    raw_uri: str,
    negotiation_type: str,
) -> None:
    """Generate one propose message from typed data and validate it with an accepting verdict.

    Args:
        language: message language of the run.
        context: negotiation context travelling with the message.
        data: typed propose input.
        raw_uri: raw template URI of the propose template.
        negotiation_type: negotiation type name the scripted verdict reports.
    """
    llm = scripted(semantic_verdict(negotiation_type, {}))
    wired = service(language, llm)
    uri = template_uri(raw_uri)
    message = wired.generate_propose_from_data(data, uri)
    _assert_message_invariants(message, uri, context, NegotiationPerformative.PROPOSE)
    filled = wired.validate_propose_prompt_and_data_filling(message.prompt_text, context, FLAT_SCHEMA, uri)
    _assert_context_survives(filled, context)
    assert llm.call_count == 1


def _assert_message_invariants(
    message: MetadataContent,
    uri: TemplateUri,
    context: NegotiationContext,
    performative: NegotiationPerformative,
) -> None:
    """Assert the three shape invariants of one generated message.

    Args:
        message: the generated message.
        uri: template URI the message was generated from.
        context: negotiation context the generation started from.
        performative: performative the pipeline stamps onto the emitted context.
    """
    assert message.template_uri == uri.uri
    assert message.negotiation_context == context.with_performative(performative)
    assert message.prompt_text is not None and message.prompt_text != ""
    assert "{{" not in (message.prompt_text or ""), "the rendered text must not leak unfilled slots"


def _assert_context_survives(filled: FilledParamData, context: NegotiationContext) -> None:
    """Assert the three context keys survive the merge with their original values.

    Args:
        filled: the validate leg's merged parameter data.
        context: negotiation context the validation started from.
    """
    assert filled.data.get("id") == context.id
    assert filled.data.get("round") == context.round
    assert filled.data.get("maxRounds") == context.max_rounds
