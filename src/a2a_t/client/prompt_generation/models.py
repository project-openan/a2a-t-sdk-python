from __future__ import annotations

from dataclasses import asdict, dataclass

from a2a_t.prompt.validation.models import SlotValidationError


@dataclass(slots=True)
class NormalizedInput:
    input_kind: str
    normalized_input: str


@dataclass(slots=True)
class ValidationResult:
    passed: bool
    slot_errors: list[SlotValidationError]

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "slot_errors": [slot_error.to_dict() for slot_error in self.slot_errors],
        }


@dataclass(slots=True)
class PromptGenerationFailure:
    code: str
    message: str
    stage: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class PromptGenerationResult:
    success: bool
    prompt_text: str | None
    scenario_code: str | None
    language: str
    input_kind: str
    slots: dict[str, str | None]
    validation: ValidationResult
    failure: PromptGenerationFailure | None

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "prompt_text": self.prompt_text,
            "scenario_code": self.scenario_code,
            "language": self.language,
            "input_kind": self.input_kind,
            "slots": dict(self.slots),
            "validation": self.validation.to_dict(),
            "failure": self.failure.to_dict() if self.failure is not None else None,
        }
