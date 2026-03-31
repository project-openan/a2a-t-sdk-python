"""Prompt template loader supporting remote and built-in sources."""

from __future__ import annotations

from typing import Any
from pathlib import Path

from a2a_t_sdk.prompt.models import PromptTemplate


class PromptLoader:
    """Loads prompt templates from various sources."""

    def __init__(self, templates_dir: Path | None = None) -> None:
        self._templates_dir = templates_dir

    def load_builtin(self, name: str) -> PromptTemplate:
        """Load a built-in template."""
        raise NotImplementedError

    def load_remote(self, url: str) -> PromptTemplate:
        """Load a template from a remote source."""
        raise NotImplementedError

    def load_file(self, path: Path) -> PromptTemplate:
        """Load a template from a file."""
        raise NotImplementedError
