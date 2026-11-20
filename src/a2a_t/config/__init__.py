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

"""Configuration management for a2a_t."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "A2ATConfig",
    "ConfigError",
    "ConfigFileNotFoundError",
    "LlmRuntimeConfig",
    "PromptRuntimeConfig",
]


def __getattr__(name: str) -> Any:
    if name in {"ConfigError", "ConfigFileNotFoundError"}:
        value = getattr(import_module("a2a_t.config.errors"), name)
    elif name in {"A2ATConfig", "LlmRuntimeConfig"}:
        value = getattr(import_module("a2a_t.config.models"), name)
    elif name in {"PromptRuntimeConfig"}:
        value = getattr(import_module("a2a_t.config.models"), name)
    else:
        raise AttributeError(f"module 'a2a_t.config' has no attribute {name!r}")

    globals()[name] = value
    return value
