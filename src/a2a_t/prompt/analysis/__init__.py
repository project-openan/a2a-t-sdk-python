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

"""Shared prompt analysis package."""

from .errors import PromptAnalysisError, ScenarioRecognitionError, SlotExtractionError
from .json_schema_builder import AnalysisJsonSchemaBuilder
from .message_builder import AnalysisMessageBuilder
from .models import (
    ScenarioRecognitionResult,
    ScenarioResolutionFailure,
    ScenarioResolutionResult,
    SlotExtractionResult,
)
from .scenario_recognizer import ScenarioRecognizer
from .scenario_resolution_orchestrator import ScenarioResolutionOrchestrator
from .slot_extractor import SlotExtractor

__all__ = [
    "AnalysisJsonSchemaBuilder",
    "AnalysisMessageBuilder",
    "PromptAnalysisError",
    "ScenarioRecognitionError",
    "ScenarioRecognitionResult",
    "ScenarioRecognizer",
    "ScenarioResolutionFailure",
    "ScenarioResolutionOrchestrator",
    "ScenarioResolutionResult",
    "SlotExtractor",
    "SlotExtractionError",
    "SlotExtractionResult",
]
