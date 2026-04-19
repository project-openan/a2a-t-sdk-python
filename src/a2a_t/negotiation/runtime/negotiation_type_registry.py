from __future__ import annotations

from a2a_t.negotiation.common.enums import NegotiationType
from a2a_t.negotiation.common.exceptions import NegotiationInputError


class NegotiationTypeRegistry:
    def __init__(self, types: dict[str | NegotiationType, object]) -> None:
        self._types = {self._normalize_key(key): value for key, value in types.items()}

    def get(self, negotiation_type: str | NegotiationType) -> object:
        normalized_key = self._normalize_key(negotiation_type)
        try:
            return self._types[normalized_key]
        except KeyError as error:
            raise NegotiationInputError(f"Unsupported negotiation type: {normalized_key}") from error

    @staticmethod
    def _normalize_key(value: str | NegotiationType) -> str:
        if isinstance(value, NegotiationType):
            return value.value
        return str(value)
