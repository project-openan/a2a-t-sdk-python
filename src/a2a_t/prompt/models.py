from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal


class CacheStatus(StrEnum):
    """表示 Prompt 缓存状态 / Represent the cache state of a prompt load."""

    MISS = "miss"
    HIT = "hit"
    EXPIRED = "expired"
    REFRESHED = "refreshed"
    STALE_FALLBACK = "stale_fallback"


@dataclass(slots=True)
class PromptSource:
    """描述 Prompt 的来源信息 / Describe where a prompt was loaded from."""

    source_type: Literal["local_file", "url", "agent"]
    locator: str


@dataclass(slots=True)
class Prompt:
    """表示解析完成的 Prompt / Represent a parsed prompt."""

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
    """表示可供 catalog 输出并供 loader 使用的 Prompt 引用 / Represent a prompt reference used both for catalog listing and loader input."""

    name: str
    language: str
    version: str
    title: str
    description: str
    source: PromptSource
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class FetchResult:
    """表示 provider 获取到的原始结果 / Represent the raw result returned by a provider."""

    content: str
    content_type: str | None
    source: PromptSource
    fetched_at: Any


@dataclass(slots=True)
class CachedPromptRecord:
    """表示落盘缓存的元数据记录 / Represent the metadata record stored for a cached prompt."""

    cache_key: str
    source_type: str
    name: str
    language: str
    version: str
    format: str
    fetched_at: Any
    expires_at: Any
    checksum: str
