from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .cache import ConflictResolutionPolicy, LocalFilePromptStore
from .catalog import LocalPromptCatalog, PromptCatalog
from .catalog_registry import DefaultPromptCatalogRegistry
from .config import PromptLoaderConfig
from .loader import PromptLoader
from .parser import MarkdownPromptParser, PromptParser, PromptParserRegistry, build_default_prompt_parser_registry
from .providers import AgentProvider, LocalFileProvider, PromptProvider, UrlProvider


def build_default_prompt_catalog_registry(
    config: PromptLoaderConfig,
    *,
    parser_registry: PromptParserRegistry | None = None,
    registry: DefaultPromptCatalogRegistry | None = None,
    local_catalog: PromptCatalog | None = None,
) -> DefaultPromptCatalogRegistry:
    prompt_parser_registry = parser_registry or build_default_prompt_parser_registry()
    catalog_registry = registry or DefaultPromptCatalogRegistry()
    catalog_registry.register(
        "local",
        local_catalog
        or LocalPromptCatalog(
            prompt_dir=config.local_prompt_dir,
            parser_registry=prompt_parser_registry,
            allowed_extensions=config.allowed_extensions,
        ),
    )
    return catalog_registry


def build_default_prompt_loader(
    config: PromptLoaderConfig,
    *,
    parser: PromptParser | None = None,
    cache_store: LocalFilePromptStore | None = None,
    providers: dict[str, PromptProvider] | None = None,
    now_provider: Callable[[], datetime] | None = None,
    conflict_resolution_policy: ConflictResolutionPolicy | None = None,
) -> PromptLoader:
    prompt_parser = parser or MarkdownPromptParser()
    prompt_cache_store = cache_store or LocalFilePromptStore(
        config.local_prompt_dir,
        conflict_resolution_policy=conflict_resolution_policy,
    )
    prompt_providers = providers or {
        "local_file": LocalFileProvider(),
        "url": UrlProvider(),
        "agent": AgentProvider(),
    }
    return PromptLoader(
        config=config,
        parser=prompt_parser,
        cache_store=prompt_cache_store,
        providers=prompt_providers,
        now_provider=now_provider or (lambda: datetime.now(timezone.utc)),
    )
