from __future__ import annotations

from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus
from a2a_t.negotiation.common.exceptions import NegotiationStateError, NegotiationTerminalStateError
from a2a_t.negotiation.common.models import ContinueNegotiationInput, NegotiationRecord, ReceiveNegotiationResult, StartNegotiationInput
from a2a_t.negotiation.handling.negotiation_parser import NegotiationParser
from a2a_t.negotiation.runtime.negotiation_context_factory import NegotiationContextFactory
from a2a_t.negotiation.runtime.negotiation_map_builder import NegotiationMapBuilder
from a2a_t.negotiation.runtime.negotiation_type_registry import NegotiationTypeRegistry
from a2a_t.negotiation.store.in_memory import InMemoryNegotiationStateStore


class NegotiationHandler:
    def __init__(
        self,
        *,
        parser: NegotiationParser,
        context_factory: NegotiationContextFactory,
        type_registry: NegotiationTypeRegistry,
        map_builder: NegotiationMapBuilder,
        store: InMemoryNegotiationStateStore,
        now_factory,
    ) -> None:
        self._parser = parser
        self._context_factory = context_factory
        self._type_registry = type_registry
        self._map_builder = map_builder
        self._store = store
        self._now_factory = now_factory

    def start(self, *, input: StartNegotiationInput, role: NegotiationRole) -> dict[str, object]:
        context = self._context_factory.create_start(
            negotiation_type=input.type,
            role=role,
        )
        negotiation_type = self._type_registry.get(input.type)
        prompt_text = negotiation_type.render_start_prompt(input=input, context=context)
        now = self._now_factory()
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
        return self._map_builder.build(
            prompt_text=prompt_text,
            context=context,
        )

    def receive(self, *, message: str, context_json: dict[str, object]) -> ReceiveNegotiationResult:
        context = self._parser.parse_context(context_json)
        message_payload = self._parser.parse_message_payload(message)
        record = self._store.get(context.negotiation_id)
        if record is None:
            if context.round != 1:
                raise NegotiationStateError("Negotiation record is missing for non-initial round.")
            now = self._now_factory()
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

        negotiation_type = self._type_registry.get(context.negotiation_type)
        receive_result = negotiation_type.process_received_message(
            message_payload=message_payload,
            context=context,
            record=record,
        )
        record.context = context
        record.last_message = message
        record.last_receive_result = receive_result
        record.updated_at = self._now_factory()
        self._store.save(record)
        return ReceiveNegotiationResult(
            context=context,
            need_response=receive_result.need_response,
            facts=dict(receive_result.facts),
            message=receive_result.message,
        )

    def continue_(self, *, input: ContinueNegotiationInput) -> dict[str, object]:
        record = self._store.get(input.context.negotiation_id)
        if record is None:
            raise NegotiationStateError("Negotiation record is missing.")
        if record.context.status in {NegotiationStatus.AGREED, NegotiationStatus.REJECTED}:
            raise NegotiationTerminalStateError("Cannot continue a terminal negotiation.")

        negotiation_type = self._type_registry.get(input.context.negotiation_type)
        continue_result = negotiation_type.render_continue_prompt(
            record=record,
            context=input.context,
            status=input.status,
            content_text=input.content_text,
        )
        next_context = self._context_factory.create_next(
            previous=input.context,
            status=input.status,
        )
        record.context = next_context
        record.last_continue_result = continue_result
        record.last_task_prompt = input.content_text
        record.updated_at = self._now_factory()
        self._store.save(record)
        return self._map_builder.build(
            prompt_text=continue_result.prompt_text,
            context=next_context,
            final_task_prompt=continue_result.final_task_prompt,
        )
