from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class NegotiationModelsRuntimeTest(unittest.TestCase):
    def test_negotiation_context_to_conetxt_uses_protocol_field_names(self) -> None:
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
            context.to_conetxt(),
            {
                "negotiationType": "information",
                "negotiationId": "neg-1",
                "role": "client",
                "round": 2,
                "status": "in-progress",
                "extra": {"x": "y"},
            },
        )

    def test_receive_result_keeps_internal_receive_fields(self) -> None:
        from a2a_t.negotiation.common.models import ReceiveResult

        result = ReceiveResult(
            need_response=True,
            facts={"clarificationItems": []},
            message="Please clarify the intent.",
        )

        self.assertTrue(result.need_response)
        self.assertEqual(result.facts, {"clarificationItems": []})
        self.assertEqual(result.message, "Please clarify the intent.")

    def test_negotiation_context_from_conetxt_uses_protocol_field_names(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.models import NegotiationContext

        context = NegotiationContext.from_conetxt(
            {
                "negotiationType": "information",
                "negotiationId": "neg-3",
                "role": "server",
                "round": 4,
                "status": "agreed",
                "extra": {},
            }
        )

        self.assertEqual(context.negotiation_type, NegotiationType.INFORMATION)
        self.assertEqual(context.negotiation_id, "neg-3")
        self.assertEqual(context.role, NegotiationRole.SERVER)
        self.assertEqual(context.round, 4)
        self.assertEqual(context.status, NegotiationStatus.AGREED)
