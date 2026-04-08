"""Rate limiter for protecting backend LLM services."""

from __future__ import annotations

import time
from threading import Lock
from dataclasses import dataclass, field


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""

    max_requests: int = 100
    window_seconds: float = 60.0
    burst_size: int = 10


class RateLimiter:
    """Token bucket rate limiter implementation."""

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self._config = config or RateLimitConfig()
        self._tokens: dict[str, list[float]] = field(default_factory=dict)
        self._lock = Lock()

    def allow(self, client_id: str) -> bool:
        """Check if a request from client_id is allowed."""
        with self._lock:
            now = time.time()
            window_start = now - self._config.window_seconds

            if client_id not in self._tokens:
                self._tokens[client_id] = []

            self._tokens[client_id] = [
                t for t in self._tokens[client_id] if t > window_start
            ]

            if len(self._tokens[client_id]) < self._config.max_requests:
                self._tokens[client_id].append(now)
                return True
            return False

    def reset(self, client_id: str | None = None) -> None:
        """Reset rate limit counters."""
        with self._lock:
            if client_id:
                self._tokens.pop(client_id, None)
            else:
                self._tokens.clear()
