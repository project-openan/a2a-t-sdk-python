from __future__ import annotations


class PromptLoaderError(Exception):
    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.context = context


class PromptSourceError(PromptLoaderError):
    pass


class PromptConfigError(PromptLoaderError):
    pass


class PromptFetchError(PromptLoaderError):
    pass


class PromptParseError(PromptLoaderError):
    pass


class PromptMetadataError(PromptLoaderError):
    pass


class PromptCacheError(PromptLoaderError):
    pass


class PromptConflictError(PromptLoaderError):
    pass


class PromptVersionComparisonError(PromptLoaderError):
    pass


class PromptCatalogRegistryError(PromptLoaderError):
    pass
