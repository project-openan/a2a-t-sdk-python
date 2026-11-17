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

from a2a_t.common.prompt_resources.models import SlotSchema


class AnalysisJsonSchemaBuilder:
    """Build JSON schemas that constrain prompt analysis model outputs."""

    def build_scenario_recognition_schema(self) -> dict[str, object]:
        """Return the schema expected from scenario recognition."""
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["matched", "scenario_code", "error_message"],
            "properties": {
                "matched": {"type": "boolean"},
                "scenario_code": {"type": ["string", "null"]},
                "error_message": {"type": ["string", "null"]},
            },
        }

    def build_slot_extraction_schema(self, *, slot_schema: SlotSchema) -> dict[str, object]:
        """Return the schema expected from slot extraction for a given slot schema."""
        slot_names = [slot.name for slot in slot_schema.slots]
        slot_properties = {slot.name: {"type": ["string", "null"]} for slot in slot_schema.slots}
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["slots", "slot_errors"],
            "properties": {
                "slots": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": slot_names,
                    "properties": slot_properties,
                },
                "slot_errors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["slot_name", "code"],
                        "properties": {
                            "slot_name": {"type": "string", "enum": slot_names},
                            "code": {"type": "string", "enum": [
                                "slot.not_provided",
                                "slot.constraint_violated",
                                "missing_input",
                                "invalid_value",
                            ]},
                            "facts": {"type": "object", "additionalProperties": {"type": "string"}},
                            "message": {"type": "string"},
                        },
                    },
                },
            },
        }
