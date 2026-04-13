from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SlotValidationError:
    slot_name: str
    code: str
    message: str


@dataclass(slots=True)
class SlotValidationResult:
    passed: bool
    slot_errors: list[SlotValidationError]


@dataclass(slots=True)
class GuardrailResult:
    passed: bool
    error_code: str | None
    error_message: str | None
