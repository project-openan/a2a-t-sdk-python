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

    def receive(self, *, message: str, context_json: dict[str, object]):
        self.receive_calls.append({"message": message, "context_json": context_json})

        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.models import NegotiationContext, ReceiveNegotiationResult

        return ReceiveNegotiationResult(
            context=NegotiationContext(
                negotiation_type=NegotiationType.CLARIFICATION,
                negotiation_id="neg-1",
                role=NegotiationRole.CLIENT,
                round=1,
                status=NegotiationStatus.IN_PROGRESS,
                extra={},
            ),
            need_response=True,
            facts={"clarificationItems": []},
            message="Please clarify.",
        )

    def continue_(self, *, input: object) -> dict[str, object]:
        self.continue_calls.append({"input": input})
        return {"continued": True}


class FakeNegotiationParser:
    def __init__(self) -> None:
        self.context_calls: list[dict[str, object]] = []

    def parse_context(self, context_json: dict[str, object]):
        self.context_calls.append(dict(context_json))

        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.models import NegotiationContext

        return NegotiationContext(
            negotiation_type=NegotiationType(str(context_json["negotiationType"])),
            negotiation_id=str(context_json["negotiationId"]),
            role=NegotiationRole(str(context_json["role"])),
            round=int(context_json["round"]),
            status=NegotiationStatus(str(context_json["status"])),
            extra=dict(context_json["extra"]),
        )


class NegotiationOrchestratorTest(unittest.TestCase):
    def test_client_orchestrator_start_negotiation_uses_client_role(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole
        from a2a_t.client.negotiation.negotiation_orchestrator import NegotiationOrchestrator

        handler = FakeNegotiationHandler()
        orchestrator = NegotiationOrchestrator(handler=handler, parser=FakeNegotiationParser())

        result = orchestrator.start_negotiation(
            {
                "type": "clarification",
                "contentText": "Please clarify.",
                "facts": {},
            }
        )

        self.assertEqual(result, {"started": True})
        self.assertEqual(handler.start_calls[0]["role"], NegotiationRole.CLIENT)

    def test_server_orchestrator_start_negotiation_uses_server_role(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole
        from a2a_t.server.negotiation.negotiation_orchestrator import NegotiationOrchestrator

        handler = FakeNegotiationHandler()
        orchestrator = NegotiationOrchestrator(handler=handler, parser=FakeNegotiationParser())

        result = orchestrator.start_negotiation(
            {
                "type": "information",
                "contentText": "Need more information.",
                "facts": {},
            }
        )

        self.assertEqual(result, {"started": True})
        self.assertEqual(handler.start_calls[0]["role"], NegotiationRole.SERVER)

    def test_receive_negotiation_returns_public_dict(self) -> None:
        from a2a_t.client.negotiation.negotiation_orchestrator import NegotiationOrchestrator

        orchestrator = NegotiationOrchestrator(
            handler=FakeNegotiationHandler(),
            parser=FakeNegotiationParser(),
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
        from a2a_t.client.negotiation.negotiation_orchestrator import NegotiationOrchestrator

        handler = FakeNegotiationHandler()
        parser = FakeNegotiationParser()
        orchestrator = NegotiationOrchestrator(
            handler=handler,
            parser=parser,
        )

        result = orchestrator.continue_negotiation(
            {
                "context": {
                    "negotiationType": "clarification",
                    "negotiationId": "neg-1",
                    "role": "client",
                    "round": 1,
                    "status": "in-progress",
                    "extra": {},
                },
                "status": "in-progress",
                "contentText": "Here is the clarification.",
            }
        )

        self.assertEqual(result, {"continued": True})
        self.assertEqual(len(handler.continue_calls), 1)
        self.assertEqual(len(parser.context_calls), 1)
