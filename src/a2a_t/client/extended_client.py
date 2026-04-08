"""Extended A2A client with connection pooling and enhanced features."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from a2a_t.common.connection_pool import PoolConfig


class ExtendedClient:
    """Extended A2A client with connection pooling support."""

    def __init__(self, url: str, pool_config: PoolConfig | None = None) -> None:
        self._url = url
        self._pool_config = pool_config

    def send(self, task_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a task request."""
        raise NotImplementedError

    def close(self) -> None:
        """Close the client and release resources."""
        raise NotImplementedError
