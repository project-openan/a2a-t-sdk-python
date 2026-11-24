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

"""Resource key for the SDK prompt resource tree (port of Java ``PromptResourceKey``).

Template resources mirror the
:class:`~a2a_t.core.template_uri.TemplateUri` layout one-to-one, so a template
or slot schema for the URI ``Task-T/network-layer/ran-energy-saving/v1`` lives
at ``prompt_resources/templates/Task-T/network-layer/ran-energy-saving/v1/<language>/<file_name>``.
"""

from __future__ import annotations

from dataclasses import dataclass

from a2a_t.core.path_segments import is_simple_segment
from a2a_t.core.template_uri import TemplateUri

__all__ = ["PromptResourceKey"]

_PROMPT_RESOURCES_ROOT = "prompt_resources"
_SCENARIO_CATEGORY = "scenarios"


@dataclass(frozen=True)
class PromptResourceKey:
    """Identifies one resource bundled with the SDK prompt resource tree.

    Attributes:
        category: resource category such as ``prompts``, ``templates``,
            ``slots`` or ``scenarios``.
        path_segments: segments between the category and the language, such as
            ``("slot_extraction",)`` for a prompt or the full template URI
            segments for a template resource; empty for scenarios.
        language: locale identifier.
        file_name: target file name.
    """

    category: str
    path_segments: tuple[str, ...]
    language: str
    file_name: str

    def __post_init__(self) -> None:
        """Validate the components and defensively copy the path segments.

        Raises:
            ValueError: when any component is blank or not a simple path
                segment.
        """
        object.__setattr__(self, "path_segments", tuple(self.path_segments))
        _validate_segment("category", self.category)
        for segment in self.path_segments:
            _validate_segment("path segment", segment)
        _validate_segment("language", self.language)
        _validate_segment("file_name", self.file_name)

    @classmethod
    def prompt(cls, action: str, language: str, file_name: str) -> PromptResourceKey:
        """Create a prompt resource key for one prompt action bundle.

        Args:
            action: prompt action name.
            language: locale identifier.
            file_name: target file name.

        Returns:
            prompt resource key resolving to
            ``prompt_resources/prompts/<action>/<language>/<file_name>``.
        """
        return cls("prompts", (action,), language, file_name)

    @classmethod
    def template(cls, template_uri: TemplateUri, language: str, file_name: str) -> PromptResourceKey:
        """Create a template resource key mirroring the template URI layout.

        Args:
            template_uri: template URI identifying the bundle.
            language: locale identifier.
            file_name: target file name.

        Returns:
            template resource key resolving to
            ``prompt_resources/templates/<template_uri>/<language>/<file_name>``.
        """
        return cls("templates", template_uri.segments, language, file_name)

    @classmethod
    def slot_schema(cls, template_uri: TemplateUri, language: str, file_name: str) -> PromptResourceKey:
        """Create a slot schema resource key mirroring the template URI layout.

        Args:
            template_uri: template URI identifying the bundle.
            language: locale identifier.
            file_name: target file name.

        Returns:
            slot schema resource key resolving to
            ``prompt_resources/slots/<template_uri>/<language>/<file_name>``.
        """
        return cls("slots", template_uri.segments, language, file_name)

    @classmethod
    def scenario(cls, language: str, file_name: str) -> PromptResourceKey:
        """Create a scenario catalog resource key.

        Args:
            language: locale identifier.
            file_name: target file name.

        Returns:
            scenario resource key resolving to
            ``prompt_resources/scenarios/<language>/<file_name>``.
        """
        return cls(_SCENARIO_CATEGORY, (), language, file_name)

    def relative_path(self) -> str:
        """Resolve the relative resource path for the current key.

        Returns:
            relative path under ``prompt_resources/``.
        """
        parts: list[str] = [_PROMPT_RESOURCES_ROOT, self.category]
        if self.category != _SCENARIO_CATEGORY:
            parts.extend(self.path_segments)
        parts.append(self.language)
        parts.append(self.file_name)
        return "/".join(parts)


def _validate_segment(field_name: str, value: str | None) -> None:
    """Validate one resource key component (blank and traversal guards)."""
    if value is None or value.strip() == "":
        raise ValueError(f"{field_name} must not be blank")
    if not is_simple_segment(value):
        raise ValueError(f"{field_name} must be a simple path segment")
