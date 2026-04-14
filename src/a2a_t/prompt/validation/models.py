from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


@dataclass(slots=True)
class SlotValidationError:
    slot_name: str
    code: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class SlotValidationResult:
    passed: bool
    slot_errors: list[SlotValidationError]


class GuardrailDecision(str, Enum):
    """Unified guardrail decision semantics."""

    ALLOW = "allow"
    BLOCK = "block"
    MASK = "mask"
    REVIEW = "review"


@dataclass(slots=True)
class GuardrailRequest:
    """Normalized guardrail input request."""

    text: str
    metadata: dict[str, object] | None = None
    policy_id: str | None = None


@dataclass(slots=True)
class GuardrailResult:
    passed: bool
    decision: GuardrailDecision = GuardrailDecision.ALLOW
    category: str | None = None
    reason: str | None = None
    raw_response: dict[str, object] | None = None
    provider: str | None = None
    policy_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
