"""Prompt-specific error definitions."""


class PromptError(Exception):
    """Base exception for prompt-related errors."""

    def __init__(self, message: str, template_name: str | None = None) -> None:
        super().__init__(message)
        self.template_name = template_name


class ValidationError(PromptError):
    """Raised when prompt validation fails."""

    def __init__(
        self, message: str, template_name: str | None = None, errors: list[str] | None = None
    ) -> None:
        super().__init__(message, template_name)
        self.validation_errors = errors or []


class TemplateNotFoundError(PromptError):
    """Raised when a template is not found."""

    def __init__(self, template_name: str) -> None:
        super().__init__(f"Template not found: {template_name}", template_name)


class TemplateLoadError(PromptError):
    """Raised when a template fails to load."""

    def __init__(self, message: str, source: str | None = None) -> None:
        super().__init__(message)
        self.source = source
