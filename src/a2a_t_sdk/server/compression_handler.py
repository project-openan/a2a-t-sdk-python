"""Compression handler middleware for A2A server."""

from __future__ import annotations

from typing import Any


class CompressionHandler:
    """Middleware for decompressing incoming context."""

    def __init__(self, decompressor: Any | None = None) -> None:
        self._decompressor = decompressor

    def decompress(self, context: bytes | str) -> dict[str, Any]:
        """Decompress incoming context."""
        raise NotImplementedError

    def process(self, task_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Process and decompress the incoming task."""
        raise NotImplementedError
