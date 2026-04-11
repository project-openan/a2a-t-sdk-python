from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class EnvConfig:
    """Load key/value settings from a .env file and process environment."""

    values: dict[str, str]

    @classmethod
    def load(cls, *, env_path: Path) -> "EnvConfig":
        values: dict[str, str] = {}

        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                normalized = line.strip()
                if not normalized or normalized.startswith("#"):
                    continue
                if "=" not in normalized:
                    continue
                key, value = normalized.split("=", 1)
                values[key.strip()] = value.strip()

        for key, value in os.environ.items():
            if key in values:
                values[key] = value

        return cls(values=values)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.values.get(key, default)
