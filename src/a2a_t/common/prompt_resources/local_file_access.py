"""Local prompt-resource root handling: traversal guards and the frozen snapshot (D9).

Port of the Java ``LocalFileResourceSnapshot`` plus the traversal-validation semantics of the
interim ``LocalPromptResourceFiles``: caller-supplied paths must be relative and must never escape
the local root. The snapshot captures the routed subtrees of the local root once, at access-object
construction, and never re-reads them: runtime lookups resolve against the captured map rather than
the filesystem, so local file changes only take effect after the SDK is restarted (D9).

Divergence from the Java snapshot (D31): the routed categories include ``negotiation-vocabulary/``,
because negotiation resources are locally overridable in this port (D13 as overridden by D31); Java
1.1.0 keeps them classpath-fixed and therefore does not snapshot them.

The snapshot itself carries no fallback: the built-in packaged fallback of the missing local
business content (Java ADR 0005 overlay) is layered on top by
:class:`~a2a_t.common.prompt_resources.builtin_fallback.BuiltinFallbackReader`, which the access
object wires as the routed reader.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final, Mapping

from a2a_t.core.errors.exceptions import A2ATError, ResourceNotFoundError
from a2a_t.core.prompt_resource_key import PromptResourceKey

__all__ = ["LocalResourceSnapshot", "require_root_relative_path"]

_PROMPT_RESOURCES_PREFIX: Final[str] = "prompt_resources/"

#: Routed categories captured by the snapshot (D31 routing table: everything except the
#: package-fixed ``prompts/`` and ``errors/`` trees).
_SNAPSHOT_CATEGORIES: Final[tuple[str, ...]] = ("templates", "slots", "scenarios", "negotiation-vocabulary")

_SEPARATOR: Final[str] = "/"


def require_root_relative_path(relative_path: str | None, *, label: str = "Local prompt resource path") -> str:
    """Validate that one resource path is relative and cannot escape the local root.

    Port of the traversal guards of the interim ``LocalPromptResourceFiles.resolve``: the path must
    be relative (no absolute path, no Windows drive), must use forward slashes and must not contain
    a ``..`` segment, so a resolved location can never leave the root it is resolved against.

    Args:
        relative_path: candidate resource path, forward-slash separated.
        label: human-readable label describing the path, used in the failure message.

    Returns:
        the validated path, unchanged.

    Raises:
        ValueError: when the path is blank, absolute or escapes the root.
    """
    if relative_path is None or relative_path.strip() == "":
        raise ValueError(f"{label} must not be blank but was {relative_path}.")
    if "\\" in relative_path:
        raise ValueError(f"{label} must use forward slashes and stay inside the local root but was {relative_path}.")
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError(f"{label} must be a relative path inside the local root but was {relative_path}.")
    if any(part == ".." for part in candidate.parts):
        raise ValueError(f"{label} must not escape the local root but was {relative_path}.")
    return relative_path


class LocalResourceSnapshot:
    """Immutable in-memory snapshot of one local prompt resource root, captured once (D9).

    The ``templates/``, ``slots/``, ``scenarios/`` and ``negotiation-vocabulary/`` subtrees of the
    local root are read into a frozen map keyed by forward-slash root-relative path
    (``templates/Task-T/.../template.md``). Runtime lookups resolve against this map, never against
    the filesystem. The snapshot also serves as the local reader of the D31 routing seam: it exposes
    ``read_text``/``category_types`` shaped exactly like
    :class:`~a2a_t.common.prompt_resources.packaged_access.PackagedResourceReader`, so the generic
    loaders in :mod:`~a2a_t.common.prompt_resources.template_source` and
    :mod:`~a2a_t.common.prompt_resources.json_source` are source-agnostic.
    """

    def __init__(self, root_dir: Path, content: Mapping[str, str]) -> None:
        """Freeze one captured content map for one local root.

        Args:
            root_dir: the local prompt resource root the content was captured from.
            content: root-relative path to UTF-8 text payload mapping, copied defensively.
        """
        self._root_dir = root_dir
        self._content: dict[str, str] = dict(content)

    @classmethod
    def capture(cls, root_dir: Path) -> LocalResourceSnapshot:
        """Capture the routed subtrees under one local root into an immutable snapshot.

        Directory symlinks are not followed, mirroring the Java ``Files.walk`` default. A missing
        category directory is simply absent from the snapshot.

        Args:
            root_dir: local prompt resource root.

        Returns:
            the frozen snapshot of the root.

        Raises:
            A2ATError: when a subtree cannot be enumerated or a file cannot be read.
        """
        content: dict[str, str] = {}
        for category in _SNAPSHOT_CATEGORIES:
            cls._capture_tree(root_dir / category, category, content)
        return cls(root_dir, content)

    @property
    def root_dir(self) -> Path:
        """The local prompt resource root this snapshot was captured from.

        Returns:
            the root directory the content map was read from.
        """
        return self._root_dir

    def contains(self, root_relative_path: str) -> bool:
        """Return whether the snapshot captured one root-relative path.

        Args:
            root_relative_path: path relative to the local root, such as
                ``templates/Task-T/.../template.md``.

        Returns:
            ``True`` when the path was present at capture time.
        """
        return require_root_relative_path(root_relative_path) in self._content

    def read_text(self, key: PromptResourceKey) -> str:
        """Read one captured resource through its resource key.

        Args:
            key: resource key identifying the file; its ``prompt_resources/`` prefix is stripped to
                get the root-relative snapshot key.

        Returns:
            the text captured at construction time — never a fresh filesystem read.

        Raises:
            ResourceNotFoundError: when the path was not captured (missing at construction).
        """
        snapshot_key = self._snapshot_key(key)
        text = self._content.get(snapshot_key)
        if text is None:
            raise ResourceNotFoundError("Prompt resource file does not exist.", snapshot_key)
        return text

    def category_types(self, category: str) -> tuple[str, ...]:
        """Return the sorted first-level directory names captured under one category.

        Args:
            category: category prefix, such as ``templates`` or ``slots``.

        Returns:
            the sorted directory names captured at construction time; empty when none was captured.
        """
        prefix = category + _SEPARATOR
        types: set[str] = set()
        for key in self._content:
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix) :]
            separator = rest.find(_SEPARATOR)
            if separator > 0:
                types.add(rest[:separator])
        return tuple(sorted(types))

    def category_files(self, category: str, file_name: str) -> dict[str, str]:
        """Return the captured files of one category matching a file name.

        The local half of the directory-driven template enumeration consumed by the template
        catalog (Java ``PromptTemplateCatalog.localTemplates``): every captured path under the
        category ending in ``file_name`` is reported with its captured text, so later file edits
        stay invisible exactly like every other snapshot read (D9). The returned keys are
        category-relative like the packaged reader's (Java keeps the classpath root prefix in the
        intermediate map and strips it when resolving the URI; this port returns the stripped form
        directly).

        Args:
            category: category prefix, such as ``templates``.
            file_name: file name of the payloads to report, such as ``template.md``.

        Returns:
            a mapping of category-relative path (forward slashes) to the captured text; the paths
            end with ``/<language>/<file_name>`` for template-shaped categories.
        """
        prefix = category + _SEPARATOR
        suffix = _SEPARATOR + file_name
        return {
            key[len(prefix) :]: text
            for key, text in sorted(self._content.items())
            if key.startswith(prefix) and key.endswith(suffix)
        }

    def _snapshot_key(self, key: PromptResourceKey) -> str:
        """Return the validated root-relative snapshot key of one resource key."""
        relative_path = key.relative_path()
        if relative_path.startswith(_PROMPT_RESOURCES_PREFIX):
            relative_path = relative_path[len(_PROMPT_RESOURCES_PREFIX) :]
        return require_root_relative_path(relative_path)

    @staticmethod
    def _capture_tree(category_root: Path, category: str, content: dict[str, str]) -> None:
        """Capture every regular file of one category subtree into the content map."""
        if not category_root.is_dir():
            return
        files: list[Path] = []
        try:
            for dir_path, _dir_names, file_names in os.walk(category_root, followlinks=False):
                files.extend(Path(dir_path) / name for name in file_names)
        except OSError as error:
            raise A2ATError(f"Failed to enumerate prompt resources under: {category_root}") from error
        for file_path in sorted(files):
            if not file_path.is_file():
                continue
            relative_key = category + _SEPARATOR + file_path.relative_to(category_root).as_posix()
            try:
                content[relative_key] = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise A2ATError(f"Failed to read prompt resource: {file_path}") from error
