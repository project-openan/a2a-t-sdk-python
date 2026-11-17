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

from a2a_t.common.prompt_resources import PromptResourceAccess
from a2a_t.prompt.validation.json_schema_slot_validator import JsonSchemaSlotValidator


@dataclass(slots=True)
class PromptRuntimeComponents:
    """Group the shared prompt runtime services built from configuration.

    Every prompt-pipeline resource read goes through the single resource access object (D31); the
    JSON-schema slot validator is the only stateless helper shared by the client and server flows.
    """

    resource_access: PromptResourceAccess
    json_schema_slot_validator: JsonSchemaSlotValidator
