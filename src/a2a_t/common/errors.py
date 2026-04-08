"""Common error definitions and formatters for a2a_t."""

from __future__ import annotations


class SDKError(Exception):
    """Base exception for all SDK errors."""

    def __init__(self, message: str, code: str | None = None, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or "SDK_ERROR"
        self.details = details or {}


class ConnectionError(SDKError):
    """Raised when a connection cannot be established."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message, code="CONNECTION_ERROR", details=details)


class TimeoutError(SDKError):
    """Raised when an operation times out."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message, code="TIMEOUT_ERROR", details=details)


class ConfigurationError(SDKError):
    """Raised when configuration is invalid."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message, code="CONFIG_ERROR", details=details)


class ErrorFormatter:
    """Formats errors into structured messages."""

    @staticmethod
    def format(error: SDKError) -> dict:
        """Format an SDK error into a structured dictionary."""
        return {
            "error": {
                "code": error.code,
                "message": error.message,
                "details": error.details,
            }
        }
