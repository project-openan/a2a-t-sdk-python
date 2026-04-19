from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class NegotiationModelsRuntimeTest(unittest.TestCase):
    def test_negotiation_context_to_context_json_uses_protocol_field_names(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.models import NegotiationContext

        context = NegotiationContext(
            negotiation_type=NegotiationType.INFORMATION,
            negotiation_id="neg-1",
            role=NegotiationRole.CLIENT,
            round=2,
            status=NegotiationStatus.IN_PROGRESS,
            extra={"x": "y"},
        )

        self.assertEqual(
            context.to_context_json(),
            {
                "negotiationType": "information",
                "negotiationId": "neg-1",
                "role": "client",
                "round": 2,
                "status": "in-progress",
                "extra": {"x": "y"},
            },
        )

    def test_receive_negotiation_result_to_public_dict_uses_public_field_names(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.models import NegotiationContext, ReceiveNegotiationResult

        result = ReceiveNegotiationResult(
            context=NegotiationContext(
                negotiation_type=NegotiationType.CLARIFICATION,
                negotiation_id="neg-2",
                role=NegotiationRole.SERVER,
                round=1,
                status=NegotiationStatus.IN_PROGRESS,
                extra={},
            ),
            need_response=True,
            facts={"clarificationItems": []},
            message="Please clarify the intent.",
        )

        self.assertEqual(
            result.to_public_dict(),
            {
                "context": {
                    "negotiationType": "clarification",
                    "negotiationId": "neg-2",
                    "role": "server",
                    "round": 1,
                    "status": "in-progress",
                    "extra": {},
                },
                "needResponse": True,
                "facts": {"clarificationItems": []},
                "message": "Please clarify the intent.",
            },
        )

    def test_context_factory_generates_start_and_next_context(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.runtime.negotiation_context_factory import NegotiationContextFactory

        factory = NegotiationContextFactory(id_factory=lambda: "neg-generated")

        start = factory.create_start(
            negotiation_type=NegotiationType.FEASIBILITY,
            role=NegotiationRole.CLIENT,
        )
        next_context = factory.create_next(previous=start, status=NegotiationStatus.AGREED)

        self.assertEqual(start.negotiation_id, "neg-generated")
        self.assertEqual(start.round, 1)
        self.assertEqual(start.status, NegotiationStatus.IN_PROGRESS)
        self.assertEqual(next_context.negotiation_id, "neg-generated")
        self.assertEqual(next_context.role, NegotiationRole.CLIENT)
        self.assertEqual(next_context.round, 2)
        self.assertEqual(next_context.status, NegotiationStatus.AGREED)

    def test_map_builder_emits_required_keys_and_optional_task_prompt(self) -> None:
        from a2a_t.negotiation.common.constants import (
            NEGOTIATION_CONTEXT_KEY,
            NEGOTIATION_TEXT_KEY,
            TASK_PROMPT_KEY,
        )
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.models import NegotiationContext
        from a2a_t.negotiation.runtime.negotiation_map_builder import NegotiationMapBuilder

        builder = NegotiationMapBuilder()
        context = NegotiationContext(
            negotiation_type=NegotiationType.INFORMATION,
            negotiation_id="neg-3",
            role=NegotiationRole.SERVER,
            round=4,
            status=NegotiationStatus.AGREED,
            extra={},
        )

        payload = builder.build(
            prompt_text="negotiation prompt",
            context=context,
            final_task_prompt="final task prompt",
        )

        self.assertEqual(payload[NEGOTIATION_TEXT_KEY], "negotiation prompt")
        self.assertEqual(payload[NEGOTIATION_CONTEXT_KEY]["negotiationId"], "neg-3")
        self.assertEqual(payload[TASK_PROMPT_KEY], "final task prompt")
