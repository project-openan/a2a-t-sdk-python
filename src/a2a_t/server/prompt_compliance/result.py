from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from a2a_t.prompt.validation.models import SlotValidationError


@dataclass
class PromptComplianceResult:
    """Unified compliance execution result."""

    passed: bool
    stage: str
    extracted_slots: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    slot_errors: list[SlotValidationError] | None = None
    need_negotiation: bool = False
    negotiation_input: dict[str, Any] | None = None
