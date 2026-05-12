from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from a2a_t.prompt.common.errors import PromptSourceError

from .errors import PromptResourceNotFoundError, PromptResourceParseError


class LocalPromptResourceFiles:
    """Read prompt resources directly from a local root directory."""

    def __init__(self, *, root_dir: str | Path | None = None) -> None:
        self._root_dir = Path(root_dir) if root_dir is not None else self._default_root_dir()

    @property
    def root_dir(self) -> Path:
        """Return the effective local resource root."""
        return self._root_dir

    def resolve(self, relative_path: str) -> Path:
        """Resolve one resource-relative path under the configured root."""
        relative = Path(relative_path)
        if relative.is_absolute():
            raise PromptSourceError("Prompt resource path must be relative.", locator=relative_path, source_type="local_file")

        root = self._root_dir.resolve()
        target = root.joinpath(relative).resolve()
        if root != target and root not in target.parents:
            raise PromptSourceError("Prompt resource path escapes local root.", locator=relative_path, source_type="local_file")
        return target

    def exists(self, relative_path: str) -> bool:
        """Return whether the resolved path exists as a file."""
        target = self.resolve(relative_path)
        return target.exists() and target.is_file()

    def read_text(self, relative_path: str) -> str:
        """Read a UTF-8 text resource from the local root."""
        target = self.resolve(relative_path)
        if not target.exists() or not target.is_file():
            raise PromptResourceNotFoundError("Prompt resource file does not exist.", path=str(target))
        return target.read_text(encoding="utf-8")

    def read_json(self, relative_path: str) -> dict[str, Any]:
        """Read and parse a JSON object resource from the local root."""
        text = self.read_text(relative_path)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise PromptResourceParseError("Prompt resource JSON is invalid.", path=relative_path) from error

        if not isinstance(payload, dict):
            raise PromptResourceParseError(
                "Prompt resource JSON root must be an object.",
                path=relative_path,
                actual_type=type(payload).__name__,
            )
        return payload

    def _default_root_dir(self) -> Path:
        """Return the packaged prompt resource root."""
        return Path(__file__).resolve().parents[4] / "package_data" / "prompt_resources"


class BasePromptResourceLoader:
    """Share local-file loading helpers across prompt resource loaders."""

    def __init__(self, *, root_dir: str | Path | None = None) -> None:
        self._files = LocalPromptResourceFiles(root_dir=root_dir)
        self._default_files = LocalPromptResourceFiles(root_dir=self._default_root_dir())

    @property
    def root_dir(self) -> Path:
        """Expose the effective resource root directory when available."""
        return self._files.root_dir

    def _default_root_dir(self) -> Path:
        """Return the packaged prompt resource root used by default loaders."""
        return Path(__file__).resolve().parents[4] / "package_data" / "prompt_resources"

    def _read_text(self, relative_path: str) -> str:
        """Read a text resource through the configured source."""
        return self._files.read_text(relative_path)

    def _read_json(self, relative_path: str) -> dict[str, Any]:
        """Read and parse a JSON resource through the configured source."""
        return self._files.read_json(relative_path)

    def _read_text_with_fallback(self, relative_path: str) -> str:
        """Read a text resource from the local root, then the packaged root."""
        try:
            return self._read_text(relative_path)
        except PromptResourceNotFoundError:
            return self._default_files.read_text(relative_path)

    def _read_json_with_fallback(self, relative_path: str) -> dict[str, Any]:
        """Read a JSON resource from the local root, then the packaged root."""
        try:
            return self._read_json(relative_path)
        except PromptResourceNotFoundError:
            return self._default_files.read_json(relative_path)
