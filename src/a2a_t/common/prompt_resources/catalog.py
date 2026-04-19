from __future__ import annotations

from pathlib import Path
from typing import Protocol

from a2a_t.prompt.common.errors import PromptSourceError


class PromptResourceCatalog(Protocol):
    """Resolve resource-tree relative paths against a configured source root."""

    def resolve(self, *, relative_path: str) -> Path: ...


class LocalPromptResourceCatalog:
    """Resolve A2A-T resource relative paths under a local root directory."""

    def __init__(self, *, root_dir: str | Path | None = None) -> None:
        self._root_dir = Path(root_dir) if root_dir is not None else self._default_root_dir()

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    def resolve(self, *, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute():
            raise PromptSourceError("Prompt resource path must be relative.", locator=relative_path, source_type="local_file")

        root = self._root_dir.resolve()
        target = root.joinpath(relative).resolve()
        if root != target and root not in target.parents:
            raise PromptSourceError("Prompt resource path escapes local root.", locator=relative_path, source_type="local_file")
        return target

    def _default_root_dir(self) -> Path:
        return Path(__file__).resolve().parents[4] / "package_data" / "prompt_resources"
