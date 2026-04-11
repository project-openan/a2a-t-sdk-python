"""Configuration management for a2a_t."""

from __future__ import annotations

from importlib import import_module

__all__ = ["ConfigLoader", "SDKConfig", "ClientConfig", "EnvConfig", "ServerConfig"]


def __getattr__(name: str):
    if name == "EnvConfig":
        value = import_module("a2a_t.config.env").EnvConfig
    elif name == "ConfigLoader":
        value = import_module("a2a_t.config.loader").ConfigLoader
    elif name in {"SDKConfig", "ClientConfig", "ServerConfig"}:
        value = getattr(import_module("a2a_t.config.models"), name)
    else:
        raise AttributeError(f"module 'a2a_t.config' has no attribute {name!r}")

    globals()[name] = value
    return value
