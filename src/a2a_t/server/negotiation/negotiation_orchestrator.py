from __future__ import annotations

from a2a_t.negotiation.common.enums import NegotiationRole
from a2a_t.negotiation.runtime.base_negotiation_orchestrator import BaseNegotiationOrchestrator


class NegotiationOrchestrator(BaseNegotiationOrchestrator):
    def __init__(self, *, handler) -> None:
        super().__init__(
            handler=handler,
            role=NegotiationRole.SERVER,
        )
