"""Traced wrapper of the deterministic scripted SDK provider (offline mode only)."""

from __future__ import annotations

from typing import Any

from a2a_t.llm.factory import LLMClientFactory
from a2a_t.llm.models import LLMResponse
from shared.traced_llm import TRACED_SCRIPTED_PROVIDER, TracedStructuredMixin

from demo.scripted_llm import ScriptedLLMClient


class TracedScriptedClient(TracedStructuredMixin, ScriptedLLMClient):
    """Offline ``ScriptedLLMClient`` that captures the canned response it served."""

    _provider_label = TRACED_SCRIPTED_PROVIDER

    def _call_structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None,
        max_tokens: int | None,
    ) -> LLMResponse:
        """Delegate to the deterministic scripted implementation."""
        return ScriptedLLMClient.structured(
            self,
            messages=messages,
            json_schema=json_schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )


def register_traced_scripted_provider() -> None:
    """Register the traced scripted provider with the SDK factory (idempotent)."""
    if TRACED_SCRIPTED_PROVIDER not in set(LLMClientFactory.available_providers()):
        LLMClientFactory.register(TRACED_SCRIPTED_PROVIDER, TracedScriptedClient)
