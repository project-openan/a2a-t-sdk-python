"""LLM usage stash: contextvar bridge from the LLM layer to client entry spans (spec 4.2).

The internal ``A2ATLLMClientDecorator`` (a2a-java parity, spec 2.2) calls
``set_llm_usage`` right after a prompt-generation LLM call; the client transport
decorator reads (and clears) the stash when creating entry spans so the prompt
tokens land on ``gen_ai.usage.input_tokens`` / ``gen_ai.usage.output_tokens`` /
``gen_ai.request.model``. Contextvar scoping keeps concurrent calls isolated
(spec 8.1: task-level isolation, no cross-call pollution).
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(slots=True)
class LLMUsageStash:
    """Token usage of one prompt-generation LLM call, awaiting attachment to an entry span."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    request_model: str | None = None


_STASH: ContextVar[LLMUsageStash | None] = ContextVar("a2at_llm_usage_stash", default=None)


def set_llm_usage(stash: LLMUsageStash) -> None:
    """Stash usage for the next client entry span created in this context."""
    _STASH.set(stash)


def get_llm_usage() -> LLMUsageStash | None:
    """Return the stashed usage, or None when no LLM call happened in this context."""
    return _STASH.get()


def clear_llm_usage() -> None:
    """Drop the stashed usage (called by the transport decorator after reading it)."""
    _STASH.set(None)
