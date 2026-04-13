"""A2A-T 的 Prompt 顶层包 / Top-level prompt package for A2A-T."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "AgentFetcher",
    "AgentPromptCatalog",
    "AgentProvider",
    "CacheStatus",
    "CacheStore",
    "ConflictResolutionPolicy",
    "CachedPromptRecord",
    "DefaultPromptCatalogRegistry",
    "ExpirationPolicy",
    "FetchResult",
    "LocalFileFetcher",
    "LocalFilePromptStore",
    "LocalFileProvider",
    "LocalPromptCatalog",
    "MarkdownPromptParser",
    "OverwriteIfNewerVersionPolicy",
    "OverwriteOnConflictPolicy",
    "Prompt",
    "PromptCacheError",
    "PromptCatalog",
    "PromptCatalogRegistry",
    "PromptCatalogRegistryError",
    "PromptConfigError",
    "PromptConflictError",
    "PromptFetchError",
    "PromptLoader",
    "PromptLoaderConfig",
    "PromptLoaderError",
    "PromptMetadataError",
    "PromptParseError",
    "PromptParser",
    "PromptParserRegistry",
    "PromptProvider",
    "PromptReference",
    "PromptSource",
    "PromptSourceError",
    "PromptStore",
    "PromptVersionComparisonError",
    "TTLExpirationPolicy",
    "UrlFetcher",
    "UrlIndexFetcher",
    "UrlPromptCatalog",
    "UrlProvider",
    "build_default_prompt_parser_registry",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentFetcher": ("a2a_t.prompt.resources.providers", "AgentFetcher"),
    "AgentPromptCatalog": ("a2a_t.prompt.resources.catalog", "AgentPromptCatalog"),
    "AgentProvider": ("a2a_t.prompt.resources.providers", "AgentProvider"),
    "CacheStatus": ("a2a_t.prompt.common.models", "CacheStatus"),
    "CacheStore": ("a2a_t.prompt.resources.cache", "CacheStore"),
    "ConflictResolutionPolicy": ("a2a_t.prompt.resources.cache", "ConflictResolutionPolicy"),
    "CachedPromptRecord": ("a2a_t.prompt.common.models", "CachedPromptRecord"),
    "DefaultPromptCatalogRegistry": ("a2a_t.prompt.resources.catalog_registry", "DefaultPromptCatalogRegistry"),
    "ExpirationPolicy": ("a2a_t.prompt.resources.cache", "ExpirationPolicy"),
    "FetchResult": ("a2a_t.prompt.common.models", "FetchResult"),
    "LocalFileFetcher": ("a2a_t.prompt.resources.providers", "LocalFileFetcher"),
    "LocalFilePromptStore": ("a2a_t.prompt.resources.cache", "LocalFilePromptStore"),
    "LocalFileProvider": ("a2a_t.prompt.resources.providers", "LocalFileProvider"),
    "LocalPromptCatalog": ("a2a_t.prompt.resources.catalog", "LocalPromptCatalog"),
    "MarkdownPromptParser": ("a2a_t.prompt.resources.parser", "MarkdownPromptParser"),
    "OverwriteIfNewerVersionPolicy": ("a2a_t.prompt.resources.cache", "OverwriteIfNewerVersionPolicy"),
    "OverwriteOnConflictPolicy": ("a2a_t.prompt.resources.cache", "OverwriteOnConflictPolicy"),
    "Prompt": ("a2a_t.prompt.common.models", "Prompt"),
    "PromptCacheError": ("a2a_t.prompt.common.errors", "PromptCacheError"),
    "PromptCatalog": ("a2a_t.prompt.resources.catalog", "PromptCatalog"),
    "PromptCatalogRegistry": ("a2a_t.prompt.resources.catalog_registry", "PromptCatalogRegistry"),
    "PromptCatalogRegistryError": ("a2a_t.prompt.common.errors", "PromptCatalogRegistryError"),
    "PromptConfigError": ("a2a_t.prompt.common.errors", "PromptConfigError"),
    "PromptConflictError": ("a2a_t.prompt.common.errors", "PromptConflictError"),
    "PromptFetchError": ("a2a_t.prompt.common.errors", "PromptFetchError"),
    "PromptLoader": ("a2a_t.prompt.loader", "PromptLoader"),
    "PromptLoaderConfig": ("a2a_t.prompt.common.config", "PromptLoaderConfig"),
    "PromptLoaderError": ("a2a_t.prompt.common.errors", "PromptLoaderError"),
    "PromptMetadataError": ("a2a_t.prompt.common.errors", "PromptMetadataError"),
    "PromptParseError": ("a2a_t.prompt.common.errors", "PromptParseError"),
    "PromptParser": ("a2a_t.prompt.resources.parser", "PromptParser"),
    "PromptParserRegistry": ("a2a_t.prompt.resources.parser", "PromptParserRegistry"),
    "PromptProvider": ("a2a_t.prompt.resources.providers", "PromptProvider"),
    "PromptReference": ("a2a_t.prompt.common.models", "PromptReference"),
    "PromptSource": ("a2a_t.prompt.common.models", "PromptSource"),
    "PromptSourceError": ("a2a_t.prompt.common.errors", "PromptSourceError"),
    "PromptStore": ("a2a_t.prompt.resources.cache", "PromptStore"),
    "PromptVersionComparisonError": ("a2a_t.prompt.common.errors", "PromptVersionComparisonError"),
    "TTLExpirationPolicy": ("a2a_t.prompt.resources.cache", "TTLExpirationPolicy"),
    "UrlFetcher": ("a2a_t.prompt.resources.providers", "UrlFetcher"),
    "UrlIndexFetcher": ("a2a_t.prompt.resources.catalog", "UrlIndexFetcher"),
    "UrlPromptCatalog": ("a2a_t.prompt.resources.catalog", "UrlPromptCatalog"),
    "UrlProvider": ("a2a_t.prompt.resources.providers", "UrlProvider"),
    "build_default_prompt_parser_registry": ("a2a_t.prompt.resources.parser", "build_default_prompt_parser_registry"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(f"module 'a2a_t.prompt' has no attribute {name!r}") from error

    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
