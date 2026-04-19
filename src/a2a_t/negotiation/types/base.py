from __future__ import annotations

from a2a_t.negotiation.common.enums import NegotiationStatus
from a2a_t.negotiation.common.models import ContinueResult, NegotiationContext, NegotiationRecord, ReceiveResult, StartNegotiationInput
from a2a_t.negotiation.rendering.negotiation_prompt_renderer import NegotiationPromptRenderer


class BaseNegotiationType:
    def __init__(self, *, prompt_renderer: NegotiationPromptRenderer) -> None:
        self._prompt_renderer = prompt_renderer

    def render_start_prompt(self, *, input: StartNegotiationInput, context: NegotiationContext) -> str:
        return self._prompt_renderer.render_start(input=input, context=context)

    def process_received_message(
        self,
        *,
        message_payload: dict[str, object],
        context: NegotiationContext,
        record: NegotiationRecord | None,
    ) -> ReceiveResult:
        facts = message_payload.get("facts", {})
        if not isinstance(facts, dict):
            facts = {}
        message = message_payload.get("contentText", "")
        if not isinstance(message, str):
            message = ""
        return ReceiveResult(
            need_response=context.status == NegotiationStatus.IN_PROGRESS,
            facts=dict(facts),
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
        facts: dict[str, object] = {}
        if record.last_receive_result is not None:
            facts = dict(record.last_receive_result.facts)
        return ContinueResult(
            prompt_text=self._prompt_renderer.render_continue(
                negotiation_type=context.negotiation_type,
                context=context,
                status=status,
                content_text=content_text,
                facts=facts,
            ),
            final_task_prompt=None,
        )
