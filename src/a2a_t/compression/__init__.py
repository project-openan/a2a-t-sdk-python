"""Compression module for a2a_t."""

from a2a_t.compression.base import CompressionStrategy, CompressionResult
from a2a_t.compression.chain import StrategyChain
from a2a_t.compression.errors import CompressionError

__all__ = ["CompressionStrategy", "CompressionResult", "StrategyChain", "CompressionError"]
