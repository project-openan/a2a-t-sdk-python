from __future__ import annotations

from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
from a2a_t.negotiation.common.exceptions import NegotiationInputError
from a2a_t.negotiation.common.models import ContinueNegotiationInput, StartNegotiationInput


class BaseNegotiationOrchestrator:
    def __init__(self, *, handler, parser, role: NegotiationRole) -> None:
        self._handler = handler
        self._parser = parser
        self._role = role

    def start_negotiation(self, input: dict[str, object]) -> dict[str, object]:
        start_input = StartNegotiationInput(
            type=self._parse_type(input),
            content_text=self._parse_required_string(input, "contentText"),
            facts=self._parse_optional_dict(input, "facts"),
        )
        return self._handler.start(input=start_input, role=self._role)

    def receive_negotiation(self, message: str, context_json: dict[str, object]) -> dict[str, object]:
        return self._handler.receive(
            message=message,
            context_json=context_json,
        ).to_public_dict()

    def continue_negotiation(self, input: dict[str, object]) -> dict[str, object]:
        context_json = input.get("context")
        if not isinstance(context_json, dict):
            raise NegotiationInputError("Negotiation continue input is missing context.")

        continue_input = ContinueNegotiationInput(
            context=self._parser.parse_context(context_json),
            status=self._parse_status(input),
            content_text=self._parse_required_string(input, "contentText"),
        )
        return self._handler.continue_(input=continue_input)

    @staticmethod
    def _parse_type(input: dict[str, object]) -> NegotiationType:
        try:
            return NegotiationType(str(input["type"]))
        except (KeyError, ValueError) as error:
            raise NegotiationInputError("Negotiation start input has invalid type.") from error

    @staticmethod
    def _parse_status(input: dict[str, object]) -> NegotiationStatus:
        try:
            return NegotiationStatus(str(input["status"]))
        except (KeyError, ValueError) as error:
            raise NegotiationInputError("Negotiation continue input has invalid status.") from error

    @staticmethod
    def _parse_required_string(input: dict[str, object], key: str) -> str:
        value = input.get(key)
        if not isinstance(value, str) or not value:
            raise NegotiationInputError(f"Negotiation input is missing {key}.")
        return value

    @staticmethod
    def _parse_optional_dict(input: dict[str, object], key: str) -> dict[str, object]:
        value = input.get(key, {})
        if not isinstance(value, dict):
            raise NegotiationInputError(f"Negotiation input has invalid {key}.")
        return dict(value)
