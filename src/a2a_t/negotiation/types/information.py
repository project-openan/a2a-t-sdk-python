from __future__ import annotations

from a2a_t.negotiation.common.enums import NegotiationStatus
from a2a_t.negotiation.common.models import ContinueResult, NegotiationContext, NegotiationRecord, ReceiveResult

from .base import BaseNegotiationType


class InformationNegotiationType(BaseNegotiationType):
    _COMPLETE_MESSAGE = "Task prompt is complete."

    def __init__(self, *, prompt_renderer, prompt_checker=None) -> None:
        super().__init__(prompt_renderer=prompt_renderer)
        self._prompt_checker = prompt_checker

    def process_received_message(
        self,
        *,
        message_payload: dict[str, object],
        context: NegotiationContext,
        record: NegotiationRecord | None,
    ) -> ReceiveResult:
        if context.status in {NegotiationStatus.AGREED, NegotiationStatus.REJECTED}:
            return ReceiveResult(
                need_response=False,
                facts={},
                message=str(message_payload.get("contentText", "")),
            )

        if self._prompt_checker is None:
            return super().process_received_message(
                message_payload=message_payload,
                context=context,
                record=record,
            )

        latest_task_prompt = str(message_payload.get("contentText", ""))
        compliance_result = self._prompt_checker.check(
            processed_prompt_text=latest_task_prompt,
            request_metadata=None,
        )
        if compliance_result.need_negotiation and compliance_result.negotiation_input is not None:
            facts = compliance_result.negotiation_input.get("facts", {})
            if not isinstance(facts, dict):
                facts = {}
            return ReceiveResult(
                need_response=True,
                facts=dict(facts),
                message=compliance_result.error_message or str(message_payload.get("contentText", "")),
            )

        return ReceiveResult(
            need_response=True,
            facts={},
            message=self._COMPLETE_MESSAGE,
        )

    def render_continue_prompt(
        self,
        *,
        record: NegotiationRecord,
        context: NegotiationContext,
        status: NegotiationStatus,
        content_text: str,
    ) -> ContinueResult:
        prompt_text = self._prompt_renderer.render_continue(
            negotiation_type=context.negotiation_type,
            context=context,
            status=status,
            content_text=content_text,
            facts={},
        )
        final_task_prompt = content_text if status == NegotiationStatus.AGREED else None
        return ContinueResult(
            prompt_text=prompt_text,
            final_task_prompt=final_task_prompt,
        )
