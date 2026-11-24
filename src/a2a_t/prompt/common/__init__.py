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

"""Shared common prompt runtime primitives."""

from .errors import (
    PromptCacheError,
    PromptCatalogRegistryError,
    PromptConfigError,
    PromptConflictError,
    PromptFetchError,
    PromptLoaderError,
    PromptMetadataError,
    PromptParseError,
    PromptSourceError,
    PromptVersionComparisonError,
    TaskPromptFormatError,
)
from .models import (
    CachedPromptRecord,
    CacheStatus,
    FetchResult,
    Prompt,
    PromptAssetReference,
    PromptReference,
    PromptSource,
    TaskPromptMetadata,
)
from .task_prompt_format import format_task_prompt, parse_task_prompt_metadata

__all__ = [
    "CacheStatus",
    "CachedPromptRecord",
    "FetchResult",
    "Prompt",
    "PromptAssetReference",
    "PromptCacheError",
    "PromptCatalogRegistryError",
    "PromptConfigError",
    "PromptConflictError",
    "PromptFetchError",
    "PromptLoaderError",
    "PromptMetadataError",
    "PromptParseError",
    "PromptReference",
    "PromptSource",
    "PromptSourceError",
    "PromptVersionComparisonError",
    "TaskPromptFormatError",
    "TaskPromptMetadata",
    "parse_task_prompt_metadata",
    "format_task_prompt",
]
