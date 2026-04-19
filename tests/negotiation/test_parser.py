from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class NegotiationParserTest(unittest.TestCase):
    def test_parse_context_returns_negotiation_context(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.handling.negotiation_parser import NegotiationParser

        parser = NegotiationParser()

        context = parser.parse_context(
            {
                "negotiationType": "information",
                "negotiationId": "neg-1",
                "role": "client",
                "round": 3,
                "status": "in-progress",
                "extra": {},
            }
        )

        self.assertEqual(context.negotiation_type, NegotiationType.INFORMATION)
        self.assertEqual(context.negotiation_id, "neg-1")
        self.assertEqual(context.role, NegotiationRole.CLIENT)
        self.assertEqual(context.round, 3)
        self.assertEqual(context.status, NegotiationStatus.IN_PROGRESS)

    def test_parse_context_rejects_invalid_root_fields(self) -> None:
        from a2a_t.negotiation.common.exceptions import NegotiationContextError
        from a2a_t.negotiation.handling.negotiation_parser import NegotiationParser

        parser = NegotiationParser()

        with self.assertRaises(NegotiationContextError):
            parser.parse_context(
                {
                    "negotiationType": "unknown",
                    "negotiationId": "neg-1",
                    "role": "client",
                    "round": 0,
                    "status": "in-progress",
                    "extra": {},
                }
            )

    def test_parse_message_payload_extracts_negotiation_json_block(self) -> None:
        from a2a_t.negotiation.handling.negotiation_parser import NegotiationParser

        parser = NegotiationParser()

        payload = parser.parse_message_payload(
            "Please review the negotiation.\n\n```negotiation-json\n{\"facts\": {\"missingFields\": [\"site\"]}, \"contentText\": \"latest prompt\"}\n```"
        )

        self.assertEqual(
            payload,
            {
                "facts": {"missingFields": ["site"]},
                "contentText": "latest prompt",
            },
        )

    def test_parse_message_payload_rejects_missing_block(self) -> None:
        from a2a_t.negotiation.common.exceptions import NegotiationParseError
        from a2a_t.negotiation.handling.negotiation_parser import NegotiationParser

        parser = NegotiationParser()

        with self.assertRaises(NegotiationParseError):
            parser.parse_message_payload("plain text only")
