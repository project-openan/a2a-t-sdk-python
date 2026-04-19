from __future__ import annotations

from typing import Protocol

from a2a_t.negotiation.common.models import NegotiationRecord


class NegotiationStateStore(Protocol):
    def get(self, negotiation_id: str) -> NegotiationRecord | None: ...

    def save(self, record: NegotiationRecord) -> None: ...

    def delete(self, negotiation_id: str) -> None: ...

    def cleanup_expired(self) -> bool: ...
