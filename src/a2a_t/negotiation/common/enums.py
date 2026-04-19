from __future__ import annotations

from enum import Enum


class NegotiationType(str, Enum):
    INFORMATION = "information"
    CLARIFICATION = "clarification"
    FEASIBILITY = "feasibility"
    FULFILLMENT = "fulfillment"


class NegotiationRole(str, Enum):
    CLIENT = "client"
    SERVER = "server"


class NegotiationStatus(str, Enum):
    IN_PROGRESS = "in-progress"
    AGREED = "agreed"
    REJECTED = "rejected"
