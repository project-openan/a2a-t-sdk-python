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


class SlotConfigLoadError(PromptComplianceError):
    """Raised when a slot configuration file cannot be loaded."""

    pass


class SlotConfigValidationError(PromptComplianceError):
    """Raised when a slot configuration file is invalid."""

    pass


class SlotExtractionError(PromptComplianceError):
    """Raised when structured slot extraction fails or returns invalid data."""

    pass


class GuardrailRejectedError(PromptComplianceError):
    """Raised when the safety guardrail rejects the processed prompt."""

    pass


class GuardrailExecutionError(PromptComplianceError):
    """Raised when the safety guardrail fails due to a transient external error."""

    pass
