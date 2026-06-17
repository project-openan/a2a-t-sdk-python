"""Provider-facing LLM client interface."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from a2a_t.llm.models import LLMResponse


@runtime_checkable
class LLMClient(Protocol):
    """Minimal interface implemented by LLM provider clients."""

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate a structured response constrained by the provided JSON schema."""
        ...
