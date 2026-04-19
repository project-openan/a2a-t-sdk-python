"""
a2a_t - Python A2A SDK for Telecom Scenarios

This SDK extends the official a2a-python SDK with features tailored for
telecom operator environments, including prompt management, context compression,
and LLM integration adapters.
"""

from __future__ import annotations

from importlib import import_module

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "errors",
    "connection_pool",
    "logging",
    "utils",
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

_LAZY_IMPORTS = {
    "errors": "a2a_t.common.errors",
    "connection_pool": "a2a_t.common.connection_pool",
    "logging": "a2a_t.common.logging",
    "utils": "a2a_t.common.utils",
    "prompt": "a2a_t.prompt",
    "models": "a2a_t.prompt.common.models",
    "loader": "a2a_t.prompt.loader",
    "cache": "a2a_t.prompt.resources.cache",
    "prompt_errors": "a2a_t.prompt.common.errors",
    "base": "a2a_t.compression.base",
    "chain": "a2a_t.compression.chain",
    "compression_errors": "a2a_t.compression.errors",
    "llm_base": "a2a_t.llm.base",
    "factory": "a2a_t.llm.factory",
    "config_models": "a2a_t.config.models",
}


def __getattr__(name: str):
    module_name = _LAZY_IMPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'a2a_t' has no attribute {name!r}")
    module = import_module(module_name)
    globals()[name] = module
    return module
