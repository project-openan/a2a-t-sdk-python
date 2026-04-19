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
        message: str,
        context: NegotiationContext,
        record: NegotiationRecord | None,
    ) -> ReceiveResult:
        if context.status in {NegotiationStatus.AGREED, NegotiationStatus.REJECTED}:
            return ReceiveResult(
                need_response=False,
                facts={},
                message=message,
            )

        if self._prompt_checker is None:
            return super().process_received_message(
                message=message,
                context=context,
                record=None,
            )

        compliance_result = self._prompt_checker.check(
            processed_prompt_text=message,
            request_metadata=None,
        )
        if compliance_result.need_negotiation and compliance_result.negotiation_input is not None:
            return ReceiveResult(
                need_response=True,
                facts={},
                message=compliance_result.error_message or message,
            )
        if not compliance_result.passed:
            return ReceiveResult(
                need_response=True,
                facts={},
                message=compliance_result.error_message or "Task prompt validation failed.",
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
            message=content_text,
        )
        final_task_prompt = content_text if status == NegotiationStatus.AGREED else None
        return ContinueResult(
            prompt_text=prompt_text,
            final_task_prompt=final_task_prompt,
        )
