"""Strategy chain manager for compression pipelines."""

from __future__ import annotations

from typing import Any
from a2a_t_sdk.compression.base import CompressionStrategy, CompressionResult


class StrategyChain:
    """Manages a chain of compression strategies."""

    def __init__(self) -> None:
        self._strategies: list[CompressionStrategy] = []

    def add(self, strategy: CompressionStrategy) -> None:
        """Add a strategy to the chain."""
        self._strategies.append(strategy)

    def compress(self, text: str, options: dict[str, Any] | None = None) -> CompressionResult:
        """Apply all strategies in sequence."""
        current = text
        metadata: dict[str, Any] = {"strategies": []}

        for strategy in self._strategies:
            result = strategy.compress(current, options)
            current = result.compressed
            metadata["strategies"].append({
                "name": strategy.name,
                "ratio": result.ratio,
            })
            metadata.update(result.metadata)

        return CompressionResult(
            compressed=current,
            original_length=len(text),
            compressed_length=len(current),
            ratio=len(current) / len(text) if text else 0,
            metadata=metadata,
        )

    def clear(self) -> None:
        """Remove all strategies from the chain."""
        self._strategies.clear()
