from __future__ import annotations

from typing import Protocol

from .catalog import PromptCatalog
from .errors import PromptCatalogRegistryError


class PromptCatalogRegistry(Protocol):
    """暴露所有可用 Prompt Catalog / Expose all available prompt catalogs."""

    def list_catalogs(self) -> dict[str, PromptCatalog]: ...


class DefaultPromptCatalogRegistry:
    """管理 Prompt Catalog 注册表 / Manage prompt catalog registrations."""

    def __init__(self) -> None:
        self._catalogs: dict[str, PromptCatalog] = {}

    def register(self, name: str, catalog: PromptCatalog) -> None:
        self._catalogs[name] = catalog

    def unregister(self, name: str) -> None:
        self._catalogs.pop(name, None)

    def get(self, name: str) -> PromptCatalog:
        try:
            return self._catalogs[name]
        except KeyError as error:
            raise PromptCatalogRegistryError("Prompt catalog is not registered.", catalog_name=name) from error

    def list_catalogs(self) -> dict[str, PromptCatalog]:
        return dict(self._catalogs)
