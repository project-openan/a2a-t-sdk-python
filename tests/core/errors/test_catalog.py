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

"""Tests for the closed error catalog (port of Java ``ErrorCatalog``)."""

from __future__ import annotations

import re

import pytest

from a2a_t.core.errors.catalog import Category, ErrorCatalog, by_code

# The full 43-entry table ported verbatim from the Java source
# (a2a-t-core/.../core/exception/ErrorCatalog.java): (member name, code, category, fact parameters).
# This literal table is the factParameters parity oracle; the assertions below pin the Python
# catalog member-for-member against it.
JAVA_ERROR_CATALOG: tuple[tuple[str, str, Category, tuple[str, ...]], ...] = (
    # domain: template
    ("TEMPLATE_NOT_FOUND", "template.not_found", Category.BUSINESS, ("template_uri", "language")),
    ("TEMPLATE_RENDER_FAILED", "template.render_failed", Category.BUSINESS, ("template_uri", "reason")),
    ("TEMPLATE_LOAD_FAILED", "template.load_failed", Category.INFRA, ("resource_path",)),
    # domain: slot
    ("SLOT_SCHEMA_NOT_FOUND", "slot.schema_not_found", Category.BUSINESS, ("template_uri", "language")),
    ("SLOT_NOT_PROVIDED", "slot.not_provided", Category.BUSINESS, ("slot_label",)),
    ("SLOT_CONSTRAINT_VIOLATED", "slot.constraint_violated", Category.BUSINESS, ("slot_label", "actual")),
    ("SLOT_SEMANTIC_CONFLICT", "slot.semantic_conflict", Category.BUSINESS, ("slot_label", "reason")),
    ("SLOT_FABRICATED_VALUE", "slot.fabricated_value", Category.BUSINESS, ("slot_label", "actual")),
    ("SLOT_CROSS_SCENARIO_POLLUTION", "slot.cross_scenario_pollution", Category.BUSINESS, ("slot_label",)),
    ("SLOT_INSUFFICIENT_GROUNDING", "slot.insufficient_grounding", Category.BUSINESS, ("slot_label",)),
    ("SLOT_INTENT_UNCLEAR", "slot.intent_unclear", Category.BUSINESS, ("slot_label", "actual")),
    ("SLOT_RULE_VIOLATION", "slot.rule_violation", Category.BUSINESS, ("slot_label",)),
    # domain: input
    ("INPUT_TEXT_TOO_LONG", "input.text_too_long", Category.BUSINESS, ("actual_length", "max_chars")),
    # domain: content
    ("CONTENT_PARAM_MISSING", "content.param_missing", Category.BUSINESS, ("section_label",)),
    (
        "CONTENT_ENTRY_FIELD_MISSING",
        "content.entry_field_missing",
        Category.BUSINESS,
        ("section_label", "index", "field_label"),
    ),
    ("CONTENT_FORMAT_ERROR", "content.format_error", Category.BUSINESS, ("section_label", "reason")),
    ("CONTENT_VALUE_NOT_ALLOWED", "content.value_not_allowed", Category.BUSINESS, ("section_label", "actual")),
    ("CONTENT_SEMANTIC_CONFLICT", "content.semantic_conflict", Category.BUSINESS, ("section_label", "reason")),
    ("CONTENT_RULE_VIOLATION", "content.rule_violation", Category.BUSINESS, ("section_label",)),
    # domain: scenario
    ("SCENARIO_NOT_MATCHED", "scenario.not_matched", Category.BUSINESS, ("reason",)),
    # domain: llm
    ("LLM_NOT_CONFIGURED", "llm.not_configured", Category.BUSINESS, ()),
    ("LLM_INVOCATION_FAILED", "llm.invocation_failed", Category.BUSINESS, ("provider", "reason")),
    ("LLM_RESPONSE_INVALID", "llm.response_invalid", Category.BUSINESS, ("step",)),
    # domain: negotiation
    ("NEGOTIATION_INVALID_INPUT", "negotiation.invalid_input", Category.BUSINESS, ("reason",)),
    ("NEGOTIATION_INVALID_CONTEXT_ID", "negotiation.invalid_context_id", Category.BUSINESS, ("actual",)),
    ("NEGOTIATION_ROUND_EXCEEDED", "negotiation.round_exceeded", Category.BUSINESS, ("round", "max_rounds")),
    ("NEGOTIATION_TYPE_MISMATCH", "negotiation.type_mismatch", Category.BUSINESS, ("implied", "declared")),
    ("NEGOTIATION_PHASE_MISMATCH", "negotiation.phase_mismatch", Category.BUSINESS, ("implied", "declared")),
    (
        "NEGOTIATION_CONCLUSION_MISMATCH",
        "negotiation.conclusion_mismatch",
        Category.BUSINESS,
        ("expected", "actual"),
    ),
    ("NEGOTIATION_CONTENT_INVALID", "negotiation.content_invalid", Category.BUSINESS, ("field", "reason")),
    ("NEGOTIATION_FIELD_MISSING", "negotiation.field_missing", Category.BUSINESS, ("field",)),
    (
        "NEGOTIATION_CONTENT_EXTRACT_FAILED",
        "negotiation.content_extract_failed",
        Category.BUSINESS,
        ("field", "reason"),
    ),
    (
        "NEGOTIATION_CONCLUSION_CONTENT_MISMATCH",
        "negotiation.conclusion_content_mismatch",
        Category.BUSINESS,
        ("conclusion", "section_label"),
    ),
    (
        "NEGOTIATION_MISSING_RESULT_CONTENT",
        "negotiation.missing_result_content",
        Category.BUSINESS,
        ("section_label",),
    ),
    (
        "NEGOTIATION_MUTUALLY_EXCLUSIVE_SECTIONS",
        "negotiation.mutually_exclusive_sections",
        Category.BUSINESS,
        ("sections",),
    ),
    (
        "NEGOTIATION_CONSTRAINT_CONFLICT",
        "negotiation.constraint_conflict",
        Category.BUSINESS,
        ("section_label", "reason"),
    ),
    (
        "NEGOTIATION_FIELD_INCONSISTENCY",
        "negotiation.field_inconsistency",
        Category.BUSINESS,
        ("section_label", "reason"),
    ),
    (
        "NEGOTIATION_INVALID_TIME_INTERVAL",
        "negotiation.invalid_time_interval",
        Category.BUSINESS,
        ("section_label",),
    ),
    ("NEGOTIATION_SEMANTIC_REJECTED", "negotiation.semantic_rejected", Category.BUSINESS, ()),
    ("NEGOTIATION_RULE_VIOLATION", "negotiation.rule_violation", Category.BUSINESS, ("section_label",)),
    # domain: infra
    ("INFRA_CONFIG_INVALID", "infra.config_invalid", Category.INFRA, ("key", "reason")),
    ("INFRA_RESOURCE_READ_FAILED", "infra.resource_read_failed", Category.INFRA, ("resource_path",)),
    ("INFRA_INTERNAL_ERROR", "infra.internal_error", Category.INFRA, ()),
)


@pytest.mark.parametrize(("member_name", "code", "category", "fact_parameters"), JAVA_ERROR_CATALOG)
def test_entry_matches_the_java_error_catalog(
    member_name: str,
    code: str,
    category: Category,
    fact_parameters: tuple[str, ...],
) -> None:
    member = ErrorCatalog[member_name]
    assert member.value == code
    assert type(member.value) is str
    assert member.category is category
    assert member.fact_parameters == fact_parameters
    assert by_code(code) is member


def test_catalog_size_is_43() -> None:
    assert len(ErrorCatalog) == 43
    assert len(JAVA_ERROR_CATALOG) == 43
    assert len({entry[1] for entry in JAVA_ERROR_CATALOG}) == 43
    assert {member.name for member in ErrorCatalog} == {entry[0] for entry in JAVA_ERROR_CATALOG}


def test_category_split_matches_java() -> None:
    infra_members = {member.name for member in ErrorCatalog if member.category is Category.INFRA}
    assert infra_members == {
        "TEMPLATE_LOAD_FAILED",
        "INFRA_CONFIG_INVALID",
        "INFRA_RESOURCE_READ_FAILED",
        "INFRA_INTERNAL_ERROR",
    }


@pytest.mark.parametrize("member", list(ErrorCatalog))
def test_codes_are_layered_domain_semantic_tokens(member: ErrorCatalog) -> None:
    assert re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", member.value)


@pytest.mark.parametrize("member", list(ErrorCatalog))
def test_fact_parameter_names_are_snake_case_tokens(member: ErrorCatalog) -> None:
    for name in member.fact_parameters:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", name)


@pytest.mark.parametrize("member", list(ErrorCatalog))
def test_has_fact_parameter(member: ErrorCatalog) -> None:
    for name in member.fact_parameters:
        assert member.has_fact_parameter(name) is True
    assert member.has_fact_parameter("definitely_not_a_fact_parameter") is False
    assert member.has_fact_parameter(None) is False


@pytest.mark.parametrize("code", [entry[1] for entry in JAVA_ERROR_CATALOG])
def test_by_code_resolves_every_code(code: str) -> None:
    assert by_code(code).value == code


@pytest.mark.parametrize(
    "code",
    [
        "",
        "template",
        "template.not_found.extra",
        "TEMPLATE_NOT_FOUND",
        " template.not_found",
        "unknown.code",
    ],
)
def test_by_code_raises_key_error_for_unknown_codes(code: str) -> None:
    with pytest.raises(KeyError):
        by_code(code)


def test_members_compare_equal_to_their_code_strings() -> None:
    assert ErrorCatalog.TEMPLATE_NOT_FOUND == "template.not_found"
    assert ErrorCatalog("slot.not_provided") is ErrorCatalog.SLOT_NOT_PROVIDED
    assert ErrorCatalog(ErrorCatalog.INPUT_TEXT_TOO_LONG) is ErrorCatalog.INPUT_TEXT_TOO_LONG
    assert [member.value for member in ErrorCatalog][:3] == [
        "template.not_found",
        "template.render_failed",
        "template.load_failed",
    ]


def test_errors_package_reexports_the_catalog() -> None:
    from a2a_t.core import errors

    assert errors.ErrorCatalog is ErrorCatalog
    assert errors.by_code is by_code
    assert errors.Category is Category
