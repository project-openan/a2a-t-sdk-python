from __future__ import annotations

import json
import re

from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
from a2a_t.negotiation.common.exceptions import NegotiationContextError, NegotiationParseError
from a2a_t.negotiation.common.models import NegotiationContext


_NEGOTIATION_JSON_BLOCK_RE = re.compile(r"```negotiation-json\s*(\{.*?\})\s*```", re.DOTALL)


class NegotiationParser:
    def parse_context(self, context_json: dict[str, object]) -> NegotiationContext:
        try:
            negotiation_type = NegotiationType(str(context_json["negotiationType"]))
            negotiation_id = str(context_json["negotiationId"])
            role = NegotiationRole(str(context_json["role"]))
            round_value = int(context_json["round"])
            status = NegotiationStatus(str(context_json["status"]))
            extra = context_json["extra"]
        except (KeyError, TypeError, ValueError) as error:
            raise NegotiationContextError("Invalid negotiation context_json.") from error

        if not negotiation_id or round_value < 1 or not isinstance(extra, dict):
            raise NegotiationContextError("Invalid negotiation context_json.")

        return NegotiationContext(
            negotiation_type=negotiation_type,
            negotiation_id=negotiation_id,
            role=role,
            round=round_value,
            status=status,
            extra=dict(extra),
        )

    def parse_message_payload(self, message: str) -> dict[str, object]:
        if not isinstance(message, str) or not message.strip():
            raise NegotiationParseError("Negotiation message must be a non-empty string.")

        match = _NEGOTIATION_JSON_BLOCK_RE.search(message)
        if match is None:
            raise NegotiationParseError("Negotiation message is missing negotiation-json block.")

        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as error:
            raise NegotiationParseError("Negotiation message contains invalid negotiation-json.") from error

        if not isinstance(payload, dict):
            raise NegotiationParseError("Negotiation message contains invalid negotiation-json.")
        return payload
