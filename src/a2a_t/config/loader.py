"""Configuration loader for a2a_t."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
import yaml


class ConfigLoader:
    """Loads configuration from YAML files and environment variables."""

    @staticmethod
    def load_file(path: Path) -> dict[str, Any]:
        """Load configuration from a YAML file."""
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @staticmethod
    def load_env_prefix(prefix: str = "A2A_") -> dict[str, Any]:
        """Load configuration from environment variables with a prefix."""
        config: dict[str, Any] = {}
        for key, value in os.environ.items():
            if key.startswith(prefix):
                config_key = key[len(prefix):].lower()
                config[config_key] = value
        return config

    @staticmethod
    def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """Merge two configuration dictionaries."""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ConfigLoader.merge(result[key], value)
            else:
                result[key] = value
        return result

    def load(self, config_path: Path | None = None) -> dict[str, Any]:
        """Load and merge configurations from file and environment."""
        config: dict[str, Any] = {}

        if config_path:
            config = self.load_file(config_path)

        env_config = self.load_env_prefix()
        return self.merge(config, env_config)
