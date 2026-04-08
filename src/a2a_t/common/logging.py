"""Logging utilities for a2a_t."""

from __future__ import annotations

import logging
from typing import Literal

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_default_logger: logging.Logger | None = None


def get_logger(name: str, level: LogLevel = "INFO") -> logging.Logger:
    """Get or create a logger with the specified name and level."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, level))
    return logger


def get_default_logger() -> logging.Logger:
    """Get the default SDK logger."""
    global _default_logger
    if _default_logger is None:
        _default_logger = get_logger("a2a_t")
    return _default_logger
