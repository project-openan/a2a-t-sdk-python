"""Hybrid compression strategy combining multiple approaches."""

from __future__ import annotations

from typing import Any

from a2a_t.compression.base import CompressionStrategy, CompressionResult
from a2a_t.compression.strategies.keyword_extractor import KeywordExtractor
from a2a_t.compression.strategies.llm_summarizer import LLMSummarizer


class HybridStrategy(CompressionStrategy):
    """Combines keyword extraction and LLM summarization."""

    def __init__(
        self,
        keyword_extractor: KeywordExtractor | None = None,
        llm_summarizer: LLMSummarizer | None = None,
    ) -> None:
        self._keyword_extractor = keyword_extractor or KeywordExtractor()
        self._llm_summarizer = llm_summarizer

    @property
    def name(self) -> str:
        return "hybrid"

    def compress(self, text: str, options: dict[str, Any] | None = None) -> CompressionResult:
        """Apply hybrid compression: keywords first, then LLM summary."""
        step1 = self._keyword_extractor.compress(text, options)

        if self._llm_summarizer and len(step1.compressed) > 200:
            return self._llm_summarizer.compress(step1.compressed, options)

        return step1

    def decompress(self, data: str) -> str:
        """Cannot decompress hybrid compression."""
        raise NotImplementedError("Hybrid compression does not support decompression")
