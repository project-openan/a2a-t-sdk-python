"""Data models for prompt management."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PromptTemplate:
    """Represents a prompt template."""

    name: str
    content: str
    description: str = ""
    version: str = "1.0"
    parameters: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def render(self, **kwargs: Any) -> str:
        """Render the template with provided parameters."""
        return self.content.format(**kwargs)


@dataclass
class PromptRegistry:
    """Registry for managing prompt templates."""

    templates: dict[str, PromptTemplate] = field(default_factory=dict)

    def register(self, template: PromptTemplate) -> None:
        """Register a template."""
        self.templates[template.name] = template

    def get(self, name: str) -> PromptTemplate | None:
        """Get a template by name."""
        return self.templates.get(name)

    def list_names(self) -> list[str]:
        """List all registered template names."""
        return list(self.templates.keys())
