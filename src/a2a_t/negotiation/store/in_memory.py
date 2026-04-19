from __future__ import annotations

from a2a_t.negotiation.common.models import NegotiationRecord


class InMemoryNegotiationStateStore:
    def __init__(self) -> None:
        self._records: dict[str, NegotiationRecord] = {}

    def get(self, negotiation_id: str) -> NegotiationRecord | None:
        return self._records.get(negotiation_id)

    def save(self, record: NegotiationRecord) -> None:
        self._records[record.context.negotiation_id] = record

    def delete(self, negotiation_id: str) -> None:
        self._records.pop(negotiation_id, None)

    def cleanup_expired(self) -> bool:
        return True
