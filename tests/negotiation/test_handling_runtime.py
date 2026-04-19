from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class NegotiationHandlingRuntimeTest(unittest.TestCase):
    def _clarification_types(self):
        from a2a_t.negotiation.rendering.negotiation_prompt_renderer import NegotiationPromptRenderer
        from a2a_t.negotiation.common.enums import NegotiationType
        from a2a_t.negotiation.types.clarification import ClarificationNegotiationType

        return {
            NegotiationType.CLARIFICATION: ClarificationNegotiationType(prompt_renderer=NegotiationPromptRenderer()),
        }

    def test_handler_start_returns_fixed_key_map_and_saves_record(self) -> None:
        from a2a_t.negotiation.common.constants import NEGOTIATION_CONTEXT_KEY, NEGOTIATION_TEXT_KEY
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationType
        from a2a_t.negotiation.common.models import StartNegotiationInput
        from a2a_t.negotiation.handling.negotiation_handler import NegotiationHandler
        from a2a_t.negotiation.store.in_memory import InMemoryNegotiationStateStore

        store = InMemoryNegotiationStateStore()
        handler = NegotiationHandler(
            negotiation_types=self._clarification_types(),
            store=store,
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
        self.assertEqual(payload[NEGOTIATION_TEXT_KEY], "Please clarify the request.")
        self.assertTrue(payload[NEGOTIATION_CONTEXT_KEY]["negotiationId"])
        self.assertIsNotNone(store.get(payload[NEGOTIATION_CONTEXT_KEY]["negotiationId"]))

    def test_handler_receive_allows_first_round_without_existing_record(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.handling.negotiation_handler import NegotiationHandler
        from a2a_t.negotiation.store.in_memory import InMemoryNegotiationStateStore

        handler = NegotiationHandler(
            negotiation_types=self._clarification_types(),
            store=InMemoryNegotiationStateStore(),
        )

        result = handler.receive(
            message="Clarify intent",
            context={
                "negotiationType": "clarification",
                "negotiationId": "neg-receive",
                "role": "client",
                "round": 1,
                "status": "in-progress",
                "extra": {},
            },
        )

        self.assertTrue(result["needResponse"])
        self.assertEqual(result["context"]["negotiationType"], NegotiationType.CLARIFICATION.value)
        self.assertEqual(result["context"]["role"], NegotiationRole.CLIENT.value)
        self.assertEqual(result["context"]["status"], NegotiationStatus.IN_PROGRESS.value)
        self.assertEqual(result["facts"], {})
        self.assertEqual(result["message"], "Clarify intent")

    def test_handler_continue_returns_map_with_incremented_round(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.models import ContinueNegotiationInput, NegotiationContext, NegotiationRecord, ReceiveResult
        from a2a_t.negotiation.handling.negotiation_handler import NegotiationHandler
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
            negotiation_types=self._clarification_types(),
            store=store,
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
        self.assertEqual(
            payload["https://github.com/a2aproject/telecommunication/extensions/NEGOTIATION-T"],
            "Here is the clarification.",
        )

    def test_handler_receive_terminal_message_returns_need_response_false(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.models import NegotiationContext, NegotiationRecord
        from a2a_t.negotiation.handling.negotiation_handler import NegotiationHandler
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
            negotiation_types=self._clarification_types(),
            store=store,
        )

        result = handler.receive(
            message="Clarify intent",
            context={
                "negotiationType": "clarification",
                "negotiationId": "neg-terminal",
                "role": "client",
                "round": 2,
                "status": "agreed",
                "extra": {},
            },
        )

        self.assertFalse(result["needResponse"])

    def test_handler_receive_rejects_incoming_round_that_skips_local_progress(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.exceptions import NegotiationStateError
        from a2a_t.negotiation.common.models import NegotiationContext, NegotiationRecord
        from a2a_t.negotiation.handling.negotiation_handler import NegotiationHandler
        from a2a_t.negotiation.store.in_memory import InMemoryNegotiationStateStore

        store = InMemoryNegotiationStateStore()
        store.save(
            NegotiationRecord(
                context=NegotiationContext(
                    negotiation_type=NegotiationType.CLARIFICATION,
                    negotiation_id="neg-round-skip",
                    role=NegotiationRole.CLIENT,
                    round=2,
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
            negotiation_types=self._clarification_types(),
            store=store,
        )

        with self.assertRaises(NegotiationStateError):
            handler.receive(
                message="Clarify intent",
                context={
                    "negotiationType": "clarification",
                    "negotiationId": "neg-round-skip",
                    "role": "client",
                    "round": 4,
                    "status": "in-progress",
                    "extra": {},
                },
            )

    def test_handler_continue_rejects_context_round_mismatch(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.exceptions import NegotiationStateError
        from a2a_t.negotiation.common.models import ContinueNegotiationInput, NegotiationContext, NegotiationRecord
        from a2a_t.negotiation.handling.negotiation_handler import NegotiationHandler
        from a2a_t.negotiation.store.in_memory import InMemoryNegotiationStateStore

        store = InMemoryNegotiationStateStore()
        store.save(
            NegotiationRecord(
                context=NegotiationContext(
                    negotiation_type=NegotiationType.CLARIFICATION,
                    negotiation_id="neg-continue-mismatch",
                    role=NegotiationRole.CLIENT,
                    round=2,
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
            negotiation_types=self._clarification_types(),
            store=store,
        )

        with self.assertRaises(NegotiationStateError):
            handler.continue_(
                input=ContinueNegotiationInput(
                    context=NegotiationContext(
                        negotiation_type=NegotiationType.CLARIFICATION,
                        negotiation_id="neg-continue-mismatch",
                        role=NegotiationRole.CLIENT,
                        round=1,
                        status=NegotiationStatus.IN_PROGRESS,
                        extra={},
                    ),
                    status=NegotiationStatus.IN_PROGRESS,
                    content_text="Here is the clarification.",
                )
            )

    def test_handler_receive_returns_reject_guidance_when_incoming_round_hits_limit(self) -> None:
        from a2a_t.negotiation.common.constants import MAX_IN_PROGRESS_NEGOTIATION_ROUND
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.models import NegotiationContext, NegotiationRecord
        from a2a_t.negotiation.handling.negotiation_handler import NegotiationHandler
        from a2a_t.negotiation.store.in_memory import InMemoryNegotiationStateStore

        store = InMemoryNegotiationStateStore()
        previous_round = MAX_IN_PROGRESS_NEGOTIATION_ROUND - 1
        store.save(
            NegotiationRecord(
                context=NegotiationContext(
                    negotiation_type=NegotiationType.CLARIFICATION,
                    negotiation_id="neg-round-limit",
                    role=NegotiationRole.CLIENT,
                    round=previous_round,
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
            negotiation_types=self._clarification_types(),
            store=store,
        )

        result = handler.receive(
            message="Clarify intent",
            context={
                "negotiationType": "clarification",
                "negotiationId": "neg-round-limit",
                "role": "client",
                "round": MAX_IN_PROGRESS_NEGOTIATION_ROUND,
                "status": "in-progress",
                "extra": {},
            },
        )

        self.assertTrue(result["needResponse"])
        self.assertEqual(result["message"], "Negotiation reached the maximum in-progress round limit. Please reject it.")
