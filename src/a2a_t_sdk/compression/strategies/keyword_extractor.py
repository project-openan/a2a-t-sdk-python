"""Keyword extraction compression strategy."""

from __future__ import annotations

import re
from typing import Any
from collections import Counter

from a2a_t_sdk.compression.base import CompressionStrategy, CompressionResult


class KeywordExtractor(CompressionStrategy):
    """Extracts and retains only important keywords."""

    def __init__(self, top_n: int = 20, min_length: int = 3) -> None:
        self._top_n = top_n
        self._min_length = min_length

    @property
    def name(self) -> str:
        return "keyword_extractor"

    def compress(self, text: str, options: dict[str, Any] | None = None) -> CompressionResult:
        """Extract top keywords from text."""
        words = re.findall(r"\b\w+\b", text.lower())
        filtered = [w for w in words if len(w) >= self._min_length]
        counter = Counter(filtered)
        top_keywords = [w for w, _ in counter.most_common(options.get("top_n", self._top_n) if options else self._top_n)]

        return CompressionResult(
            compressed=" ".join(top_keywords),
            original_length=len(text),
            compressed_length=len(" ".join(top_keywords)),
            ratio=len(" ".join(top_keywords)) / len(text) if text else 0,
            metadata={"keyword_count": len(top_keywords)},
        )

    def decompress(self, data: str) -> str:
        """Keyword extraction is lossy, cannot decompress."""
        raise NotImplementedError("Keyword extraction does not support decompression")
