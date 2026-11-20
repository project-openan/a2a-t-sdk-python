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

"""Task-T client-side API registration (port of the Java ``ClientApis``).

JSON step arguments are bound to the production :class:`~a2a_t.client.prompt_generation.
prompt_generation_orchestrator.PromptGenerationOrchestrator` assembled by the corpus runtime.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from engine.registry import ApiRegistry

from a2a_t.client.prompt_generation.prompt_generation_orchestrator import PromptGenerationOrchestrator
from a2a_t.core.metadata import MetadataContent
from a2a_t.core.template_uri import TemplateUri

__all__ = ["Args", "metadata_to_map", "register_client_task_apis"]


def register_client_task_apis(registry: ApiRegistry, client: PromptGenerationOrchestrator) -> None:
    """Register the Task-T phase-1 client generation APIs of one assembled client orchestrator."""
    registry.register(
        "generateTaskPromptFromText",
        lambda args: metadata_to_map(
            client.generate_task_prompt_from_text(
                Args.text(args, "text"),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )
    registry.register(
        "generateTaskPromptFromDataWithSchema",
        lambda args: metadata_to_map(
            client.generate_task_prompt_from_data_with_schema(
                Args.map(args, "data"),
                Args.map(args, "schema"),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )


def metadata_to_map(content: MetadataContent) -> dict[str, object]:
    """Serialize one generation result into the transcript payload shape."""
    return {
        "templateUri": content.template_uri,
        "promptText": content.prompt_text,
        "extensionUri": content.extension_uri,
    }


class Args:
    """Step-argument binding helpers mirroring the Java ``ClientApis.Args``."""

    @staticmethod
    def text(args: Mapping[str, Any], name: str) -> str:
        """Return one required string argument.

        Blank strings are passed through to the SDK: the facade raises its own coded error, which
        the expectation layer asserts against.
        """
        value = args.get(name)
        if not isinstance(value, str):
            raise ValueError(f"step argument {name} must be a string: {value}")
        return value

    @staticmethod
    def map(args: Mapping[str, Any], name: str) -> dict[str, object]:
        """Return one required non-empty object argument."""
        value = args.get(name)
        if not isinstance(value, dict):
            raise ValueError(f"step argument {name} must be an object: {value}")
        if not value:
            raise ValueError(f"step argument {name} must not be an empty object")
        return dict(value)

    @staticmethod
    def template_uri(args: Mapping[str, Any], name: str) -> TemplateUri:
        """Parse one required template URI argument, fail-fast when malformed."""
        raw = Args.text(args, name)
        parsed = TemplateUri.parse(raw)
        if parsed is None:
            raise ValueError(f"Unparseable template URI: {raw}")
        return parsed
