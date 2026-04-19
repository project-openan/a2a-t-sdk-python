from __future__ import annotations

from a2a_t.negotiation.common.enums import NegotiationStatus
from a2a_t.negotiation.common.models import ContinueResult, NegotiationContext, NegotiationRecord, ReceiveResult, StartNegotiationInput
from a2a_t.negotiation.rendering.negotiation_prompt_renderer import NegotiationPromptRenderer


class BaseNegotiationType:
    def __init__(self, *, prompt_renderer: NegotiationPromptRenderer) -> None:
        self._prompt_renderer = prompt_renderer

    def render_start_prompt(self, *, input: StartNegotiationInput, context: NegotiationContext) -> str:
        return self._prompt_renderer.render_start(
            negotiation_type=input.type,
            message=input.content_text,
        )

    def process_received_message(
        self,
        *,
        message: str,
        context: NegotiationContext,
        record: NegotiationRecord | None,
    ) -> ReceiveResult:
        return ReceiveResult(
            need_response=context.status == NegotiationStatus.IN_PROGRESS,
            facts={},
            message=message,
        )

    def render_continue_prompt(
        self,
        *,
        record: NegotiationRecord,
        context: NegotiationContext,
        status,
        content_text: str,
    ) -> ContinueResult:
        return ContinueResult(
            prompt_text=self._prompt_renderer.render_continue(
                negotiation_type=context.negotiation_type,
                message=content_text,
            ),
            final_task_prompt=None,
        )
