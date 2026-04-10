from __future__ import annotations


class PromptComplianceError(Exception):
    """Base class for prompt compliance errors."""

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.context = context
