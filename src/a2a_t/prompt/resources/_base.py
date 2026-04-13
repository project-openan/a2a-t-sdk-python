from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import PromptResourceNotFoundError, PromptResourceParseError


class BasePromptResourceLoader:
    def __init__(self, *, root_dir: str | Path | None = None) -> None:
        self._root_dir = Path(root_dir) if root_dir is not None else self._default_root_dir()

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    def _default_root_dir(self) -> Path:
        return Path(__file__).resolve().parents[4] / "package_data" / "prompt_resources"

    def _read_text(self, path: Path) -> str:
        if not path.exists():
            raise PromptResourceNotFoundError("Prompt resource file does not exist.", path=str(path))

        return path.read_text(encoding="utf-8")

    def _read_json(self, path: Path) -> dict[str, Any]:
        text = self._read_text(path)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise PromptResourceParseError("Prompt resource JSON is invalid.", path=str(path)) from error

        if not isinstance(data, dict):
            raise PromptResourceParseError(
                "Prompt resource JSON root must be an object.",
                path=str(path),
                actual_type=type(data).__name__,
            )

        return data
