"""Template registry for managing prompt templates."""

from __future__ import annotations

from typing import Any
from a2a_t.prompt.models import PromptTemplate


class TemplateRegistry:
    """Central registry for prompt templates."""

    def __init__(self) -> None:
        self._templates: dict[str, PromptTemplate] = {}

    def add(self, template: PromptTemplate) -> None:
        """Add a template to the registry."""
        self._templates[template.name] = template

    def get(self, name: str, version: str | None = None) -> PromptTemplate | None:
        """Get a template by name and optional version."""
        template = self._templates.get(name)
        if template and version and template.version != version:
            return None
        return template

    def remove(self, name: str) -> bool:
        """Remove a template from the registry."""
        return self._templates.pop(name, None) is not None

    def find_by_tag(self, tag: str) -> list[PromptTemplate]:
        """Find templates by tag."""
        return [
            t for t in self._templates.values() if tag in t.metadata.get("tags", [])
        ]
