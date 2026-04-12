from __future__ import annotations


class PromptComplianceError(Exception):
    """Prompt 遵从校验异常基类 / Base class for prompt compliance errors."""

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.context = context


class ProcessedPromptParseError(PromptComplianceError):
    """加工后 Prompt front matter 解析失败时抛出 / Raised when processed prompt front matter cannot be parsed."""


class PromptOriginResolveError(PromptComplianceError):
    """无法通过 Prompt 身份解析原始 Prompt 时抛出 / Raised when prompt identity cannot resolve original prompt."""


class SlotSchemaLoadError(PromptComplianceError):
    """槽位 schema 文件无法加载时抛出 / Raised when a slot schema file cannot be loaded."""


class SlotSchemaValidationError(PromptComplianceError):
    """槽位 schema 文件格式无效时抛出 / Raised when a slot schema file is invalid."""


class SlotExtractionError(PromptComplianceError):
    """结构化槽位提取失败或返回无效数据时抛出 / Raised when structured slot extraction fails or returns invalid data."""


class GuardrailExecutionError(PromptComplianceError):
    """安全护栏因外部瞬时错误执行失败时抛出 / Raised when the safety guardrail fails due to a transient external error."""


class SlotValidationError(PromptComplianceError):
    """提取槽位不满足运行时槽位 schema 时抛出 / Raised when extracted slots do not satisfy the runtime slot schema."""
