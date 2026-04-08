"""Template caching for performance optimization."""

from __future__ import annotations

import time
from threading import Lock
from dataclasses import dataclass, field
from a2a_t.prompt.models import PromptTemplate


@dataclass
class CacheEntry:
    """Cache entry for a template."""

    template: PromptTemplate
    loaded_at: float = field(default_factory=time.time)


class PromptCache:
    """LRU cache for prompt templates."""

    def __init__(self, max_size: int = 100, ttl_seconds: float = 3600.0) -> None:
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._cache: dict[str, CacheEntry] = {}
        self._lock = Lock()

    def get(self, name: str) -> PromptTemplate | None:
        """Get a cached template."""
        with self._lock:
            entry = self._cache.get(name)
            if entry and (time.time() - entry.loaded_at) < self._ttl:
                return entry.template
            self._cache.pop(name, None)
            return None

    def set(self, template: PromptTemplate) -> None:
        """Cache a template."""
        with self._lock:
            if len(self._cache) >= self._max_size:
                oldest = min(self._cache.items(), key=lambda x: x[1].loaded_at)
                self._cache.pop(oldest[0])
            self._cache[template.name] = CacheEntry(template=template)

    def invalidate(self, name: str | None = None) -> None:
        """Invalidate cache entries."""
        with self._lock:
            if name:
                self._cache.pop(name, None)
            else:
                self._cache.clear()
