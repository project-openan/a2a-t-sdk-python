"""Human approval gate in front of a network change.

The server execution agent evaluates a plan first. Before applying it, the gate publishes an A2A
``INPUT_REQUIRED`` status and waits for the engineer's approval collected by the Web UI. The gate
is a sample control plane: the A2A task state is real, while the yes/no travels over the local
Web UI -> server HTTP channel (a production deployment would use MIE/OSS).
"""

from __future__ import annotations

import asyncio
from typing import Final

#: Default seconds an approval may wait before it is treated as rejected. The real NMS demo keeps
#: the intent review open for a working day instead of four minutes.
DEFAULT_APPROVAL_TIMEOUT_SECONDS: Final[float] = 86_400.0


class ApprovalRegistry:
    """Per-run waiters of the human approval gate.

    The registry is process-local: the server process hosts it, the approval route resolves the
    waiter, and the apply tool awaits it. A wait times out into a rejection rather than hanging the
    task forever.
    """

    def __init__(self) -> None:
        """Create one empty registry."""
        self._waiters: dict[str, asyncio.Event] = {}
        self._results: dict[str, bool] = {}

    def pending(self, key: str) -> bool:
        """Whether an approval is currently expected for ``key``."""
        return key in self._waiters

    def resolve(self, key: str, approved: bool) -> bool:
        """Resolve one pending approval; returns ``False`` when nothing is waiting."""
        event = self._waiters.get(key)
        if event is None:
            return False
        self._results[key] = bool(approved)
        event.set()
        return True

    async def wait(self, key: str, timeout_seconds: float) -> bool:
        """Wait for the approval of ``key``; ``False`` on rejection or timeout."""
        event = asyncio.Event()
        self._waiters[key] = event
        self._results.pop(key, None)
        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.1, timeout_seconds))
            return self._results.pop(key, False)
        except TimeoutError:
            return False
        finally:
            self._waiters.pop(key, None)
            self._results.pop(key, None)

    def clear(self) -> None:
        """Drop every waiter (used on shutdown)."""
        for event in self._waiters.values():
            event.set()
        self._waiters.clear()
        self._results.clear()
