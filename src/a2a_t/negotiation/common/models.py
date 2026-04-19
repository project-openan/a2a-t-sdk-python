from __future__ import annotations

from dataclasses import dataclass

from .exceptions import NegotiationContextError
from .enums import NegotiationRole, NegotiationStatus, NegotiationType


@dataclass(slots=True)
class StartNegotiationInput:
    type: NegotiationType
    content_text: str
    facts: dict[str, object]


@dataclass(slots=True)
class ContinueNegotiationInput:
    context: "NegotiationContext"
    status: NegotiationStatus
    content_text: str


@dataclass(slots=True)
class NegotiationContext:
    negotiation_type: NegotiationType
    negotiation_id: str
    role: NegotiationRole
    round: int
    status: NegotiationStatus
    extra: dict[str, object]

    @classmethod
    def from_context(cls, context: dict[str, object]) -> "NegotiationContext":
        try:
            negotiation_type = NegotiationType(str(context["negotiationType"]))
            negotiation_id = str(context["negotiationId"])
            role = NegotiationRole(str(context["role"]))
            round_value = int(context["round"])
            status = NegotiationStatus(str(context["status"]))
            extra = context["extra"]
        except (KeyError, TypeError, ValueError) as error:
            raise NegotiationContextError("Invalid negotiation context.") from error

        if not negotiation_id or round_value < 1 or not isinstance(extra, dict):
            raise NegotiationContextError("Invalid negotiation context.")

        return cls(
            negotiation_type=negotiation_type,
            negotiation_id=negotiation_id,
            role=role,
            round=round_value,
            status=status,
            extra=dict(extra),
        )

    def to_context(self) -> dict[str, object]:
        return {
            "negotiationType": self.negotiation_type.value,
            "negotiationId": self.negotiation_id,
            "role": self.role.value,
            "round": self.round,
            "status": self.status.value,
            "extra": dict(self.extra),
        }


@dataclass(slots=True)
class ReceiveResult:
    need_response: bool
    facts: dict[str, object]
    message: str = ""


@dataclass(slots=True)
class ContinueResult:
    prompt_text: str
    final_task_prompt: str | None


@dataclass(slots=True)
class NegotiationRecord:
    context: NegotiationContext
    last_message: str | None
    last_receive_result: ReceiveResult | None
    last_continue_result: ContinueResult | None
    last_task_prompt: str | None
    created_at: object
    updated_at: object
