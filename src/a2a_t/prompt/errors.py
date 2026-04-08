from __future__ import annotations


class PromptLoaderError(Exception):
    """Prompt Loader 所有异常的基类 / Base class for all prompt loader errors."""

    def __init__(self, message: str, **context: object) -> None:
        """保存简短错误文本与机器可读上下文字段 / Store a short error message plus machine-readable context fields.

        主错误文本保持简短且便于阅读，额外细节通过 ``context`` 传递，并复用稳定字段名
        如 ``locator``、``source_type``、``cache_key``、``field``、``status_code``、
        ``expected_*`` 与 ``actual_*`` / Keep the primary message concise and pass
        extra details through ``context`` using stable field names such as
        ``locator``, ``source_type``, ``cache_key``, ``field``, ``status_code``,
        ``expected_*``, and ``actual_*``.
        """

        super().__init__(message)
        self.context = context


class PromptSourceError(PromptLoaderError):
    """当 Prompt 来源非法或不受支持时抛出 / Raised when a prompt source is invalid or unsupported."""

    pass


class PromptFetchError(PromptLoaderError):
    """当无法从来源获取 Prompt 内容时抛出 / Raised when prompt content cannot be fetched from a source."""

    pass


class PromptParseError(PromptLoaderError):
    """当 Prompt 内容无法解析为模板时抛出 / Raised when prompt content cannot be parsed as a template."""

    pass


class PromptMetadataError(PromptLoaderError):
    """当必填 Prompt 元数据缺失或不匹配时抛出 / Raised when required prompt metadata is missing or mismatched."""

    pass


class PromptCacheError(PromptLoaderError):
    """当缓存的 Prompt 内容无法读写时抛出 / Raised when cached prompt content cannot be read or written."""

    pass
