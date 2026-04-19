from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class FakeNegotiationHandler:
    def __init__(self) -> None:
        self.start_calls: list[dict[str, object]] = []
        self.receive_calls: list[dict[str, object]] = []
        self.continue_calls: list[dict[str, object]] = []

    def start(self, *, input: object, role: object) -> dict[str, object]:
        self.start_calls.append({"input": input, "role": role})
        return {"started": True}

    def receive(self, *, message: str, context: dict[str, object]):
        self.receive_calls.append({"message": message, "context": context})
        return {
            "context": {
                "negotiationType": "clarification",
                "negotiationId": "neg-1",
                "role": "client",
                "round": 1,
                "status": "in-progress",
                "extra": {},
            },
            "needResponse": True,
            "facts": {"clarificationItems": []},
            "message": "Please clarify.",
        }

    def continue_(self, *, input: object) -> dict[str, object]:
        self.continue_calls.append({"input": input})
        return {"continued": True}


class NegotiationOrchestratorTest(unittest.TestCase):
    def test_client_orchestrator_start_negotiation_uses_client_role(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationType
        from a2a_t.negotiation.common.models import StartNegotiationInput
        from a2a_t.client.negotiation.negotiation_orchestrator import NegotiationOrchestrator

        handler = FakeNegotiationHandler()
        orchestrator = NegotiationOrchestrator(handler=handler)

        result = orchestrator.start_negotiation(
            StartNegotiationInput(
                type=NegotiationType.CLARIFICATION,
                content_text="Please clarify.",
                facts={},
            )
        )

        self.assertEqual(result, {"started": True})
        self.assertEqual(handler.start_calls[0]["role"], NegotiationRole.CLIENT)

    def test_server_orchestrator_start_negotiation_uses_server_role(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationType
        from a2a_t.negotiation.common.models import StartNegotiationInput
        from a2a_t.server.negotiation.negotiation_orchestrator import NegotiationOrchestrator

        handler = FakeNegotiationHandler()
        orchestrator = NegotiationOrchestrator(handler=handler)

        result = orchestrator.start_negotiation(
            StartNegotiationInput(
                type=NegotiationType.INFORMATION,
                content_text="Need more information.",
                facts={},
            )
        )

        self.assertEqual(result, {"started": True})
        self.assertEqual(handler.start_calls[0]["role"], NegotiationRole.SERVER)

    def test_receive_negotiation_returns_public_dict(self) -> None:
        from a2a_t.client.negotiation.negotiation_orchestrator import NegotiationOrchestrator

        orchestrator = NegotiationOrchestrator(
            handler=FakeNegotiationHandler(),
        )

        result = orchestrator.receive_negotiation(
            "message",
            {
                "negotiationType": "clarification",
                "negotiationId": "neg-1",
                "role": "client",
                "round": 1,
                "status": "in-progress",
                "extra": {},
            },
        )

        self.assertEqual(result["context"]["negotiationId"], "neg-1")
        self.assertEqual(result["needResponse"], True)
        self.assertEqual(result["message"], "Please clarify.")

    def test_continue_negotiation_returns_handler_payload(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationStatus
        from a2a_t.negotiation.common.models import ContinueNegotiationInput, NegotiationContext
        from a2a_t.client.negotiation.negotiation_orchestrator import NegotiationOrchestrator

        handler = FakeNegotiationHandler()
        orchestrator = NegotiationOrchestrator(
            handler=handler,
        )

        result = orchestrator.continue_negotiation(
            ContinueNegotiationInput(
                context=NegotiationContext.from_context(
                    {
                        "negotiationType": "clarification",
                        "negotiationId": "neg-1",
                        "role": "client",
                        "round": 1,
                        "status": "in-progress",
                        "extra": {},
                    }
                ),
                status=NegotiationStatus.IN_PROGRESS,
                content_text="Here is the clarification.",
            )
        )

        self.assertEqual(result, {"continued": True})
        self.assertEqual(len(handler.continue_calls), 1)
        self.assertEqual(handler.continue_calls[0]["input"].context.negotiation_id, "neg-1")
