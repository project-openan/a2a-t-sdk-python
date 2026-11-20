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

"""Shared prompt resource loading package (D31: the single resource access layer).

The resource access layer (:mod:`~a2a_t.common.prompt_resources.resource_access`) is the one
access point both the prompt pipeline and the negotiation pipeline read resources through; the
module docstring of :mod:`~a2a_t.common.prompt_resources.resource_access` carries the routing
table. The view models in :mod:`~a2a_t.common.prompt_resources.models` project raw resources into
the shapes the pipelines consume, and :mod:`~a2a_t.common.prompt_resources.catalog` hosts the
directory-driven template catalog and query service consumed by the client and server facades.
"""

from .catalog import PromptTemplateCatalog, TemplateQueryService, extract_description
from .models import (
    SOURCE_LOCAL,
    SOURCE_PACKAGED,
    PromptMessages,
    PromptTemplate,
    ScenarioDefinition,
    SlotDefinition,
    SlotRange,
    SlotSchema,
    slot_schema_from_json_schema,
)
from .resource_access import (
    LOCAL_FILE_SOURCE_TYPE,
    PACKAGED_SOURCE_TYPE,
    LocalFilePromptResourceAccess,
    PackagedPromptResourceAccess,
    PromptResourceAccess,
    create,
)
from .vocabulary import CANONICAL_KEYS, Vocabulary

__all__ = [
    "CANONICAL_KEYS",
    "LOCAL_FILE_SOURCE_TYPE",
    "PACKAGED_SOURCE_TYPE",
    "LocalFilePromptResourceAccess",
    "PackagedPromptResourceAccess",
    "PromptMessages",
    "PromptResourceAccess",
    "PromptTemplate",
    "PromptTemplateCatalog",
    "ScenarioDefinition",
    "SOURCE_LOCAL",
    "SOURCE_PACKAGED",
    "SlotDefinition",
    "SlotRange",
    "SlotSchema",
    "TemplateQueryService",
    "Vocabulary",
    "create",
    "extract_description",
    "slot_schema_from_json_schema",
]
