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
from enum import StrEnum
from typing import Any, Literal


class CacheStatus(StrEnum):
    """Describe whether prompt content came from cache or origin."""

    MISS = "miss"
    HIT = "hit"
    EXPIRED = "expired"
    REFRESHED = "refreshed"
    STALE_FALLBACK = "stale_fallback"


@dataclass(slots=True)
class PromptSource:
    """Identify where a prompt or prompt asset was loaded from."""

    source_type: Literal["local_file", "url", "agent"]
    locator: str


@dataclass(slots=True)
class Prompt:
    """Represent a fully loaded prompt payload and its provenance."""

    name: str
    language: str
    title: str
    description: str
    format: str
    body: str
    raw_content: str
    source: PromptSource
    cache_status: CacheStatus


@dataclass(slots=True)
class PromptReference:
    """Identify one scenario-scoped prompt resource set."""

    scenario_code: str
    language: str


@dataclass(slots=True)
class TaskPromptMetadata:
    """Represent the front-matter metadata embedded in task prompts."""

    scenario_code: str
    language: str
    description: str

    def to_prompt_reference(self) -> PromptReference:
        """Convert prompt metadata into the shared prompt reference model."""
        return PromptReference(
            scenario_code=self.scenario_code,
            language=self.language,
        )


@dataclass(slots=True)
class PromptAssetReference:
    """Describe prompt metadata without embedding the full prompt body."""

    name: str
    language: str
    title: str
    description: str
    source: PromptSource
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class FetchResult:
    """Represent raw content fetched from a prompt source."""

    content: str
    content_type: str | None
    source: PromptSource
    fetched_at: Any


@dataclass(slots=True)
class CachedPromptRecord:
    """Store the metadata needed to reuse cached prompt content."""

    cache_key: str
    source_type: str
    name: str
    language: str
    format: str
    fetched_at: Any
    expires_at: Any
    source_locator: str
    parser_name: str
    content_hash: str
    overwrite_reason: str | None = None
    previous_content_hash: str | None = None
