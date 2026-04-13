from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import PromptResourceNotFoundError, PromptResourceParseError
from .source import LocalPromptResourceSource, PromptResourceSource


class BasePromptResourceLoader:
    def __init__(self, *, source: PromptResourceSource | None = None, root_dir: str | Path | None = None) -> None:
        self._source = source or LocalPromptResourceSource(root_dir=root_dir)

    @property
    def root_dir(self) -> Path:
        root_dir = getattr(self._source, "root_dir", None)
        if root_dir is None:
            return self._default_root_dir()
        return Path(root_dir)

    @property
    def source(self) -> PromptResourceSource:
        return self._source

    def _default_root_dir(self) -> Path:
        return Path(__file__).resolve().parents[4] / "package_data" / "prompt_resources"

    def _read_text(self, relative_path: str) -> str:
        return self._source.read_text(relative_path=relative_path)

    def _read_json(self, relative_path: str) -> dict[str, Any]:
        return self._source.read_json(relative_path=relative_path)
