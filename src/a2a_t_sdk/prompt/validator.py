"""Prompt validation engine."""

from __future__ import annotations

from typing import Any
from a2a_t_sdk.prompt.models import PromptTemplate


class ValidationResult:
    """Result of template validation."""

    def __init__(self, valid: bool, errors: list[str] | None = None) -> None:
        self.valid = valid
        self.errors = errors or []


class PromptValidator:
    """Validates prompt templates and parameters."""

    def validate_template(self, template: PromptTemplate) -> ValidationResult:
        """Validate a template's structure."""
        errors: list[str] = []
        if not template.name:
            errors.append("Template name is required")
        if not template.content:
            errors.append("Template content is required")
        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def validate_params(
        self, template: PromptTemplate, params: dict[str, Any]
    ) -> ValidationResult:
        """Validate parameters against a template."""
        errors: list[str] = []
        missing = set(template.parameters) - set(params.keys())
        if missing:
            errors.append(f"Missing required parameters: {', '.join(missing)}")
        extra = set(params.keys()) - set(template.parameters)
        if extra:
            errors.append(f"Unknown parameters: {', '.join(extra)}")
        return ValidationResult(valid=len(errors) == 0, errors=errors)
