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

"""Closed catalog of the machine-readable error codes exposed by the A2A-T SDK.

Port of Java ``a2a-t-core`` ``net.openan.a2at.sdk.core.exception.ErrorCatalog`` (1.1.0). Each member
carries the layered ``domain.semantic`` code string (for example ``content.param_missing``), the
:class:`Category` that decides which exception family carries it, and the names of the fact
parameters the message template of the code renders. Message templates live in
``prompt_resources/errors/{language}/errors.json``; use :mod:`a2a_t.core.errors.messages` to render
them.

The catalog is closed: codes returned by an LLM step that are not in this catalog are mapped to the
per-domain ``*.rule_violation`` fallback members by the callers, never surfaced raw (D4: the enum is
the internal carrier, ``str`` is the external one — use ``ErrorCatalog.value`` / ``code_str``).
"""

from __future__ import annotations

from enum import Enum
from typing import Self, cast


class Category(Enum):
    """Category deciding which exception family carries the code."""

    BUSINESS = "business"
    INFRA = "infra"


class ErrorCatalog(str, Enum):
    """Closed set of the 42 layered error codes, each with its category and fact parameters.

    Every member is its code string (``str``-derived, so a member serializes and compares as its
    plain string form) and additionally carries the two elements of the code's contract:

    Attributes:
        category: :class:`Category` deciding which exception family carries the code — business
            failures travel on :class:`~a2a_t.core.errors.exceptions.A2ATBusinessError` subclasses,
            infrastructure failures on plain
            :class:`~a2a_t.core.errors.exceptions.A2ATError`.
        fact_parameters: names of the fact parameters the code's message template renders, for
            example ``("template_uri", "language")``; callers pass the values keyed by these names
            when raising, and the message renderer fills the ``{name}`` placeholders with them.
    """

    category: Category
    fact_parameters: tuple[str, ...]

    def __new__(cls, code: str, category: Category, fact_parameters: tuple[str, ...]) -> Self:
        """Create one catalog member from its code string, category and fact parameter names."""
        member = str.__new__(cls, code)
        member._value_ = code
        member.category = category
        member.fact_parameters = fact_parameters
        return member

    def has_fact_parameter(self, name: str | None) -> bool:
        """Return whether one name is a declared fact parameter of the code.

        Args:
            name: candidate fact parameter name; may be ``None``.

        Returns:
            ``True`` when the name is one of this code's declared fact parameters.
        """
        return name is not None and name in self.fact_parameters

    # domain: template (template resources)

    #: The referenced template does not exist for the configured language.
    TEMPLATE_NOT_FOUND = ("template.not_found", Category.BUSINESS, ("template_uri", "language"))
    #: Rendering a template with its slot values failed.
    TEMPLATE_RENDER_FAILED = ("template.render_failed", Category.BUSINESS, ("template_uri", "reason"))
    #: A template resource could not be loaded from its configured source.
    TEMPLATE_LOAD_FAILED = ("template.load_failed", Category.INFRA, ("resource_path",))

    # domain: slot (generation-side slots and generic schemas)

    #: The slot schema describing the expected slots could not be resolved for the template.
    SLOT_SCHEMA_NOT_FOUND = ("slot.schema_not_found", Category.BUSINESS, ("template_uri", "language"))
    #: A required slot could not be extracted from the caller input.
    SLOT_NOT_PROVIDED = ("slot.not_provided", Category.BUSINESS, ("slot_label",))
    #: An extracted slot value violates a closed value range or structural constraint.
    SLOT_CONSTRAINT_VIOLATED = ("slot.constraint_violated", Category.BUSINESS, ("slot_label", "actual"))
    #: A slot value conflicts with the slot definition.
    SLOT_SEMANTIC_CONFLICT = ("slot.semantic_conflict", Category.BUSINESS, ("slot_label", "reason"))
    #: A slot value is obvious placeholder content rather than a valid value.
    SLOT_FABRICATED_VALUE = ("slot.fabricated_value", Category.BUSINESS, ("slot_label", "actual"))
    #: A slot value mixes in content from a different scenario.
    SLOT_CROSS_SCENARIO_POLLUTION = ("slot.cross_scenario_pollution", Category.BUSINESS, ("slot_label",))
    #: A slot value lacks sufficient grounding in the input.
    SLOT_INSUFFICIENT_GROUNDING = ("slot.insufficient_grounding", Category.BUSINESS, ("slot_label",))
    #: A slot value expresses an unclear intent that cannot be resolved into a concrete value.
    SLOT_INTENT_UNCLEAR = ("slot.intent_unclear", Category.BUSINESS, ("slot_label", "actual"))
    #: Fallback for unknown slot-domain codes returned by an LLM step.
    SLOT_RULE_VIOLATION = ("slot.rule_violation", Category.BUSINESS, ("slot_label",))

    # domain: input (caller input)

    #: A free-text input exceeded the configured maximum length.
    INPUT_TEXT_TOO_LONG = ("input.text_too_long", Category.BUSINESS, ("actual_length", "max_chars"))

    # domain: content (validation-side parameter content, LLM-judged)

    #: A parameter section carries no value at all.
    CONTENT_PARAM_MISSING = ("content.param_missing", Category.BUSINESS, ("section_label",))
    #: A list entry is missing a required field.
    CONTENT_ENTRY_FIELD_MISSING = (
        "content.entry_field_missing",
        Category.BUSINESS,
        ("section_label", "index", "field_label"),
    )
    #: A value exists but its format is invalid.
    CONTENT_FORMAT_ERROR = ("content.format_error", Category.BUSINESS, ("section_label", "reason"))
    #: A value exists and is well-formed but is not within the allowed range.
    CONTENT_VALUE_NOT_ALLOWED = ("content.value_not_allowed", Category.BUSINESS, ("section_label", "actual"))
    #: Values conflict semantically.
    CONTENT_SEMANTIC_CONFLICT = ("content.semantic_conflict", Category.BUSINESS, ("section_label", "reason"))
    #: Fallback for unknown content-domain codes returned by an LLM step.
    CONTENT_RULE_VIOLATION = ("content.rule_violation", Category.BUSINESS, ("section_label",))

    # domain: scenario (scenario recognition)

    #: The input does not match any known scenario.
    SCENARIO_NOT_MATCHED = ("scenario.not_matched", Category.BUSINESS, ("reason",))

    # domain: llm (LLM invocations, retryable by the caller)

    #: No LLM client is configured.
    LLM_NOT_CONFIGURED = ("llm.not_configured", Category.BUSINESS, ())
    #: An LLM invocation failed at the transport level.
    LLM_INVOCATION_FAILED = ("llm.invocation_failed", Category.BUSINESS, ("provider", "reason"))
    #: An LLM response violates the response contract of the step.
    LLM_RESPONSE_INVALID = ("llm.response_invalid", Category.BUSINESS, ("step",))

    # domain: negotiation (negotiation flows)

    #: The negotiation input is not valid for the requested operation.
    NEGOTIATION_INVALID_INPUT = ("negotiation.invalid_input", Category.BUSINESS, ("reason",))
    #: The negotiation context id is not a valid UUID.
    NEGOTIATION_INVALID_CONTEXT_ID = ("negotiation.invalid_context_id", Category.BUSINESS, ("actual",))
    #: The negotiation round exceeds the configured maximum.
    NEGOTIATION_ROUND_EXCEEDED = ("negotiation.round_exceeded", Category.BUSINESS, ("round", "max_rounds"))
    #: The message content implies a different negotiation type than the declared template.
    NEGOTIATION_TYPE_MISMATCH = ("negotiation.type_mismatch", Category.BUSINESS, ("implied", "declared"))
    #: The message phase does not match the declared template phase.
    NEGOTIATION_PHASE_MISMATCH = ("negotiation.phase_mismatch", Category.BUSINESS, ("implied", "declared"))
    #: The message conclusion does not match the conclusion expected by the called method.
    NEGOTIATION_CONCLUSION_MISMATCH = (
        "negotiation.conclusion_mismatch",
        Category.BUSINESS,
        ("expected", "actual"),
    )
    #: A negotiation content field passed to a from-data method is invalid.
    NEGOTIATION_CONTENT_INVALID = ("negotiation.content_invalid", Category.BUSINESS, ("field", "reason"))
    #: The negotiation message is missing a required field.
    NEGOTIATION_FIELD_MISSING = ("negotiation.field_missing", Category.BUSINESS, ("field",))
    #: Negotiation content could not be extracted from free text.
    NEGOTIATION_CONTENT_EXTRACT_FAILED = (
        "negotiation.content_extract_failed",
        Category.BUSINESS,
        ("field", "reason"),
    )
    #: The conclusion does not match the content required by the result section.
    NEGOTIATION_CONCLUSION_CONTENT_MISMATCH = (
        "negotiation.conclusion_content_mismatch",
        Category.BUSINESS,
        ("conclusion", "section_label"),
    )
    #: The result section is missing the content required by its conclusion.
    NEGOTIATION_MISSING_RESULT_CONTENT = (
        "negotiation.missing_result_content",
        Category.BUSINESS,
        ("section_label",),
    )
    #: Mutually exclusive sections appear together.
    NEGOTIATION_MUTUALLY_EXCLUSIVE_SECTIONS = (
        "negotiation.mutually_exclusive_sections",
        Category.BUSINESS,
        ("sections",),
    )
    #: A section conflicts with existing constraints.
    NEGOTIATION_CONSTRAINT_CONFLICT = (
        "negotiation.constraint_conflict",
        Category.BUSINESS,
        ("section_label", "reason"),
    )
    #: Fields within one section are inconsistent.
    NEGOTIATION_FIELD_INCONSISTENCY = (
        "negotiation.field_inconsistency",
        Category.BUSINESS,
        ("section_label", "reason"),
    )
    #: A time interval is invalid, for example start later than end.
    NEGOTIATION_INVALID_TIME_INTERVAL = (
        "negotiation.invalid_time_interval",
        Category.BUSINESS,
        ("section_label",),
    )
    #: The negotiation message failed semantic validation.
    NEGOTIATION_SEMANTIC_REJECTED = ("negotiation.semantic_rejected", Category.BUSINESS, ())
    #: Fallback for unknown negotiation-domain codes returned by an LLM step.
    NEGOTIATION_RULE_VIOLATION = ("negotiation.rule_violation", Category.BUSINESS, ("section_label",))

    # domain: infra (infrastructure)

    #: A configuration entry is invalid.
    INFRA_CONFIG_INVALID = ("infra.config_invalid", Category.INFRA, ("key", "reason"))
    #: A resource could not be read.
    INFRA_RESOURCE_READ_FAILED = ("infra.resource_read_failed", Category.INFRA, ("resource_path",))
    #: Default code for SDK internal errors.
    INFRA_INTERNAL_ERROR = ("infra.internal_error", Category.INFRA, ())


def by_code(code: str) -> ErrorCatalog:
    """Return the catalog entry for one layered code string.

    Args:
        code: layered error code, for example ``content.param_missing``

    Returns:
        the catalog entry carrying the code

    Raises:
        KeyError: when the code is not in the closed catalog
    """
    try:
        return cast(ErrorCatalog, ErrorCatalog._value2member_map_[code])
    except KeyError:
        raise KeyError(code) from None
