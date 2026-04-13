from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PromptComplianceResult:
    """Unified compliance execution result."""

    passed: bool
    stage: str
    extracted_slots: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
