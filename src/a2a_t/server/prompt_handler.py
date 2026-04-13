"""Prompt validation middleware for A2A server."""

from __future__ import annotations

from typing import Any


class PromptHandler:
    """Middleware for validating prompt requests."""

    def __init__(self, validator: Any | None = None) -> None:
        self._validator = validator

    def validate(self, template_name: str, params: dict[str, Any]) -> tuple[bool, str]:
        """Validate request against template rules."""
        if self._validator is None:
            return True, ""

        processed_prompt_text = str(params.get("processed_prompt_text", ""))
        result = self._validator.check(
            processed_prompt_text=processed_prompt_text,
            request_metadata={"template_name": template_name},
        )
        return result.passed, result.error_message or ""

    def process(self, task_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Process and validate the incoming task."""
        if self._validator is None:
            return {"passed": True, "task_id": task_id, "params": params}

        processed_prompt_text = str(params.get("processed_prompt_text", ""))
        result = self._validator.check(
            processed_prompt_text=processed_prompt_text,
            request_metadata={"task_id": task_id},
        )
        return {
            "passed": result.passed,
            "stage": result.stage,
            "extracted_slots": result.extracted_slots,
            "error_code": result.error_code,
            "error_message": result.error_message,
        }
