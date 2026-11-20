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

"""Tests for the path segment guards (port of Java ``PathSegments``)."""

from __future__ import annotations

import pytest

from a2a_t.core.path_segments import (
    is_simple_segment,
    require_simple_relative_path,
    require_simple_segment,
)

SIMPLE_SEGMENTS = [
    "a",
    "Task-T",
    "network-layer",
    "ran-energy-saving",
    "v1",
    "en-US",
    "a.b",
    "a b",
    ".",
    "slot_extraction",
]

NON_SIMPLE_SEGMENTS = [
    None,
    "",
    "   ",
    "\t",
    "\n",
    "a/b",
    "a\\b",
    "..",
    "a..b",
    "../x",
    "x/..",
    "a/../b",
    "..\\x",
]

SIMPLE_RELATIVE_PATHS = [
    "network-layer",
    "network-layer/ran-energy-saving",
    "Task-T/network-layer/ran-energy-saving/v1",
    "a/b/c/d/e",
]

NON_SIMPLE_RELATIVE_PATHS = [
    None,
    "",
    "   ",
    "a//b",
    "/a",
    "a/",
    "a/../b",
    "../a",
    "a/..",
    "a\\b",
    "a/b..c/d",
]


@pytest.mark.parametrize("value", SIMPLE_SEGMENTS)
def test_is_simple_segment_accepts_simple_values(value: str) -> None:
    assert is_simple_segment(value) is True


@pytest.mark.parametrize("value", NON_SIMPLE_SEGMENTS)
def test_is_simple_segment_rejects_non_simple_values(value: str | None) -> None:
    assert is_simple_segment(value) is False


@pytest.mark.parametrize("value", SIMPLE_SEGMENTS)
def test_require_simple_segment_accepts_simple_values(value: str) -> None:
    require_simple_segment(value, "Segment")


@pytest.mark.parametrize("value", NON_SIMPLE_SEGMENTS)
def test_require_simple_segment_rejects_non_simple_values(value: str | None) -> None:
    with pytest.raises(ValueError, match="must be a non-blank simple path segment"):
        require_simple_segment(value, "Segment")


@pytest.mark.parametrize(
    ("value", "label", "expected_message"),
    [
        ("..", "Extension name", "Extension name must be a non-blank simple path segment but was ..."),
        ("a/b", "Language", "Language must be a non-blank simple path segment but was a/b."),
        (None, "Template version", "Template version must be a non-blank simple path segment but was None."),
    ],
)
def test_require_simple_segment_message_carries_label_and_value(
    value: str | None, label: str, expected_message: str
) -> None:
    with pytest.raises(ValueError) as excinfo:
        require_simple_segment(value, label)
    assert str(excinfo.value) == expected_message


@pytest.mark.parametrize("value", SIMPLE_RELATIVE_PATHS)
def test_require_simple_relative_path_accepts_simple_paths(value: str) -> None:
    require_simple_relative_path(value, "Path")


@pytest.mark.parametrize("value", NON_SIMPLE_RELATIVE_PATHS)
def test_require_simple_relative_path_rejects_non_simple_paths(value: str | None) -> None:
    with pytest.raises(ValueError):
        require_simple_relative_path(value, "Path")


@pytest.mark.parametrize("value", [None, "", "   "])
def test_require_simple_relative_path_blank_message(value: str | None) -> None:
    with pytest.raises(ValueError, match="Scenario path must be a non-blank relative path but was"):
        require_simple_relative_path(value, "Scenario path")


@pytest.mark.parametrize("value", ["a/../b", "../a", "a/..", "a//b", "a\\b"])
def test_require_simple_relative_path_segment_message(value: str) -> None:
    with pytest.raises(ValueError, match="Scenario path must contain only simple path segments but was"):
        require_simple_relative_path(value, "Scenario path")
