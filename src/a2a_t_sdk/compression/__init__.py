"""Compression module for a2a_t_sdk."""

from a2a_t_sdk.compression.base import CompressionStrategy, CompressionResult
from a2a_t_sdk.compression.chain import StrategyChain
from a2a_t_sdk.compression.errors import CompressionError

__all__ = ["CompressionStrategy", "CompressionResult", "StrategyChain", "CompressionError"]
