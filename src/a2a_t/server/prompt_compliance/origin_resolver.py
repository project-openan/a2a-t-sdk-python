from __future__ import annotations

from a2a_t.prompt import Prompt, PromptLoader, PromptReference
from a2a_t.prompt.catalog_registry import PromptCatalogRegistry
from a2a_t.server.prompt_compliance.errors import PromptOriginResolveError
from a2a_t.server.prompt_compliance.models import PromptIdentity


class PromptOriginResolver:
    """通过 catalog 匹配 Prompt 身份并解析原始 Prompt / Resolve the original prompt by matching prompt identity against catalogs."""

    def __init__(self, *, catalog_registry: PromptCatalogRegistry, prompt_loader: PromptLoader) -> None:
        self._catalog_registry = catalog_registry
        self._prompt_loader = prompt_loader

    def resolve(self, identity: PromptIdentity) -> Prompt:
        for catalog in self._catalog_registry.list_catalogs().values():
            for reference in catalog.list():
                if self._matches(identity, reference):
                    return self._prompt_loader.load(reference=reference)

        raise PromptOriginResolveError(
            "Original prompt could not be resolved from prompt identity.",
            name=identity.name,
            language=identity.language,
            version=identity.version,
        )

    def _matches(self, identity: PromptIdentity, reference: PromptReference) -> bool:
        return (
            reference.name == identity.name
            and reference.language == identity.language
            and reference.version == identity.version
        )
