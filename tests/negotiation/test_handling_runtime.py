from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class NegotiationHandlingRuntimeTest(unittest.TestCase):
    def _clarification_registry(self):
        from a2a_t.negotiation.rendering.negotiation_prompt_renderer import NegotiationPromptRenderer
        from a2a_t.negotiation.runtime.negotiation_type_registry import NegotiationTypeRegistry
        from a2a_t.negotiation.types.clarification import ClarificationNegotiationType

        return NegotiationTypeRegistry(
            {
                "clarification": ClarificationNegotiationType(prompt_renderer=NegotiationPromptRenderer()),
            }
        )

    def test_handler_start_returns_fixed_key_map_and_saves_record(self) -> None:
        from a2a_t.negotiation.common.constants import NEGOTIATION_CONTEXT_KEY, NEGOTIATION_TEXT_KEY
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationType
        from a2a_t.negotiation.common.models import StartNegotiationInput
        from a2a_t.negotiation.runtime.negotiation_context_factory import NegotiationContextFactory
        from a2a_t.negotiation.runtime.negotiation_map_builder import NegotiationMapBuilder
        from a2a_t.negotiation.handling.negotiation_handler import NegotiationHandler
        from a2a_t.negotiation.handling.negotiation_parser import NegotiationParser
        from a2a_t.negotiation.store.in_memory import InMemoryNegotiationStateStore

        store = InMemoryNegotiationStateStore()
        handler = NegotiationHandler(
            parser=NegotiationParser(),
            context_factory=NegotiationContextFactory(id_factory=lambda: "neg-start"),
            type_registry=self._clarification_registry(),
            map_builder=NegotiationMapBuilder(),
            store=store,
            now_factory=lambda: "2026-04-18T00:00:00Z",
        )

        payload = handler.start(
            input=StartNegotiationInput(
                type=NegotiationType.CLARIFICATION,
                content_text="Please clarify the request.",
                facts={"clarificationItems": [{"name": "intent"}]},
            ),
            role=NegotiationRole.CLIENT,
        )

        self.assertIn(NEGOTIATION_TEXT_KEY, payload)
        self.assertEqual(payload[NEGOTIATION_CONTEXT_KEY]["negotiationId"], "neg-start")
        self.assertIsNotNone(store.get("neg-start"))

    def test_handler_receive_allows_first_round_without_existing_record(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.handling.negotiation_handler import NegotiationHandler
        from a2a_t.negotiation.handling.negotiation_parser import NegotiationParser
        from a2a_t.negotiation.runtime.negotiation_context_factory import NegotiationContextFactory
        from a2a_t.negotiation.runtime.negotiation_map_builder import NegotiationMapBuilder
        from a2a_t.negotiation.store.in_memory import InMemoryNegotiationStateStore

        handler = NegotiationHandler(
            parser=NegotiationParser(),
            context_factory=NegotiationContextFactory(id_factory=lambda: "unused"),
            type_registry=self._clarification_registry(),
            map_builder=NegotiationMapBuilder(),
            store=InMemoryNegotiationStateStore(),
            now_factory=lambda: "2026-04-18T00:00:00Z",
        )

        result = handler.receive(
            message=(
                "Clarify the request.\n\n"
                "```negotiation-json\n"
                "{\"facts\": {\"clarificationItems\": [{\"name\": \"intent\"}]}, \"contentText\": \"Clarify intent\"}\n"
                "```"
            ),
            context_json={
                "negotiationType": "clarification",
                "negotiationId": "neg-receive",
                "role": "client",
                "round": 1,
                "status": "in-progress",
                "extra": {},
            },
        )

        self.assertTrue(result.need_response)
        self.assertEqual(result.context.negotiation_type, NegotiationType.CLARIFICATION)
        self.assertEqual(result.context.role, NegotiationRole.CLIENT)
        self.assertEqual(result.context.status, NegotiationStatus.IN_PROGRESS)
        self.assertEqual(result.facts["clarificationItems"][0]["name"], "intent")

    def test_handler_continue_returns_map_with_incremented_round(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.models import ContinueNegotiationInput, NegotiationContext, NegotiationRecord, ReceiveResult
        from a2a_t.negotiation.handling.negotiation_handler import NegotiationHandler
        from a2a_t.negotiation.handling.negotiation_parser import NegotiationParser
        from a2a_t.negotiation.runtime.negotiation_context_factory import NegotiationContextFactory
        from a2a_t.negotiation.runtime.negotiation_map_builder import NegotiationMapBuilder
        from a2a_t.negotiation.store.in_memory import InMemoryNegotiationStateStore

        store = InMemoryNegotiationStateStore()
        context = NegotiationContext(
            negotiation_type=NegotiationType.CLARIFICATION,
            negotiation_id="neg-continue",
            role=NegotiationRole.CLIENT,
            round=1,
            status=NegotiationStatus.IN_PROGRESS,
            extra={},
        )
        store.save(
            NegotiationRecord(
                context=context,
                last_message="old",
                last_receive_result=ReceiveResult(need_response=True, facts={"clarificationItems": []}),
                last_continue_result=None,
                last_task_prompt=None,
                created_at="t1",
                updated_at="t1",
            )
        )
        handler = NegotiationHandler(
            parser=NegotiationParser(),
            context_factory=NegotiationContextFactory(id_factory=lambda: "unused"),
            type_registry=self._clarification_registry(),
            map_builder=NegotiationMapBuilder(),
            store=store,
            now_factory=lambda: "2026-04-18T00:00:01Z",
        )

        payload = handler.continue_(
            input=ContinueNegotiationInput(
                context=context,
                status=NegotiationStatus.IN_PROGRESS,
                content_text="Here is the clarification.",
            )
        )

        self.assertEqual(
            payload["https://github.com/a2aproject/telecommunication/extensions/DATA-NEGOTIATION-T/v1"]["round"],
            2,
        )

    def test_handler_receive_terminal_message_returns_need_response_false(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.models import NegotiationContext, NegotiationRecord
        from a2a_t.negotiation.handling.negotiation_handler import NegotiationHandler
        from a2a_t.negotiation.handling.negotiation_parser import NegotiationParser
        from a2a_t.negotiation.runtime.negotiation_context_factory import NegotiationContextFactory
        from a2a_t.negotiation.runtime.negotiation_map_builder import NegotiationMapBuilder
        from a2a_t.negotiation.store.in_memory import InMemoryNegotiationStateStore

        store = InMemoryNegotiationStateStore()
        store.save(
            NegotiationRecord(
                context=NegotiationContext(
                    negotiation_type=NegotiationType.CLARIFICATION,
                    negotiation_id="neg-terminal",
                    role=NegotiationRole.CLIENT,
                    round=1,
                    status=NegotiationStatus.IN_PROGRESS,
                    extra={},
                ),
                last_message="old",
                last_receive_result=None,
                last_continue_result=None,
                last_task_prompt=None,
                created_at="t1",
                updated_at="t1",
            )
        )
        handler = NegotiationHandler(
            parser=NegotiationParser(),
            context_factory=NegotiationContextFactory(id_factory=lambda: "unused"),
            type_registry=self._clarification_registry(),
            map_builder=NegotiationMapBuilder(),
            store=store,
            now_factory=lambda: "2026-04-18T00:00:00Z",
        )

        result = handler.receive(
            message=(
                "Clarify the request.\n\n"
                "```negotiation-json\n"
                "{\"facts\": {\"clarificationItems\": [{\"name\": \"intent\"}]}, \"contentText\": \"Clarify intent\"}\n"
                "```"
            ),
            context_json={
                "negotiationType": "clarification",
                "negotiationId": "neg-terminal",
                "role": "client",
                "round": 2,
                "status": "agreed",
                "extra": {},
            },
        )

        self.assertFalse(result.need_response)
