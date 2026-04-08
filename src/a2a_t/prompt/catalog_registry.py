from __future__ import annotations

from typing import Protocol

from .catalog import PromptCatalog


class PromptCatalogRegistry(Protocol):
    """暴露所有可用 Prompt Catalog / Expose all available prompt catalogs."""

    def list_catalogs(self) -> dict[str, PromptCatalog]: ...
