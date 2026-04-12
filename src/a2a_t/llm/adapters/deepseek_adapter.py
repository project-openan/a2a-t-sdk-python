"""DeepSeek adapter for text completion and chat."""

from __future__ import annotations

from a2a_t.llm.adapters.openai_adapter import OpenAIAdapter


class DeepSeekAdapter(OpenAIAdapter):
    """LLM adapter for DeepSeek's OpenAI-compatible API."""

    @property
    def adapter_type(self) -> str:
        return "deepseek"
