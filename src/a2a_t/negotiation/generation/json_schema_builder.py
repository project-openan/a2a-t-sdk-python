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

"""JSON Schemas of the negotiation LLM steps (port of Java ``NegotiationJsonSchemaBuilder``).

Extraction schemas are keyed by the snake_case property names of the extracted content, one schema
per (negotiation type, performative) pair with accept and reject sharing the terminal schema. The
performative keeps the Java builder's ``phase`` wording in its failure messages but is named after
the speech-act concept everywhere else (port plan 7.5).

The builder is a stateless module-level function following the sibling
:func:`a2a_t.negotiation.generation.message_builder.build_messages` convention; every call returns
freshly built mappings, so callers may embed the schema into payloads without sharing state.

The semantic-validation schema that merges a caller-provided parameter schema into the fixed
four-key output contract is NOT part of this port: Java keeps it in
``DefaultNegotiationSemanticValidator`` (P6 validation pipeline).
"""

from __future__ import annotations

from typing import Final

from a2a_t.core.metadata import NegotiationPerformative

from ..content.enums import NegotiationType

__all__ = ["build_extraction_schema"]

#: Wire literals of the terminal conclusion slot (Java ``CONCLUSION_ENUM``).
_CONCLUSION_ENUM: Final[tuple[str, ...]] = ("Accept", "Reject")

#: Wire literals of the feasibility propose action slot (Java ``ACTION_ENUM``).
_ACTION_ENUM: Final[tuple[str, ...]] = ("REQUEST_FEASIBILITY_EVALUATION", "PROPOSE_ALTERNATIVE_ON_FAILURE")


def build_extraction_schema(
    negotiation_type: NegotiationType | None,
    performative: NegotiationPerformative,
) -> dict[str, object]:
    """Build the content extraction schema of one (negotiation type, performative) pair.

    Args:
        negotiation_type: negotiation type whose content is extracted; ``None`` only for the
            type-independent abort performative.
        performative: performative of the extraction; accept and reject share the terminal schema.

    Returns:
        JSON Schema describing the snake_case extraction output of the pair.

    Raises:
        TypeError: when the performative is ``None``, or the type is ``None`` on a typed
            performative (Java ``NullPointerException`` parity).
        ValueError: when the abort performative carries a type (Java ``IllegalArgumentException``
            parity).
    """
    if performative is None:
        raise TypeError("Negotiation phase must not be null.")
    if performative is NegotiationPerformative.ABORT:
        if negotiation_type is not None:
            raise ValueError(
                f"The ABORT phase is type-independent and must not carry a type but carried {negotiation_type}."
            )
        return _abort_schema()
    if negotiation_type is None:
        raise TypeError(f"Negotiation type must not be null for the {performative} phase.")
    is_propose = performative is NegotiationPerformative.PROPOSE
    if negotiation_type is NegotiationType.INFORMATION:
        return _information_propose_schema() if is_propose else _information_ending_schema()
    if negotiation_type is NegotiationType.TARGET:
        return _target_propose_schema() if is_propose else _target_ending_schema()
    return _feasibility_propose_schema() if is_propose else _feasibility_ending_schema()


def _abort_schema() -> dict[str, object]:
    """Schema of the type-independent abort extraction output."""
    properties: dict[str, object] = {"termination_reason": {"type": "string"}}
    return _object_schema(properties, ["termination_reason"])


def _information_propose_schema() -> dict[str, object]:
    """Schema of the information negotiation propose extraction output."""
    properties: dict[str, object] = {
        "items": _non_empty_item_array_schema(),
        "relationship": _nullable_string_schema(),
    }
    return _object_schema(properties, ["items"])


def _information_ending_schema() -> dict[str, object]:
    """Schema of the information negotiation terminal extraction output."""
    properties: dict[str, object] = {
        "conclusion": _conclusion_schema(),
        "items": _non_empty_item_array_schema(),
    }
    return _object_schema(properties, ["conclusion", "items"])


def _target_propose_schema() -> dict[str, object]:
    """Schema of the target negotiation propose extraction output."""
    properties: dict[str, object] = {
        "target_negotiation_description": {"type": "string"},
        "intent_understanding": _nullable_item_array_schema(),
        "alignment_and_clarification": _nullable_item_array_schema(),
        "request_for_clarification": _nullable_item_array_schema(),
        "target_confirm_request": _nullable_string_schema(),
    }
    return _object_schema(properties, ["target_negotiation_description"])


def _target_ending_schema() -> dict[str, object]:
    """Schema of the target negotiation terminal extraction output."""
    properties: dict[str, object] = {
        "conclusion": _conclusion_schema(),
        "confirmed_intent": _nullable_string_schema(),
        "failure_reason": _nullable_string_schema(),
    }
    return _object_schema(properties, ["conclusion"])


def _feasibility_propose_schema() -> dict[str, object]:
    """Schema of the feasibility negotiation propose extraction output."""
    properties: dict[str, object] = {
        "feasibility_negotiation_description": {"type": "string"},
        "action": {"type": "string", "enum": list(_ACTION_ENUM)},
        "contents_to_evaluate": _nullable_item_array_schema(),
        "infeasibility_details_and_proposal": _nullable_item_array_schema(),
        "feasibility_confirm_request": _nullable_string_schema(),
    }
    return _object_schema(properties, ["feasibility_negotiation_description", "action"])


def _feasibility_ending_schema() -> dict[str, object]:
    """Schema of the feasibility negotiation terminal extraction output."""
    properties: dict[str, object] = {
        "conclusion": _conclusion_schema(),
        "feasibility_summary": {"type": "string"},
    }
    return _object_schema(properties, ["conclusion", "feasibility_summary"])


def _item_array_schema() -> dict[str, object]:
    """Schema of a mandatory item array slot."""
    return {"type": "array", "items": _item_schema()}


def _non_empty_item_array_schema() -> dict[str, object]:
    """Schema of a mandatory non-empty item array slot."""
    schema = _item_array_schema()
    schema["minItems"] = 1
    return schema


def _nullable_item_array_schema() -> dict[str, object]:
    """Schema of an optional item array slot."""
    return {"type": ["array", "null"], "items": _item_schema()}


def _item_schema() -> dict[str, object]:
    """Schema of one negotiation item entry."""
    item_properties: dict[str, object] = {
        "name": {"type": "string"},
        "value": _nullable_string_schema(),
    }
    return _object_schema(item_properties, ["name", "value"])


def _conclusion_schema() -> dict[str, object]:
    """Schema of the terminal conclusion slot."""
    return {"type": "string", "enum": list(_CONCLUSION_ENUM)}


def _nullable_string_schema() -> dict[str, object]:
    """Schema of an optional string slot."""
    return {"type": ["string", "null"]}


def _object_schema(properties: dict[str, object], required: list[str]) -> dict[str, object]:
    """Assemble one object schema from its properties and required property names."""
    return {"type": "object", "properties": properties, "required": required}
