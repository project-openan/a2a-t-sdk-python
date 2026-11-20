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

"""Top-level prompt package for A2A-T."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "PromptReference",
    "TaskPromptMetadata",
    "TaskPromptFormatError",
    "format_task_prompt",
    "parse_task_prompt_metadata",
    "TaskPromptRenderer",
    "TaskPromptRenderError",
    "collapse_sections",
    "drop_sections",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "PromptReference": ("a2a_t.prompt.common.models", "PromptReference"),
    "TaskPromptMetadata": ("a2a_t.prompt.common.models", "TaskPromptMetadata"),
    "TaskPromptFormatError": ("a2a_t.prompt.common.errors", "TaskPromptFormatError"),
    "format_task_prompt": ("a2a_t.prompt.common.task_prompt_format", "format_task_prompt"),
    "parse_task_prompt_metadata": ("a2a_t.prompt.common.task_prompt_format", "parse_task_prompt_metadata"),
    "TaskPromptRenderer": ("a2a_t.prompt.task_rendering", "TaskPromptRenderer"),
    "TaskPromptRenderError": ("a2a_t.prompt.task_rendering.errors", "TaskPromptRenderError"),
    "collapse_sections": ("a2a_t.prompt.task_rendering", "collapse_sections"),
    "drop_sections": ("a2a_t.prompt.task_rendering", "drop_sections"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(f"module 'a2a_t.prompt' has no attribute {name!r}") from error

    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
