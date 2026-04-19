from __future__ import annotations


class NegotiationInputError(Exception):
    """Raised when negotiation input is invalid."""


class NegotiationContextError(Exception):
    """Raised when negotiation context is invalid."""


class NegotiationStateError(Exception):
    """Raised when local negotiation state is inconsistent."""


class NegotiationTerminalStateError(Exception):
    """Raised when a terminal negotiation is continued."""


class NegotiationParseError(Exception):
    """Raised when a negotiation message cannot be parsed."""
