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

"""Numbered-line formatting of negotiation item lists (port of Java ``NegotiationItemFormatter``).

Each item becomes one line ``N. name<colon>value``; an item without a value becomes ``N. name``.
The caller supplies the list punctuation from the negotiation vocabulary
(``punct.list_colon``) so the output matches the message language: the zh-CN vocabulary carries
the full-width ``：`` while the en-US vocabulary carries ``": "``.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..content.models import NegotiationItem

__all__ = ["format_items"]


def format_items(items: Sequence[NegotiationItem] | None, list_colon: str | None) -> str:
    """Format one item list as numbered markdown lines.

    Args:
        items: items to format; ``None`` and empty both produce an empty string.
        list_colon: colon punctuation appended between item name and value, such as ``：`` or
            ``": "``; ``None`` formats the value directly after the name.

    Returns:
        numbered lines joined by single newlines, or an empty string when there is no item.
    """
    if items is None or len(items) == 0:
        return ""
    colon = "" if list_colon is None else list_colon
    lines: list[str] = []
    for index, item in enumerate(items):
        line = f"{index + 1}. {item.name}"
        if item.value is not None and item.value.strip():
            line = f"{line}{colon}{item.value}"
        lines.append(line)
    return "\n".join(lines)
