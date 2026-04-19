from __future__ import annotations

from a2a_t.negotiation.common.enums import NegotiationType


class NegotiationPromptRenderer:
    def render(self, *, negotiation_type: NegotiationType, message: str) -> str:
        return message

    def render_start(self, *, negotiation_type: NegotiationType, message: str) -> str:
        return self.render(
            negotiation_type=negotiation_type,
            message=message,
        )

    def render_continue(
        self,
        *,
        negotiation_type: NegotiationType,
        message: str,
    ) -> str:
        return self.render(
            negotiation_type=negotiation_type,
            message=message,
        )
