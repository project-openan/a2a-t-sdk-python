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

"""
a2a_t - Python A2A SDK for Telecom Scenarios

This SDK extends the official a2a-python SDK with features tailored for
telecom operator environments, including prompt management and LLM integration.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__version__ = "1.2.1"

__all__ = [
    "__version__",
    "client",
    "server",
    "config",
    "llm",
    "common",
    "prompt",
]

_LAZY_IMPORTS = {
    "client": "a2a_t.client",
    "server": "a2a_t.server",
    "config": "a2a_t.config",
    "llm": "a2a_t.llm",
    "common": "a2a_t.common",
    "prompt": "a2a_t.prompt",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_IMPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'a2a_t' has no attribute {name!r}")
    module = import_module(module_name)
    globals()[name] = module
    return module
