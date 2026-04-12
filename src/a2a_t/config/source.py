from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

from a2a_t.config.errors import ConfigFileNotFoundError


class DotEnvConfigSource:
    """只从 .env 文件读取配置 / Read configuration only from a .env file."""

    @staticmethod
    def load(path: Path) -> dict[str, str]:
        if not path.exists():
            raise ConfigFileNotFoundError(path)

        file_values = dotenv_values(path)
        return {key: value for key, value in file_values.items() if key is not None and value is not None}
