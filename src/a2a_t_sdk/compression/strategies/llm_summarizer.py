"""LLM-based summarization compression strategy."""

from __future__ import annotations

from typing import Any

from a2a_t_sdk.compression.base import CompressionStrategy, CompressionResult


class LLMSummarizer(CompressionStrategy):
    """Uses LLM to summarize text for compression."""

    def __init__(self, llm_adapter: Any | None = None, max_tokens: int = 500) -> None:
        self._llm_adapter = llm_adapter
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return "llm_summarizer"

    def compress(self, text: str, options: dict[str, Any] | None = None) -> CompressionResult:
        """Summarize text using LLM."""
        if not self._llm_adapter:
            raise RuntimeError("LLM adapter not configured")
        raise NotImplementedError("LLM summarization requires backend integration")

    def decompress(self, data: str) -> str:
        """Cannot decompress LLM summaries."""
        raise NotImplementedError("LLM summarization does not support decompression")
