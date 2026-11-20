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

"""Task-T server-side API registration (port of the Java ``ServerApis``).

The ``validateTaskPromptAndDataFilling`` step binds to the production content validator assembled
by the corpus runtime (``build_content_validator`` of the server compliance layer).
"""

from __future__ import annotations

from engine.client_apis import Args
from engine.registry import ApiRegistry

from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.server.prompt_compliance.content_validator import InputLimitedContentValidator

__all__ = ["register_server_task_apis"]


def register_server_task_apis(registry: ApiRegistry, task_validator: InputLimitedContentValidator) -> None:
    """Register the Task-T phase-1 validation API of one assembled content validator."""
    registry.register(
        "validateTaskPromptAndDataFilling",
        lambda args: _filled_to_map(
            task_validator.validate(
                Args.text(args, "promptText"),
                Args.map(args, "schema"),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )


def _filled_to_map(filled: FilledParamData) -> dict[str, object]:
    """Serialize one filled-parameter result into the transcript payload shape."""
    return {"data": dict(filled.data)}
