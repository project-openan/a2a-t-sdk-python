"""Session storage abstractions for llm chat support."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from a2a_t.llm.base import ChatSession


class SessionStore(Protocol):
    def get(self, session_id: str) -> ChatSession | None: ...
    def save(self, session: ChatSession) -> None: ...
    def reset(self, session_id: str) -> ChatSession | None: ...
    def delete(self, session_id: str) -> None: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}

    def get(self, session_id: str) -> ChatSession | None:
        session = self._sessions.get(session_id)
        return deepcopy(session) if session is not None else None

    def save(self, session: ChatSession) -> None:
        self._sessions[session.session_id] = deepcopy(session)

    def reset(self, session_id: str) -> ChatSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        session.messages.clear()
        session.system_prompt = None
        self._sessions[session_id] = deepcopy(session)
        return deepcopy(session)

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
