from __future__ import annotations

from datetime import datetime, timezone
import uuid

from a2a_t.negotiation.common.constants import (
    MAX_IN_PROGRESS_NEGOTIATION_ROUND,
    NEGOTIATION_CONTEXT_KEY,
    NEGOTIATION_TEXT_KEY,
    TASK_PROMPT_KEY,
)
from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
from a2a_t.negotiation.common.exceptions import NegotiationInputError, NegotiationStateError, NegotiationTerminalStateError
from a2a_t.negotiation.common.models import ContinueNegotiationInput, NegotiationContext, NegotiationRecord, ReceiveResult, StartNegotiationInput
from a2a_t.negotiation.store.base import NegotiationStateStore


class NegotiationHandler:
    def __init__(
        self,
        *,
        negotiation_types: dict[NegotiationType, object],
        store: NegotiationStateStore,
    ) -> None:
        self._negotiation_types = dict(negotiation_types)
        self._store = store

    def start(self, *, input: StartNegotiationInput, role: NegotiationRole) -> dict[str, object]:
        negotiation_type_key = self._normalize_negotiation_type(input.type)
        context = self._create_start_context(
            negotiation_type=negotiation_type_key,
            role=role,
        )
        negotiation_type = self._get_negotiation_type(negotiation_type_key)
        prompt_text = negotiation_type.render_start_prompt(input=input, context=context)
        now = self._utc_now()
        self._store.save(
            NegotiationRecord(
                context=context,
                last_message=prompt_text,
                last_receive_result=None,
                last_continue_result=None,
                last_task_prompt=None,
                created_at=now,
                updated_at=now,
            )
        )
        return self._build_result_map(
            prompt_text=prompt_text,
            context=context,
        )

    def receive(self, *, message: str, context: dict[str, object]) -> dict[str, object]:
        context = NegotiationContext.from_context(context)
        record = self._store.get(context.negotiation_id)
        if record is None:
            if context.round != 1:
                raise NegotiationStateError("Negotiation record is missing for non-initial round.")
            now = self._utc_now()
            record = NegotiationRecord(
                context=context,
                last_message=None,
                last_receive_result=None,
                last_continue_result=None,
                last_task_prompt=None,
                created_at=now,
                updated_at=now,
            )
        elif record.context.status in {NegotiationStatus.AGREED, NegotiationStatus.REJECTED}:
            raise NegotiationTerminalStateError("Cannot receive a terminal negotiation again.")
        elif context.round != record.context.round + 1:
            raise NegotiationStateError("Incoming negotiation round is inconsistent with local state.")
        elif (
            context.negotiation_type != record.context.negotiation_type
            or context.role != record.context.role
        ):
            raise NegotiationStateError("Incoming negotiation context is inconsistent with local state.")

        if context.status == NegotiationStatus.IN_PROGRESS and context.round >= MAX_IN_PROGRESS_NEGOTIATION_ROUND:
            receive_result = ReceiveResult(
                need_response=True,
                facts={},
                message="Negotiation reached the maximum in-progress round limit. Please reject it.",
            )
            record.context = context
            record.last_message = message
            record.last_receive_result = receive_result
            record.updated_at = self._utc_now()
            self._store.save(record)
            return self._build_receive_result_map(
                context=context,
                receive_result=receive_result,
            )

        negotiation_type = self._get_negotiation_type(context.negotiation_type)
        receive_result = negotiation_type.process_received_message(
            message=message,
            context=context,
            record=record,
        )
        record.context = context
        record.last_message = message
        record.last_receive_result = receive_result
        record.updated_at = self._utc_now()
        self._store.save(record)
        return self._build_receive_result_map(
            context=context,
            receive_result=receive_result,
        )

    def continue_(self, *, input: ContinueNegotiationInput) -> dict[str, object]:
        record = self._store.get(input.context.negotiation_id)
        if record is None:
            raise NegotiationStateError("Negotiation record is missing.")
        if record.context.status in {NegotiationStatus.AGREED, NegotiationStatus.REJECTED}:
            raise NegotiationTerminalStateError("Cannot continue a terminal negotiation.")
        if (
            input.context.negotiation_type != record.context.negotiation_type
            or input.context.negotiation_id != record.context.negotiation_id
            or input.context.role != record.context.role
            or input.context.round != record.context.round
            or input.context.status != record.context.status
        ):
            raise NegotiationStateError("Negotiation continue context is inconsistent with local state.")

        negotiation_type = self._get_negotiation_type(input.context.negotiation_type)
        continue_result = negotiation_type.render_continue_prompt(
            record=record,
            context=input.context,
            status=input.status,
            content_text=input.content_text,
        )
        next_context = self._create_next_context(
            previous=input.context,
            status=input.status,
        )
        record.context = next_context
        record.last_continue_result = continue_result
        record.last_task_prompt = input.content_text
        record.updated_at = self._utc_now()
        self._store.save(record)
        return self._build_result_map(
            prompt_text=continue_result.prompt_text,
            context=next_context,
            final_task_prompt=continue_result.final_task_prompt,
        )

    def _get_negotiation_type(self, negotiation_type: NegotiationType) -> object:
        try:
            return self._negotiation_types[negotiation_type]
        except KeyError as error:
            raise NegotiationInputError(f"Unsupported negotiation type: {negotiation_type.value}") from error

    @staticmethod
    def _normalize_negotiation_type(value: NegotiationType | str) -> NegotiationType:
        try:
            return value if isinstance(value, NegotiationType) else NegotiationType(str(value))
        except ValueError as error:
            raise NegotiationInputError(f"Unsupported negotiation type: {value}") from error

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _create_start_context(*, negotiation_type: NegotiationType, role: NegotiationRole) -> NegotiationContext:
        return NegotiationContext(
            negotiation_type=negotiation_type,
            negotiation_id=str(uuid.uuid4()),
            role=role,
            round=1,
            status=NegotiationStatus.IN_PROGRESS,
            extra={},
        )

    @staticmethod
    def _create_next_context(*, previous: NegotiationContext, status: NegotiationStatus) -> NegotiationContext:
        return NegotiationContext(
            negotiation_type=previous.negotiation_type,
            negotiation_id=previous.negotiation_id,
            role=previous.role,
            round=previous.round + 1,
            status=status,
            extra=dict(previous.extra),
        )

    @staticmethod
    def _build_result_map(
        *,
        prompt_text: str,
        context: NegotiationContext,
        final_task_prompt: str | None = None,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            NEGOTIATION_TEXT_KEY: prompt_text,
            NEGOTIATION_CONTEXT_KEY: context.to_context(),
        }
        if final_task_prompt is not None:
            result[TASK_PROMPT_KEY] = final_task_prompt
        return result

    @staticmethod
    def _build_receive_result_map(
        *,
        context: NegotiationContext,
        receive_result: ReceiveResult,
    ) -> dict[str, object]:
        return {
            "context": context.to_context(),
            "needResponse": receive_result.need_response,
            "facts": dict(receive_result.facts),
            "message": receive_result.message,
        }
