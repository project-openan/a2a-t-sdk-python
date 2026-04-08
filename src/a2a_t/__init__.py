"""
a2a_t - Python A2A SDK for Telecom Scenarios

This SDK extends the official a2a-python SDK with features tailored for
telecom operator environments, including prompt management, context compression,
and LLM integration adapters.
"""

__version__ = "0.1.0"

from a2a_t.common import errors, connection_pool, logging, utils
from a2a_t.client import extended_client, prompt_client, compression_client
from a2a_t.server import (
    extended_server,
    prompt_handler,
    compression_handler,
    rate_limiter,
)
from a2a_t import prompt
from a2a_t.prompt import models, loader, cache, errors as prompt_errors
from a2a_t.compression import base, chain, errors as compression_errors
from a2a_t.llm import base as llm_base, factory
from a2a_t.config import loader, models as config_models

__all__ = [
    "__version__",
    "errors",
    "connection_pool",
    "logging",
    "utils",
    "extended_client",
    "prompt_client",
    "compression_client",
    "extended_server",
    "prompt_handler",
    "compression_handler",
    "rate_limiter",
    "prompt",
    "models",
    "loader",
    "cache",
    "prompt_errors",
    "base",
    "chain",
    "compression_errors",
    "llm_base",
    "factory",
    "config_models",
]
