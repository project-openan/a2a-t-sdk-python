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
    "client",
    "server",
    "config",
    "llm",
    "common",
    "prompt",
]

_LAZY_IMPORTS = {
    "client": "a2a_t.client",
    "server": "a2a_t.server",
    "config": "a2a_t.config",
    "llm": "a2a_t.llm",
    "common": "a2a_t.common",
    "prompt": "a2a_t.prompt",
}


def __getattr__(name: str):
    module_name = _LAZY_IMPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'a2a_t' has no attribute {name!r}")
    module = import_module(module_name)
    globals()[name] = module
    return module
