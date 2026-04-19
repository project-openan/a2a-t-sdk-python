"""Common utilities and shared components for a2a_t."""

from __future__ import annotations

from importlib import import_module

__all__ = ["connection_pool", "errors", "logging", "prompt_resources", "prompt_runtime", "utils"]

_LAZY_IMPORTS = {
    "connection_pool": "a2a_t.common.connection_pool",
    "errors": "a2a_t.common.errors",
    "logging": "a2a_t.common.logging",
    "prompt_resources": "a2a_t.common.prompt_resources",
    "prompt_runtime": "a2a_t.common.prompt_runtime",
    "utils": "a2a_t.common.utils",
}


def __getattr__(name: str):
    module_name = _LAZY_IMPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'a2a_t.common' has no attribute {name!r}")
    module = import_module(module_name)
    globals()[name] = module
    return module
