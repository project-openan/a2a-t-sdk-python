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

from .errors import TaskPromptFormatError
from .models import TaskPromptMetadata

_FRONT_MATTER_OPEN = "---\n"
_FRONT_MATTER_CLOSE = "\n---\n"


def format_task_prompt(*, body: str, metadata: TaskPromptMetadata) -> str:
    """Embed task prompt metadata as front matter above the rendered body."""
    return (
        _FRONT_MATTER_OPEN
        + f"scenario_code: {metadata.scenario_code}\n"
        + f"language: {metadata.language}\n"
        + f"description: {metadata.description}\n"
        + "---\n\n"
        + f"{body}"
    )


def parse_task_prompt_metadata(prompt_text: str) -> TaskPromptMetadata:
    """Parse and validate task prompt front matter into structured metadata."""
    if not prompt_text.startswith(_FRONT_MATTER_OPEN):
        raise TaskPromptFormatError("Task prompt must start with front matter.")

    closing_index = prompt_text.find(_FRONT_MATTER_CLOSE, len(_FRONT_MATTER_OPEN))
    if closing_index == -1:
        raise TaskPromptFormatError("Task prompt front matter is not closed.")

    header = prompt_text[len(_FRONT_MATTER_OPEN) : closing_index]
    metadata: dict[str, str] = {}

    for line in header.splitlines():
        # Preserve a minimal parser here so format violations surface with field-specific errors.
        if not line.strip():
            continue
        if ":" not in line:
            raise TaskPromptFormatError(f"Invalid front matter line: {line}")

        key, value = line.split(":", 1)
        normalized_key = key.strip()
        normalized_value = value.strip()
        if not normalized_key:
            raise TaskPromptFormatError(f"Invalid front matter line: {line}")
        metadata[normalized_key] = normalized_value

    scenario_code = _require_metadata_field(metadata, "scenario_code")
    language = _require_metadata_field(metadata, "language")
    description = _require_metadata_field(metadata, "description")

    return TaskPromptMetadata(
        scenario_code=scenario_code,
        language=language,
        description=description,
    )


def _require_metadata_field(metadata: dict[str, str], field: str) -> str:
    """Return a required metadata field or raise a field-specific format error."""
    value = metadata.get(field)
    if value is None or not value.strip():
        raise TaskPromptFormatError(
            f"Task prompt is missing required field: {field}.",
            field=field,
        )
    return value
