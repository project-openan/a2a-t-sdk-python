"""Base classes for LLM adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class LLMResponse:
    """Response from an LLM adapter."""

    content: str
    model: str
    usage: dict[str, int]
    metadata: dict[str, Any]


class LLMAdapter(ABC):
    """Abstract base class for LLM adapters."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    @abstractmethod
    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        """Generate a completion for the given prompt."""
        raise NotImplementedError

    @abstractmethod
    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        """Generate a chat completion."""
        raise NotImplementedError

    @abstractmethod
    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, Any], **kwargs: Any) -> LLMResponse:
        """Generate a structured response constrained by the provided JSON schema."""
        raise NotImplementedError

    @property
    @abstractmethod
    def adapter_type(self) -> str:
        """Return the adapter type identifier."""
        raise NotImplementedError
