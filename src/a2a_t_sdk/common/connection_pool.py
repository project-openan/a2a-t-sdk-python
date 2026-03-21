"""Connection pool implementation for a2a_t_sdk."""

from __future__ import annotations

from typing import TYPE_CHECKING
from dataclasses import dataclass, field
from threading import Lock

if TYPE_CHECKING:
    from typing import Any, Callable, TypeVar

    T = TypeVar("T")


@dataclass
class PoolConfig:
    """Configuration for connection pool."""

    max_size: int = 10
    min_size: int = 1
    timeout: float = 30.0
    max_idle_time: float = 300.0


class ConnectionPool:
    """Thread-safe connection pool for reusing connections."""

    def __init__(self, factory: Callable[[], T], config: PoolConfig | None = None) -> None:
        self._factory = factory
        self._config = config or PoolConfig()
        self._pool: list[T] = []
        self._in_use: set[T] = set()
        self._lock = Lock()
        self._initialized = False

    def get(self) -> T:
        """Get a connection from the pool."""
        with self._lock:
            if self._pool:
                conn = self._pool.pop()
                self._in_use.add(conn)
                return conn
            return self._factory()

    def release(self, conn: T) -> None:
        """Return a connection to the pool."""
        with self._lock:
            if conn in self._in_use:
                self._in_use.discard(conn)
                self._pool.append(conn)

    def close(self) -> None:
        """Close all connections in the pool."""
        with self._lock:
            self._pool.clear()
            self._in_use.clear()
