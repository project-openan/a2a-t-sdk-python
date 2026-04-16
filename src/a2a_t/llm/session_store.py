"""Session storage abstractions for llm chat support."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from a2a_t.llm.errors import LLMConfigError

if TYPE_CHECKING:
    from a2a_t.llm.base import ChatSession


class SessionStore(Protocol):
    def get(self, session_id: str) -> ChatSession | None: ...
    def save(self, session: ChatSession) -> None: ...
    def reset(self, session_id: str) -> ChatSession | None: ...
    def delete(self, session_id: str) -> None: ...


class InMemorySessionStore:
    def __init__(self, *, max_total: int | None = None, max_per_provider: int | None = None) -> None:
        self._max_total = max_total
        self._max_per_provider = max_per_provider
        self._sessions: dict[str, ChatSession] = {}

    def get(self, session_id: str) -> ChatSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        updated = deepcopy(session)
        updated.last_accessed_time = datetime.now(UTC)
        self._sessions[session_id] = updated
        return deepcopy(updated)

    def save(self, session: ChatSession) -> None:
        stored = deepcopy(session)
        stored.last_accessed_time = datetime.now(UTC)
        self._sessions[stored.session_id] = stored
        self._evict_for_provider(stored.provider)
        self._evict_for_total()

    def reset(self, session_id: str) -> ChatSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        updated = deepcopy(session)
        updated.messages.clear()
        updated.system_prompt = None
        updated.last_accessed_time = datetime.now(UTC)
        self._sessions[session_id] = updated
        return deepcopy(updated)

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def _evict_for_provider(self, provider: str) -> None:
        if self._max_per_provider is None:
            return
        provider_sessions = [item for item in self._sessions.values() if item.provider == provider]
        while len(provider_sessions) > self._max_per_provider:
            oldest = min(provider_sessions, key=lambda item: item.last_accessed_time)
            self._sessions.pop(oldest.session_id, None)
            provider_sessions = [item for item in self._sessions.values() if item.provider == provider]

    def _evict_for_total(self) -> None:
        if self._max_total is None:
            return
        while len(self._sessions) > self._max_total:
            oldest = min(self._sessions.values(), key=lambda item: item.last_accessed_time)
            self._sessions.pop(oldest.session_id, None)


class ProviderScopedSessionStore:
    def __init__(self, provider: str, root_store: SessionStore) -> None:
        self._provider = provider
        self._root_store = root_store

    def get(self, session_id: str) -> ChatSession | None:
        if not session_id.startswith(f"{self._provider}-"):
            return None
        return self._root_store.get(session_id)

    def save(self, session: ChatSession) -> None:
        if session.provider != self._provider:
            raise LLMConfigError(
                f"session provider mismatch: expected {self._provider}, got {session.provider}"
            )
        if not session.session_id.startswith(f"{self._provider}-"):
            raise LLMConfigError(
                f"session_id must start with '{self._provider}-': {session.session_id}"
            )
        self._root_store.save(session)

    def reset(self, session_id: str) -> ChatSession | None:
        if not session_id.startswith(f"{self._provider}-"):
            return None
        return self._root_store.reset(session_id)

    def delete(self, session_id: str) -> None:
        if session_id.startswith(f"{self._provider}-"):
            self._root_store.delete(session_id)
