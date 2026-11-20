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

"""Tests for the A2AT error exception tree (port of the Java exception classes)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import (
    A2ATBusinessError,
    A2ATError,
    A2ATParamExtractionError,
    ConfigFileNotFoundError,
    ContentValidationError,
    NegotiationGenerationError,
    PromptGenerationError,
    ResourceNotFoundError,
    SlotValidationError,
)
from a2a_t.core.errors.messages import render

BUSINESS_CASES = [
    (ErrorCatalog.TEMPLATE_NOT_FOUND, {"template_uri": "Task-T/v1/energy-saving", "language": "fr-FR"}),
    (ErrorCatalog.INPUT_TEXT_TOO_LONG, {"actual_length": 9, "max_chars": 4}),
    (ErrorCatalog.NEGOTIATION_MUTUALLY_EXCLUSIVE_SECTIONS, {"sections": "rounds, deadline"}),
    (ErrorCatalog.SLOT_NOT_PROVIDED, {"slot_label": "round"}),
    (ErrorCatalog.INFRA_RESOURCE_READ_FAILED, {"resource_path": "templates/x.md"}),
]


@pytest.mark.parametrize(("code", "facts"), BUSINESS_CASES)
@pytest.mark.parametrize("language", [None, "zh-CN"])
def test_business_error_renders_its_message_from_the_code_template(
    code: ErrorCatalog,
    facts: dict[str, object],
    language: str | None,
) -> None:
    error = A2ATBusinessError(code, facts, language=language)
    assert str(error) == render(code, error.facts, language)


@pytest.mark.parametrize(("code", "facts"), BUSINESS_CASES)
def test_business_error_carries_the_code_and_normalized_facts(
    code: ErrorCatalog,
    facts: dict[str, object],
) -> None:
    error = A2ATBusinessError(code, facts)
    assert error.code is code
    assert error.code_str == code.value
    assert error.facts == {key: str(value) for key, value in facts.items()}
    assert all(isinstance(value, str) for value in error.facts.values())


def test_business_error_without_facts_renders_the_bare_template() -> None:
    error = A2ATBusinessError(ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED)
    assert error.facts == {}
    assert str(error) == "The negotiation message failed semantic validation"


def test_business_error_is_rooted_at_a2at_error() -> None:
    error = A2ATBusinessError(ErrorCatalog.SLOT_NOT_PROVIDED, {"slot_label": "x"})
    assert isinstance(error, A2ATError)
    assert isinstance(error, Exception)


@pytest.mark.parametrize(
    "error_type",
    [A2ATParamExtractionError, ContentValidationError, NegotiationGenerationError, PromptGenerationError],
)
def test_module_exceptions_extend_the_business_base(error_type: type[A2ATBusinessError]) -> None:
    error = error_type(ErrorCatalog.SLOT_NOT_PROVIDED, {"slot_label": "x"})
    assert isinstance(error, A2ATBusinessError)
    assert isinstance(error, A2ATError)
    assert error.code_str == "slot.not_provided"
    assert str(error) == "'x' is not provided in the input."


def test_root_error_defaults_to_the_internal_error_code() -> None:
    error = A2ATError("boom")
    assert error.code is ErrorCatalog.INFRA_INTERNAL_ERROR
    assert error.code_str == "infra.internal_error"
    assert str(error) == "boom"


def test_root_error_carries_a_specific_code() -> None:
    error = A2ATError("disk full", code=ErrorCatalog.INFRA_RESOURCE_READ_FAILED)
    assert error.code_str == "infra.resource_read_failed"
    assert str(error) == "disk full"


def test_cause_is_chained_and_suppresses_the_context() -> None:
    cause = ValueError("root cause")
    error = A2ATError("wrapped", cause=cause)
    assert error.__cause__ is cause
    assert error.__suppress_context__ is True


def test_business_error_chains_a_cause() -> None:
    cause = OSError("boom")
    error = A2ATBusinessError(ErrorCatalog.TEMPLATE_LOAD_FAILED, {"resource_path": "x.md"}, cause=cause)
    assert error.__cause__ is cause
    assert error.code_str == "template.load_failed"


def test_explicit_message_overrides_the_rendered_one() -> None:
    error = A2ATBusinessError(ErrorCatalog.SLOT_NOT_PROVIDED, {"slot_label": "x"}, message="custom failure")
    assert str(error) == "custom failure"
    assert error.facts == {"slot_label": "x"}
    assert error.code_str == "slot.not_provided"


def test_slot_validation_error_is_a_frozen_value_object() -> None:
    error = SlotValidationError("round", "slot.not_provided", "'round' is not provided in the input.")
    assert error.slot_name == "round"
    assert error.code == "slot.not_provided"
    assert error.message == "'round' is not provided in the input."
    assert error.facts is None
    assert error == SlotValidationError("round", "slot.not_provided", "'round' is not provided in the input.")
    with pytest.raises(FrozenInstanceError):
        error.slot_name = "deadline"  # type: ignore[misc]


def test_slot_validation_error_normalizes_a_catalog_member_code() -> None:
    error = SlotValidationError("round", ErrorCatalog.SLOT_NOT_PROVIDED, "message", {"slot_label": "round"})
    assert error.code == "slot.not_provided"
    assert type(error.code) is str
    assert error.facts == {"slot_label": "round"}


def test_prompt_generation_error_carries_failed_parameters() -> None:
    slot_error = SlotValidationError("round", "slot.not_provided", "'round' is not provided in the input.")
    error = PromptGenerationError(
        ErrorCatalog.SLOT_RULE_VIOLATION, {"slot_label": "round"}, failed_parameters=[slot_error]
    )
    assert error.failed_parameters == [slot_error]
    assert PromptGenerationError(ErrorCatalog.SLOT_RULE_VIOLATION).failed_parameters == []


def test_content_validation_error_carries_errors_and_params() -> None:
    slot_error = SlotValidationError("deadline", "content.format_error", "bad format")
    error = ContentValidationError(
        ErrorCatalog.CONTENT_FORMAT_ERROR,
        {"section_label": "deadline", "reason": "not a date"},
        errors=[slot_error],
        params={"deadline": None, "round": 2},
    )
    assert error.errors == [slot_error]
    assert error.params == {"deadline": None, "round": 2}
    assert list(error.params) == ["deadline", "round"]
    assert ContentValidationError(ErrorCatalog.CONTENT_RULE_VIOLATION).errors == []
    assert ContentValidationError(ErrorCatalog.CONTENT_RULE_VIOLATION).params == {}


def test_param_extraction_error_defaults_to_slot_not_provided() -> None:
    error = A2ATParamExtractionError()
    assert error.code is ErrorCatalog.SLOT_NOT_PROVIDED
    assert error.code_str == "slot.not_provided"
    assert error.errors == []
    assert error.facts == {}


def test_param_extraction_error_carries_slot_errors() -> None:
    slot_error = SlotValidationError("round", "slot.not_provided", "missing", {"slot_label": "round"})
    error = A2ATParamExtractionError(ErrorCatalog.SLOT_RULE_VIOLATION, {"slot_label": "round"}, errors=[slot_error])
    assert error.errors == [slot_error]
    assert error.code_str == "slot.rule_violation"


def test_resource_not_found_error_carries_the_resource_path() -> None:
    error = ResourceNotFoundError("template resource missing", "templates/Task-T/v1/x/en-US/template.md")
    assert error.resource_path == "templates/Task-T/v1/x/en-US/template.md"
    assert error.code_str == "infra.internal_error"
    assert isinstance(error, A2ATError)


def test_resource_not_found_error_accepts_a_specific_code() -> None:
    error = ResourceNotFoundError(
        "missing",
        "errors/xx-XX/errors.json",
        code=ErrorCatalog.INFRA_RESOURCE_READ_FAILED,
    )
    assert error.code_str == "infra.resource_read_failed"


def test_config_file_not_found_error_renders_the_config_invalid_template() -> None:
    error = ConfigFileNotFoundError(Path(".env"))
    assert error.code_str == "infra.config_invalid"
    assert error.path == Path(".env")
    assert str(error) == "Invalid configuration '.env': config file does not exist"


def test_errors_package_reexports_the_exception_tree() -> None:
    from a2a_t.core import errors

    assert errors.A2ATError is A2ATError
    assert errors.A2ATBusinessError is A2ATBusinessError
    assert errors.SlotValidationError is SlotValidationError
