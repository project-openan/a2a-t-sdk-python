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

from __future__ import annotations

from dataclasses import dataclass

from a2a_t.common.prompt_resources.models import ScenarioDefinition
from a2a_t.prompt.common.models import PromptReference
from a2a_t.prompt.validation.models import SlotValidationError


@dataclass(slots=True)
class ScenarioRecognitionResult:
    """Represent the normalized result returned by scenario recognition."""

    matched: bool
    scenario_code: str | None
    error_message: str | None


@dataclass(slots=True)
class SlotExtractionResult:
    """Represent the normalized result returned by slot extraction."""

    slots: dict[str, str | None]
    slot_errors: list[SlotValidationError]


@dataclass(slots=True)
class ScenarioResolutionFailure:
    """Represent a standardized failure emitted by scenario resolution."""

    code: str
    message: str
    stage: str


@dataclass(slots=True)
class ScenarioResolutionResult:
    """Represent the normalized result returned by shared scenario resolution."""

    success: bool
    reference: PromptReference | None = None
    scenario: ScenarioDefinition | None = None
    failure: ScenarioResolutionFailure | None = None
