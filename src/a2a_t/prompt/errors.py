from __future__ import annotations


class PromptLoaderError(Exception):
    """Prompt Loader 所有异常的基类 / Base class for all prompt loader errors."""

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.context = context


class PromptSourceError(PromptLoaderError):
    """当 Prompt 来源非法或不受支持时抛出 / Raised when a prompt source is invalid or unsupported."""


class PromptConfigError(PromptLoaderError):
    """当 Prompt 运行时配置不合法时抛出 / Raised when prompt runtime configuration is invalid."""


class PromptFetchError(PromptLoaderError):
    """当无法从来源获取 Prompt 内容时抛出 / Raised when prompt content cannot be fetched from a source."""


class PromptParseError(PromptLoaderError):
    """当 Prompt 内容无法解析为模板时抛出 / Raised when prompt content cannot be parsed as a template."""


class PromptMetadataError(PromptLoaderError):
    """当必填 Prompt 元数据缺失或不匹配时抛出 / Raised when required prompt metadata is missing or mismatched."""


class PromptCacheError(PromptLoaderError):
    """当缓存的 Prompt 内容无法读写时抛出 / Raised when cached prompt content cannot be read or written."""


class PromptConflictError(PromptLoaderError):
    """当 prompt 身份冲突无法被解决时抛出 / Raised when prompt identity conflicts cannot be resolved."""


class PromptVersionComparisonError(PromptLoaderError):
    """当 prompt 版本比较失败时抛出 / Raised when prompt version comparison fails."""


class PromptCatalogRegistryError(PromptLoaderError):
    """当 Prompt Catalog registry 操作失败时抛出 / Raised when prompt catalog registry operations fail."""
