from __future__ import annotations

from pathlib import Path

try:
    from dotenv import dotenv_values
except ModuleNotFoundError:
    def dotenv_values(path: str | Path) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            values[key] = value
        return values

from a2a_t.config.errors import ConfigFileNotFoundError


class DotEnvConfigSource:
    """只从 .env 文件读取配置 / Read configuration only from a .env file."""

    @staticmethod
    def load(path: Path) -> dict[str, str]:
        if not path.exists():
            raise ConfigFileNotFoundError(path)

        file_values = dotenv_values(path)
        return {key: value for key, value in file_values.items() if key is not None and value is not None}
