"""Prompt validation middleware for A2A server."""

from __future__ import annotations

from typing import Any


class PromptHandler:
    """Middleware for validating prompt requests."""

    def __init__(self, validator: Any | None = None) -> None:
        self._validator = validator

    def validate(self, template_name: str, params: dict[str, Any]) -> tuple[bool, str]:
        """Validate request against template rules."""
        raise NotImplementedError

    def process(self, task_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Process and validate the incoming task."""
        raise NotImplementedError
