"""Client with context compression support."""

from __future__ import annotations

from typing import Any


class CompressionClient:
    """Client that compresses request context before sending."""

    def __init__(self, compression_strategy: Any | None = None) -> None:
        self._compression_strategy = compression_strategy

    def send_with_compression(
        self, task_id: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Send a request with compressed context."""
        raise NotImplementedError
