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

"""Tests for the input limit configuration (port of Java ``InputLimitConfig``)."""

from __future__ import annotations

import logging

import pytest

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATBusinessError, A2ATError
from a2a_t.core.errors.input_limit import (
    DEFAULT_MAX_TEXT_CHARS,
    INPUT_TEXT_MAX_CHARS_KEY,
    InputLimitConfig,
)

INPUT_LOGGER = "a2a_t.core.errors.input_limit"


@pytest.fixture
def limit() -> InputLimitConfig:
    return InputLimitConfig(8)


def test_default_limit_is_16384() -> None:
    assert DEFAULT_MAX_TEXT_CHARS == 16384
    assert InputLimitConfig().max_text_chars == 16384
    assert InputLimitConfig.from_map({}).max_text_chars == 16384
    assert InputLimitConfig.from_map(None).max_text_chars == 16384


@pytest.mark.parametrize("max_text_chars", [0, -1, -16384])
def test_non_positive_limits_are_programming_errors_outside_the_error_tree(max_text_chars: int) -> None:
    with pytest.raises(ValueError) as excinfo:
        InputLimitConfig(max_text_chars)
    assert str(excinfo.value) == f"max_text_chars must be positive: {max_text_chars}"
    assert not isinstance(excinfo.value, A2ATError)


@pytest.mark.parametrize("raw_value", [None, "", "   "])
def test_blank_values_keep_the_default(raw_value: str | None) -> None:
    values = {} if raw_value is None else {INPUT_TEXT_MAX_CHARS_KEY: raw_value}
    assert InputLimitConfig.from_map(values).max_text_chars == 16384


@pytest.mark.parametrize("raw_value", ["32", " 32 ", "\t16384\n", "+64"])
def test_numeric_values_override_the_default(raw_value: str) -> None:
    assert InputLimitConfig.from_map({INPUT_TEXT_MAX_CHARS_KEY: raw_value}).max_text_chars == int(raw_value.strip())


@pytest.mark.parametrize("raw_value", ["abc", "1.5", "0", "-5", "1e4", "16 384"])
def test_invalid_values_fall_back_to_the_default_with_a_warning(
    raw_value: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=INPUT_LOGGER):
        resolved = InputLimitConfig.from_map({INPUT_TEXT_MAX_CHARS_KEY: raw_value})
    assert resolved.max_text_chars == 16384
    assert INPUT_TEXT_MAX_CHARS_KEY in caplog.text
    assert raw_value.strip() in caplog.text


def test_from_env_reads_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(INPUT_TEXT_MAX_CHARS_KEY, "64")
    assert InputLimitConfig.from_env().max_text_chars == 64
    monkeypatch.delenv(INPUT_TEXT_MAX_CHARS_KEY, raising=False)
    assert InputLimitConfig.from_env().max_text_chars == 16384


@pytest.mark.parametrize(
    ("length", "too_long"),
    [(0, False), (1, False), (7, False), (8, False), (9, True), (100, True)],
)
def test_is_too_long_at_the_boundary(limit: InputLimitConfig, length: int, too_long: bool) -> None:
    assert limit.is_too_long("a" * length) is too_long


def test_none_is_never_too_long() -> None:
    assert InputLimitConfig(1).is_too_long(None) is False


def test_check_passes_at_the_boundary_and_fails_beyond(limit: InputLimitConfig) -> None:
    assert limit.check("a" * 8) is None
    with pytest.raises(A2ATBusinessError) as excinfo:
        limit.check("a" * 9)
    error = excinfo.value
    assert error.code is ErrorCatalog.INPUT_TEXT_TOO_LONG
    assert error.code_str == "input.text_too_long"
    assert error.facts == {"actual_length": "9", "max_chars": "8"}
    assert str(error) == "Input text length 9 exceeds the maximum of 8 (A2AT_INPUT_TEXT_MAX_CHARS)"
    assert isinstance(error, A2ATError)


def test_check_renders_the_requested_language() -> None:
    with pytest.raises(A2ATBusinessError) as excinfo:
        InputLimitConfig(8).check("a" * 9, "zh-CN")
    assert str(excinfo.value) == "输入文本长度 9 超过上限 8(A2AT_INPUT_TEXT_MAX_CHARS)"


def test_check_none_never_raises() -> None:
    assert InputLimitConfig(1).check(None) is None


def test_length_counts_unicode_code_points() -> None:
    limit = InputLimitConfig(2)
    assert limit.check("🎉" * 2) is None
    with pytest.raises(A2ATBusinessError) as excinfo:
        limit.check("🎉" * 3)
    assert excinfo.value.facts == {"actual_length": "3", "max_chars": "2"}


def test_violation_message_states_the_limit_and_the_config_key() -> None:
    assert InputLimitConfig(8).violation_message("a" * 9) == (
        "input text length 9 exceeds the configured maximum of 8 characters (A2AT_INPUT_TEXT_MAX_CHARS)"
    )


def test_too_long_facts_carry_actual_length_and_max_chars() -> None:
    assert InputLimitConfig(16).too_long_facts("a" * 20) == {"actual_length": "20", "max_chars": "16"}
    assert InputLimitConfig(16).too_long_facts(None) == {"actual_length": "0", "max_chars": "16"}
