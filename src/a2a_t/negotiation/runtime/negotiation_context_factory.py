from __future__ import annotations

import uuid

from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
from a2a_t.negotiation.common.models import NegotiationContext


class NegotiationContextFactory:
    def __init__(self, *, id_factory: object | None = None) -> None:
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def create_start(self, *, negotiation_type: NegotiationType, role: NegotiationRole) -> NegotiationContext:
        return NegotiationContext(
            negotiation_type=negotiation_type,
            negotiation_id=str(self._id_factory()),
            role=role,
            round=1,
            status=NegotiationStatus.IN_PROGRESS,
            extra={},
        )

    def create_next(self, *, previous: NegotiationContext, status: NegotiationStatus) -> NegotiationContext:
        return NegotiationContext(
            negotiation_type=previous.negotiation_type,
            negotiation_id=previous.negotiation_id,
            role=previous.role,
            round=previous.round + 1,
            status=status,
            extra=dict(previous.extra),
        )
