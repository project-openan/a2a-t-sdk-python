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

import json

from .models import NormalizedInput


class InputNormalizer:
    """Normalize raw prompt-generation input into a canonical string payload."""

    def normalize(self, user_input: str | dict[str, object]) -> NormalizedInput:
        """Classify user input and convert it into the format expected by analysis steps."""
        if isinstance(user_input, str):
            if not user_input.strip():
                raise ValueError("user_input must not be empty.")
            parsed_json = self._try_parse_json_object(user_input)
            if parsed_json is not None:
                return NormalizedInput(input_kind="json", normalized_input=user_input)
            return NormalizedInput(input_kind="natural_language", normalized_input=user_input)

        if isinstance(user_input, dict):
            if not user_input:
                raise ValueError("user_input dict must not be empty.")
            return NormalizedInput(
                input_kind="json",
                normalized_input=json.dumps(user_input, ensure_ascii=False),
            )

        raise TypeError("user_input must be str or dict.")

    def _try_parse_json_object(self, user_input: str) -> dict[str, object] | None:
        """Return the parsed object only when the string is a JSON object payload."""
        try:
            parsed = json.loads(user_input)
        except json.JSONDecodeError:
            return None

        if not isinstance(parsed, dict):
            return None
        return parsed
