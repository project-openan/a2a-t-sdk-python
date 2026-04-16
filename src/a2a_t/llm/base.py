"""Base classes for LLM adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from a2a_t.llm.errors import LLMConfigError, LLMRuntimeError
from a2a_t.llm.session_store import InMemorySessionStore, ProviderScopedSessionStore, SessionStore


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class ChatSession:
    session_id: str
    provider: str
    system_prompt: str | None = None
    messages: list[ChatMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_accessed_time: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class LLMResponse:
    """Response from an LLM adapter."""

    content: str
    model: str
    usage: dict[str, int]
    metadata: dict[str, Any]
    session_id: str | None = None


class LLMAdapter(ABC):
    """Abstract base class for LLM adapters."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._provider = str(config.get("provider", "")).strip() or self.adapter_type
        self._model = str(config.get("model", ""))
        root_store: SessionStore = config.get("session_store") or InMemorySessionStore()
        self._session_store = (
            root_store if isinstance(root_store, ProviderScopedSessionStore) else ProviderScopedSessionStore(
                self._provider, root_store
            )
        )
        if not self._model:
            raise LLMConfigError("LLM adapter requires a non-empty model")

    def complete(self, prompt: str, system_prompt: str | None = None, **kwargs: Any) -> LLMResponse:
        """Generate a completion for the given prompt."""
        provider_kwargs = dict(kwargs)
        provider_kwargs.pop("history_window", None)
        messages = self._build_messages(prompt=prompt, system_prompt=system_prompt)
        response = self._generate_from_messages(messages, **provider_kwargs)
        response.session_id = None
        return response

    def chat(
        self,
        message: str,
        system_prompt: str | None = None,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Generate a chat completion with session history."""
        history_window = int(kwargs.get("history_window", self._config.get("history_window", 10)))
        if history_window <= 0:
            raise LLMConfigError("history_window must be greater than zero")

        session = self._load_or_create_session(session_id=session_id)
        if session.system_prompt is None and system_prompt:
            session.system_prompt = system_prompt

        provider_kwargs = dict(kwargs)
        provider_kwargs.pop("history_window", None)

        session.messages.append(ChatMessage(role="user", content=message))
        current_msg = self._build_messages_from_session(session, history_window=history_window)
        response = self._generate_from_messages(current_msg, **provider_kwargs)
        session.messages.append(ChatMessage(role="assistant", content=response.content))
        session.messages = self._trim_session_messages(session.messages, history_window=history_window)
        session.last_accessed_time = datetime.now(UTC)
        self._session_store.save(session)
        response.session_id = session.session_id
        return response

    @abstractmethod
    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, Any], **kwargs: Any) -> LLMResponse:
        """Generate a structured response constrained by the provided JSON schema."""
        raise NotImplementedError

    @property
    @abstractmethod
    def adapter_type(self) -> str:
        """Return the adapter type identifier."""
        raise NotImplementedError

    def reset_session(self, session_id: str) -> None:
        if self._session_store.reset(session_id) is None:
            raise LLMRuntimeError(f"unknown session_id: {session_id}")

    def delete_session(self, session_id: str) -> None:
        self._session_store.delete(session_id)

    def _load_or_create_session(self, session_id: str | None) -> ChatSession:
        if session_id is None:
            now = datetime.now(UTC)
            return ChatSession(
                session_id=f"{self._provider}-{uuid4()}",
                provider=self._provider,
                created_at=now,
                last_accessed_time=now,
            )
        session = self._session_store.get(session_id)
        if session is None:
            raise LLMRuntimeError(f"unknown session_id: {session_id}")
        if session.provider != self._provider:
            raise LLMRuntimeError(
                f"session_id {session_id} belongs to provider {session.provider}, not {self._provider}"
            )
        return session

    def _build_messages(self, prompt: str, system_prompt: str | None) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        if system_prompt:
            messages.append(ChatMessage(role="system", content=system_prompt))
        messages.append(ChatMessage(role="user", content=prompt))
        return messages

    def _build_messages_from_session(self, session: ChatSession, history_window: int) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        if session.system_prompt:
            messages.append(ChatMessage(role="system", content=session.system_prompt))
        trimmed = session.messages[-(history_window * 2 - 1) :]
        messages.extend(trimmed)
        return messages

    def _trim_session_messages(
        self,
        messages: list[ChatMessage],
        history_window: int,
    ) -> list[ChatMessage]:
        return messages[-(history_window * 2) :]

    def _generate_from_messages(self, messages: list[ChatMessage], **kwargs: Any) -> LLMResponse:
        raise LLMRuntimeError("adapter does not support message generation in this phase")
