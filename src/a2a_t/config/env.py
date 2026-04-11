from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


@dataclass(slots=True)
class EnvConfig:
    """Load settings from `.env` and process environment. / 从 `.env` 与进程环境加载配置。"""

    values: dict[str, str]

    @classmethod
    def load(cls, *, env_path: Path) -> "EnvConfig":
        values: dict[str, str] = {}

        if env_path.exists():
            file_values = dotenv_values(env_path)
            values.update(
                {key: value for key, value in file_values.items() if key is not None and value is not None}
            )

        for key, value in os.environ.items():
            if key in values:
                values[key] = value

        return cls(values=values)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.values.get(key, default)
