from __future__ import annotations


class GuardrailExecutionError(Exception):
    """Raised when the safety guardrail execution fails."""

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.context = context
