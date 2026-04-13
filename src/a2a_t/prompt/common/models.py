from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal


class CacheStatus(StrEnum):
    MISS = "miss"
    HIT = "hit"
    EXPIRED = "expired"
    REFRESHED = "refreshed"
    STALE_FALLBACK = "stale_fallback"


@dataclass(slots=True)
class PromptSource:
    source_type: Literal["local_file", "url", "agent"]
    locator: str


@dataclass(slots=True)
class Prompt:
    name: str
    language: str
    version: str
    title: str
    description: str
    format: str
    body: str
    raw_content: str
    source: PromptSource
    cache_status: CacheStatus


@dataclass(slots=True)
class PromptReference:
    scenario_code: str
    language: str
    version: str


@dataclass(slots=True)
class PromptAssetReference:
    name: str
    language: str
    version: str
    title: str
    description: str
    source: PromptSource
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class FetchResult:
    content: str
    content_type: str | None
    source: PromptSource
    fetched_at: Any


@dataclass(slots=True)
class CachedPromptRecord:
    cache_key: str
    source_type: str
    name: str
    language: str
    version: str
    format: str
    fetched_at: Any
    expires_at: Any
    source_locator: str
    parser_name: str
    content_hash: str
    overwrite_reason: str | None = None
    previous_content_hash: str | None = None
