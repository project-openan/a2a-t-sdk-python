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

"""Error model of the A2A-T SDK core.

Public surface of the ``core/errors`` package: the closed :class:`ErrorCatalog` (42 layered
``domain.semantic`` codes with their categories and fact parameters), the never-throw
:class:`ErrorMessages` renderer backed by the bundled ``prompt_resources/errors`` templates, the
:class:`A2ATError` exception tree, and the :class:`InputLimitConfig` input guard. Port of the Java
1.1.0 ``a2a-t-core`` exception and input-limit classes.
"""

from __future__ import annotations

from a2a_t.core.errors.catalog import Category, ErrorCatalog, by_code
from a2a_t.core.errors.exceptions import (
    A2ATBusinessError,
    A2ATError,
    A2ATParamExtractionError,
    ConfigFileNotFoundError,
    ContentValidationError,
    NegotiationGenerationError,
    NegotiationParamExtractionError,
    PromptGenerationError,
    ResourceNotFoundError,
    SlotValidationError,
)
from a2a_t.core.errors.input_limit import DEFAULT_MAX_TEXT_CHARS, INPUT_TEXT_MAX_CHARS_KEY, InputLimitConfig
from a2a_t.core.errors.messages import DEFAULT_LANGUAGE, ErrorMessages, render, template

__all__ = [
    "A2ATBusinessError",
    "A2ATError",
    "A2ATParamExtractionError",
    "Category",
    "ConfigFileNotFoundError",
    "ContentValidationError",
    "DEFAULT_LANGUAGE",
    "DEFAULT_MAX_TEXT_CHARS",
    "ErrorCatalog",
    "ErrorMessages",
    "INPUT_TEXT_MAX_CHARS_KEY",
    "InputLimitConfig",
    "NegotiationGenerationError",
    "NegotiationParamExtractionError",
    "PromptGenerationError",
    "ResourceNotFoundError",
    "SlotValidationError",
    "by_code",
    "render",
    "template",
]
