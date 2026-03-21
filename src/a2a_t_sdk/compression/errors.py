"""Compression-specific error definitions."""


class CompressionError(Exception):
    """Base exception for compression-related errors."""

    def __init__(self, message: str, strategy: str | None = None) -> None:
        super().__init__(message)
        self.strategy = strategy


class DecompressionError(CompressionError):
    """Raised when decompression fails."""

    def __init__(self, message: str, strategy: str | None = None) -> None:
        super().__init__(message, strategy)


class StrategyNotFoundError(CompressionError):
    """Raised when a compression strategy is not found."""

    def __init__(self, strategy_name: str) -> None:
        super().__init__(f"Strategy not found: {strategy_name}", strategy_name)
