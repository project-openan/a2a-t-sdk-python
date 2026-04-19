from __future__ import annotations


class PromptResourceError(Exception):
    """Base class for shared prompt resource loading errors."""

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.context = context


class PromptResourceNotFoundError(PromptResourceError):
    """Raised when a required prompt resource file does not exist."""


class PromptResourceParseError(PromptResourceError):
    """Raised when a prompt resource file cannot be parsed."""
