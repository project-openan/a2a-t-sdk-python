from __future__ import annotations


class PromptComplianceError(Exception):
    """Base class for prompt compliance errors."""

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.context = context


class ProcessedPromptParseError(PromptComplianceError):
    """Raised when the processed prompt front matter cannot be parsed."""

    pass


class PromptOriginResolveError(PromptComplianceError):
    """Raised when the original prompt cannot be resolved from prompt identity."""

    pass


class SlotSchemaLoadError(PromptComplianceError):
    """Raised when a slot schema file cannot be loaded."""

    pass


class SlotSchemaValidationError(PromptComplianceError):
    """Raised when a slot schema file is invalid."""

    pass


class SlotExtractionError(PromptComplianceError):
    """Raised when structured slot extraction fails or returns invalid data."""

    pass


class GuardrailExecutionError(PromptComplianceError):
    """Raised when the safety guardrail fails due to a transient external error."""

    pass


class SlotValidationError(PromptComplianceError):
    """Raised when extracted slots do not satisfy the runtime slot schema."""

    pass
