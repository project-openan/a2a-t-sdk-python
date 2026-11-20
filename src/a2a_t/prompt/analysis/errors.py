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

from a2a_t.core.errors.exceptions import A2ATError


class PromptAnalysisError(A2ATError):
    """Base class for shared prompt analysis errors.

    Part of the :class:`~a2a_t.core.errors.exceptions.A2ATError` tree with the default
    ``infra.internal_error`` code (Java parity: the analysis exceptions extend ``A2ATError``
    without their own code). Orchestrator boundaries translate them to the step-specific catalog
    code, for example ``llm.response_invalid`` for a response-contract violation or
    ``scenario.not_matched`` during scenario recognition.
    """

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.context = context


class ScenarioRecognitionError(PromptAnalysisError):
    """Raised when scenario recognition output is invalid."""


class SlotExtractionError(PromptAnalysisError):
    """Raised when slot extraction output is invalid."""
