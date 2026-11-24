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

"""Tests of the negotiation extraction JSON Schema builder.

Port of Java ``NegotiationJsonSchemaBuilderTest``: every (negotiation type, performative) pair
builds a snake_case object schema whose property order, required set, enums and nullable types
match the Java schema shape snapshot-for-snapshot; accept and reject share the terminal schema;
and the programming-error guards (``None`` performative, ``None`` type on a typed performative,
type on the abort performative) fail as ``TypeError`` / ``ValueError`` the way the Java builder
fails as ``NullPointerException`` / ``IllegalArgumentException``.
"""

from __future__ import annotations

import pytest

from a2a_t.core.metadata import NegotiationPerformative
from a2a_t.negotiation.content.enums import NegotiationType
from a2a_t.negotiation.generation.json_schema_builder import build_extraction_schema


def test_information_propose_schema_uses_snake_case_properties_and_nullable_relationship() -> None:
    schema = build_extraction_schema(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)

    assert schema["type"] == "object"
    properties = schema["properties"]
    assert list(properties) == ["items", "relationship"]
    assert schema["required"] == ["items"]

    relationship = properties["relationship"]
    assert relationship["type"] == ["string", "null"]
    assert "enum" not in relationship

    items = properties["items"]
    assert items["type"] == "array"
    item = items["items"]
    assert list(item["properties"]) == ["name", "value"]
    assert item["required"] == ["name", "value"]


def test_information_ending_schema_requires_conclusion_and_items() -> None:
    schema = build_extraction_schema(NegotiationType.INFORMATION, NegotiationPerformative.ACCEPT)

    properties = schema["properties"]
    assert list(properties) == ["conclusion", "items"]
    assert schema["required"] == ["conclusion", "items"]
    assert properties["conclusion"]["enum"] == ["Accept", "Reject"]
    assert properties["items"]["minItems"] == 1


def test_accept_and_reject_share_the_terminal_schema() -> None:
    assert build_extraction_schema(NegotiationType.TARGET, NegotiationPerformative.ACCEPT) == build_extraction_schema(
        NegotiationType.TARGET, NegotiationPerformative.REJECT
    )


def test_target_propose_schema_uses_snake_case_properties() -> None:
    schema = build_extraction_schema(NegotiationType.TARGET, NegotiationPerformative.PROPOSE)

    properties = schema["properties"]
    assert list(properties) == [
        "target_negotiation_description",
        "intent_understanding",
        "alignment_and_clarification",
        "request_for_clarification",
        "target_confirm_request",
    ]
    assert schema["required"] == ["target_negotiation_description"]
    assert properties["intent_understanding"]["type"] == ["array", "null"]
    assert properties["target_confirm_request"]["type"] == ["string", "null"]


def test_target_ending_schema_carries_nullable_result_fields() -> None:
    schema = build_extraction_schema(NegotiationType.TARGET, NegotiationPerformative.REJECT)

    properties = schema["properties"]
    assert list(properties) == ["conclusion", "confirmed_intent", "failure_reason"]
    assert schema["required"] == ["conclusion"]
    assert properties["confirmed_intent"]["type"] == ["string", "null"]
    assert properties["failure_reason"]["type"] == ["string", "null"]


def test_feasibility_propose_schema_requires_the_action_enum() -> None:
    schema = build_extraction_schema(NegotiationType.FEASIBILITY, NegotiationPerformative.PROPOSE)

    properties = schema["properties"]
    assert list(properties) == [
        "feasibility_negotiation_description",
        "action",
        "contents_to_evaluate",
        "infeasibility_details_and_proposal",
        "feasibility_confirm_request",
    ]
    assert schema["required"] == ["feasibility_negotiation_description", "action"]
    action = properties["action"]
    assert action["enum"] == ["REQUEST_FEASIBILITY_EVALUATION", "PROPOSE_ALTERNATIVE_ON_FAILURE"]
    assert properties["feasibility_confirm_request"]["type"] == ["string", "null"]


def test_feasibility_ending_schema_requires_the_summary() -> None:
    schema = build_extraction_schema(NegotiationType.FEASIBILITY, NegotiationPerformative.ACCEPT)

    properties = schema["properties"]
    assert list(properties) == ["conclusion", "feasibility_summary"]
    assert schema["required"] == ["conclusion", "feasibility_summary"]


def test_abort_schema_requires_the_termination_reason() -> None:
    schema = build_extraction_schema(None, NegotiationPerformative.ABORT)

    assert schema["type"] == "object"
    assert list(schema["properties"]) == ["termination_reason"]
    assert schema["properties"]["termination_reason"] == {"type": "string"}
    assert schema["required"] == ["termination_reason"]


@pytest.mark.parametrize(
    ("negotiation_type", "performative"),
    [
        (NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE),
        (NegotiationType.TARGET, NegotiationPerformative.ACCEPT),
        (NegotiationType.FEASIBILITY, NegotiationPerformative.REJECT),
    ],
)
def test_every_typed_schema_is_an_object_schema_with_nested_item_objects(
    negotiation_type: NegotiationType, performative: NegotiationPerformative
) -> None:
    schema = build_extraction_schema(negotiation_type, performative)

    assert schema["type"] == "object"
    assert set(schema) == {"type", "properties", "required"}
    for slot_name, slot_schema in schema["properties"].items():
        assert isinstance(slot_schema, dict), slot_name
        if isinstance(slot_schema.get("type"), str) and slot_schema["type"] == "array":
            assert slot_schema["items"]["type"] == "object"


def test_rejects_none_type_and_performative() -> None:
    with pytest.raises(TypeError, match="Negotiation type must not be null for the PROPOSE phase."):
        build_extraction_schema(None, NegotiationPerformative.PROPOSE)
    with pytest.raises(TypeError, match="Negotiation phase must not be null."):
        build_extraction_schema(NegotiationType.INFORMATION, None)  # type: ignore[arg-type]


@pytest.mark.parametrize("negotiation_type", list(NegotiationType))
def test_rejects_a_type_on_the_abort_performative(negotiation_type: NegotiationType) -> None:
    with pytest.raises(
        ValueError,
        match="The ABORT phase is type-independent and must not carry a type but carried",
    ):
        build_extraction_schema(negotiation_type, NegotiationPerformative.ABORT)


def test_schemas_are_not_shared_between_calls() -> None:
    first = build_extraction_schema(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
    second = build_extraction_schema(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)

    assert first == second
    assert first is not second
    assert first["properties"] is not second["properties"]
    assert first["properties"]["items"] is not second["properties"]["items"]
    assert first["properties"]["relationship"] is not second["properties"]["relationship"]
