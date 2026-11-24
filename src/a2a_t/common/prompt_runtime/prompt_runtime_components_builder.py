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

from a2a_t.common.prompt_resources import PromptResourceAccess, create
from a2a_t.config.models import A2ATConfig
from a2a_t.prompt.validation import JsonSchemaSlotValidator

from .prompt_runtime_components import PromptRuntimeComponents


class PromptRuntimeComponentsBuilder:
    """Build the shared prompt runtime services used by client and server flows.

    The resource access object is assembled from the resolved prompt runtime configuration through
    the D31 factory, which performs the routing (``packaged`` / ``local_file``), the frozen local
    snapshot capture and the custom-root warnings. Callers may pass their own access object to
    inject one (the ``llm_client``-style seam used by tests and embedders).
    """

    def build(
        self,
        *,
        config: A2ATConfig,
        resource_access: PromptResourceAccess | None = None,
    ) -> PromptRuntimeComponents:
        """Create the resource access and shared validators from the resolved config.

        Args:
            config: resolved SDK configuration carrying the prompt runtime settings.
            resource_access: optional access object overriding the one built from the config.

        Returns:
            the shared prompt runtime components.

        Raises:
            ConfigError: when the configured source type is unsupported, or ``local_file`` mode is
                selected without a local root that exists and is a directory.
        """
        access = resource_access if resource_access is not None else create(config.prompt)
        return PromptRuntimeComponents(
            resource_access=access,
            json_schema_slot_validator=JsonSchemaSlotValidator(),
        )
