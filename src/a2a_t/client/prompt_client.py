"""Prompt-enabled client for template-based requests."""

from __future__ import annotations

from typing import Any


class PromptClient:
    """Client that constructs requests using prompt templates."""

    def __init__(self, template_loader: Any | None = None) -> None:
        self._template_loader = template_loader

    def send_with_template(
        self, template_name: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Send a request using a named template."""
        raise NotImplementedError
