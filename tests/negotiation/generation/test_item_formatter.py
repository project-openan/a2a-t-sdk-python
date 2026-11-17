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

"""Tests of the numbered-line negotiation item formatter.

Port of the Java ``NegotiationItemFormatterTest``: punctuation comes from the vocabulary
(``punct.list_colon``), the value-less items omit the colon, ``None``/empty lists render empty and
a ``None`` colon is tolerated.
"""

from __future__ import annotations

import pytest

from a2a_t.negotiation.content import NegotiationItem
from a2a_t.negotiation.generation.item_formatter import format_items

ZH_ITEMS = [NegotiationItem("节能区域信息", "松山湖"), NegotiationItem("节能速率保障目标", "20Mbps")]
EN_ITEMS = [
    NegotiationItem("Energy-saving area information", "Songshan Lake"),
    NegotiationItem("Rate guarantee target", "20 Mbps"),
]


@pytest.mark.parametrize(
    ("items", "list_colon", "expected"),
    [
        (ZH_ITEMS, "：", "1. 节能区域信息：松山湖\n2. 节能速率保障目标：20Mbps"),
        (EN_ITEMS, ": ", "1. Energy-saving area information: Songshan Lake\n2. Rate guarantee target: 20 Mbps"),
    ],
    ids=["zh-full-width-colon", "en-colon-space"],
)
def test_formats_numbered_lines_with_supplied_colon_punctuation(
    items: list[NegotiationItem], list_colon: str, expected: str
) -> None:
    assert format_items(items, list_colon) == expected


@pytest.mark.parametrize("value", [None, "  "], ids=["none", "blank"])
def test_omits_colon_and_value_when_value_is_null_or_blank(value: str | None) -> None:
    items = [NegotiationItem("名称", None), NegotiationItem("另一个名称", value)]

    assert format_items(items, "：") == "1. 名称\n2. 另一个名称"


@pytest.mark.parametrize("items", [None, []], ids=["none", "empty"])
def test_renders_empty_string_for_none_or_empty_list(items: list[NegotiationItem] | None) -> None:
    assert format_items(items, "：") == ""


def test_tolerates_none_colon_punctuation() -> None:
    assert format_items([NegotiationItem("名称", "值")], None) == "1. 名称值"
