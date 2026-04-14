"""A2A-T 的 Prompt 顶层包 / Top-level prompt package for A2A-T."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "CacheStatus",
    "ConflictResolutionPolicy",
    "CachedPromptRecord",
    "ExpirationPolicy",
    "FetchResult",
    "OverwriteIfNewerVersionPolicy",
    "OverwriteOnConflictPolicy",
    "Prompt",
    "PromptAssetReference",
    "PromptCacheError",
    "PromptCatalogRegistryError",
    "PromptConfigError",
    "PromptConflictError",
    "PromptFetchError",
    "PromptLoaderError",
    "PromptMetadataError",
    "PromptParseError",
    "PromptReference",
    "PromptSource",
    "PromptSourceError",
    "PromptStore",
    "PromptVersionComparisonError",
    "TTLExpirationPolicy",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "CacheStatus": ("a2a_t.prompt.common.models", "CacheStatus"),
    "ConflictResolutionPolicy": ("a2a_t.prompt.resources.cache", "ConflictResolutionPolicy"),
    "CachedPromptRecord": ("a2a_t.prompt.common.models", "CachedPromptRecord"),
    "ExpirationPolicy": ("a2a_t.prompt.resources.cache", "ExpirationPolicy"),
    "FetchResult": ("a2a_t.prompt.common.models", "FetchResult"),
    "OverwriteIfNewerVersionPolicy": ("a2a_t.prompt.resources.cache", "OverwriteIfNewerVersionPolicy"),
    "OverwriteOnConflictPolicy": ("a2a_t.prompt.resources.cache", "OverwriteOnConflictPolicy"),
    "Prompt": ("a2a_t.prompt.common.models", "Prompt"),
    "PromptAssetReference": ("a2a_t.prompt.common.models", "PromptAssetReference"),
    "PromptCacheError": ("a2a_t.prompt.common.errors", "PromptCacheError"),
    "PromptCatalogRegistryError": ("a2a_t.prompt.common.errors", "PromptCatalogRegistryError"),
    "PromptConfigError": ("a2a_t.prompt.common.errors", "PromptConfigError"),
    "PromptConflictError": ("a2a_t.prompt.common.errors", "PromptConflictError"),
    "PromptFetchError": ("a2a_t.prompt.common.errors", "PromptFetchError"),
    "PromptLoaderError": ("a2a_t.prompt.common.errors", "PromptLoaderError"),
    "PromptMetadataError": ("a2a_t.prompt.common.errors", "PromptMetadataError"),
    "PromptParseError": ("a2a_t.prompt.common.errors", "PromptParseError"),
    "PromptReference": ("a2a_t.prompt.common.models", "PromptReference"),
    "PromptSource": ("a2a_t.prompt.common.models", "PromptSource"),
    "PromptSourceError": ("a2a_t.prompt.common.errors", "PromptSourceError"),
    "PromptStore": ("a2a_t.prompt.resources.cache", "PromptStore"),
    "PromptVersionComparisonError": ("a2a_t.prompt.common.errors", "PromptVersionComparisonError"),
    "TTLExpirationPolicy": ("a2a_t.prompt.resources.cache", "TTLExpirationPolicy"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(f"module 'a2a_t.prompt' has no attribute {name!r}") from error

    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
