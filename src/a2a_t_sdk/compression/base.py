"""Base classes for compression strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class CompressionResult:
    """Result of a compression operation."""

    compressed: str
    original_length: int
    compressed_length: int
    ratio: float
    metadata: dict[str, Any]


class CompressionStrategy(ABC):
    """Abstract base class for compression strategies."""

    @abstractmethod
    def compress(self, text: str, options: dict[str, Any] | None = None) -> CompressionResult:
        """Compress the given text."""
        raise NotImplementedError

    @abstractmethod
    def decompress(self, data: str) -> str:
        """Decompress the given data."""
        raise NotImplementedError

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the strategy name."""
        raise NotImplementedError
