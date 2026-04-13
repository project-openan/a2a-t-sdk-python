from __future__ import annotations


class PromptAnalysisError(Exception):
    """Base class for shared prompt analysis errors."""

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.context = context


class ScenarioRecognitionError(PromptAnalysisError):
    """Raised when scenario recognition output is invalid."""


class SlotExtractionError(PromptAnalysisError):
    """Raised when slot extraction output is invalid."""
