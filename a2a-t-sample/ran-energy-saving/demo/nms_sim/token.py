"""Session-token endpoint of the simulator."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from demo.nms_sim.config import SimConfig


@dataclass(frozen=True)
class _IssuedToken:
    """One issued session token and its expiry."""

    user: str
    expires_at: float


class TokenStore:
    """In-memory session-token issuer of the simulator."""

    def __init__(self, config: SimConfig) -> None:
        """Create one empty store."""
        self._config = config
        self._tokens: dict[str, _IssuedToken] = {}

    def issue(self, *, user: str, value: str) -> str | None:
        """Issue a token for the configured credentials, or ``None``."""
        if user != self._config.token_user or value != self._config.token_value:
            return None
        token = f"sim-{uuid.uuid4().hex}"
        self._tokens[token] = _IssuedToken(
            user=user,
            expires_at=time.monotonic() + self._config.token_ttl_seconds,
        )
        return token

    def valid(self, token: str) -> bool:
        """Return whether ``token`` is live."""
        issued = self._tokens.get(token)
        if issued is None:
            return False
        if time.monotonic() >= issued.expires_at:
            self._tokens.pop(token, None)
            return False
        return True
