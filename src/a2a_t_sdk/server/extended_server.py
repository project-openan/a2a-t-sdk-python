"""Extended A2A server with middleware support."""

from __future__ import annotations

from typing import Any, Callable


class ExtendedServer:
    """Extended A2A server with middleware pipeline."""

    def __init__(self) -> None:
        self._middlewares: list[Callable] = []

    def add_middleware(self, middleware: Callable) -> None:
        """Add a middleware to the pipeline."""
        self._middlewares.append(middleware)

    def handle(self, task_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Handle an incoming task."""
        raise NotImplementedError

    def start(self) -> None:
        """Start the server."""
        raise NotImplementedError

    def stop(self) -> None:
        """Stop the server."""
        raise NotImplementedError
