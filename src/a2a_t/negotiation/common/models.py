from __future__ import annotations

from dataclasses import dataclass

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

    def to_context_json(self) -> dict[str, object]:
        return {
            "negotiationType": self.negotiation_type.value,
            "negotiationId": self.negotiation_id,
            "role": self.role.value,
            "round": self.round,
            "status": self.status.value,
            "extra": dict(self.extra),
        }


@dataclass(slots=True)
class ReceiveNegotiationResult:
    context: NegotiationContext
    need_response: bool
    facts: dict[str, object]
    message: str = ""

    def to_public_dict(self) -> dict[str, object]:
        return {
            "context": self.context.to_context_json(),
            "needResponse": self.need_response,
            "facts": dict(self.facts),
            "message": self.message,
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
