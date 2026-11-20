# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Packaged prompt-resource reads through ``importlib.resources`` (D8).

Port of the Java ``ClasspathPromptResourceLoader`` / ``ClasspathResourceDirectories`` pair.
Resources are addressed with :class:`~a2a_t.core.prompt_resource_key.PromptResourceKey` paths
(``prompt_resources/...``, relative to the ``a2a_t`` package) and read from the resource tree
shipped inside the package, so wheel, zipapp and source-checkout layouts all work unchanged.

Packaged reads are frozen at first load: every resource path is resolved once and cached at module
level — the Python counterpart of the JVM per-classloader cache, with the package object as the
cache identity. A resource that is missing raises on every call and is never cached, so a resource
that appears later in the same process is picked up (port plan 7.2, "missing is never cached").
Cache mutation relies on plain dict assignment, which is atomic under the GIL; concurrent first
loads of one path may duplicate the read but never corrupt the cache.
"""

from __future__ import annotations

from importlib.resources import files as _resource_files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Final

from a2a_t.core.errors.exceptions import ResourceNotFoundError
from a2a_t.core.prompt_resource_key import PromptResourceKey

__all__ = [
    "PackagedResourceReader",
    "list_category_directories",
    "load_category_files",
    "load_text",
    "prompt_resources_root",
]

_PACKAGE: Final[str] = "a2a_t"
_PROMPT_RESOURCES_ROOT: Final[str] = "prompt_resources"

# Module-level cache of successfully read resources keyed by package-relative resource path
# (Java: ConcurrentHashMap keyed by path + context classloader — the package object is the Python
# equivalent of the classloader dimension). Missing resources are deliberately never cached.
_text_cache: dict[str, str] = {}

# Module-level cache of discovered first-level category directories. Empty results are not cached
# either, mirroring the "missing is never cached" rule for a category that does not exist yet.
_directory_cache: dict[str, tuple[str, ...]] = {}


def load_text(key: PromptResourceKey) -> str:
    """Load one UTF-8 text resource from the packaged prompt resource tree.

    Args:
        key: resource key identifying the file under ``prompt_resources/``.

    Returns:
        the cached (on first read) text payload of the resource.

    Raises:
        ResourceNotFoundError: when the resource does not exist in the package; raised on every
            call — a missing resource is never cached.
        OSError: when an existing resource cannot be read; propagated for the caller to translate.
    """
    relative_path = key.relative_path()
    cached = _text_cache.get(relative_path)
    if cached is not None:
        return cached
    resource = _resource_of(relative_path)
    if not resource.is_file():
        raise ResourceNotFoundError("Prompt resource file does not exist.", relative_path)
    text = resource.read_text(encoding="utf-8")
    _text_cache[relative_path] = text
    return text


def list_category_directories(category: str) -> tuple[str, ...]:
    """List the first-level directory names of one packaged resource category.

    Mirrors the Java ``ClasspathResourceDirectories.list`` discovery used to avoid hardcoding
    resource-type lists that drift from the packaged resource tree (extensions bundled later, such
    as ``Authorization-T``, are discovered instead of being listed).

    Args:
        category: category directory under ``prompt_resources/``, such as ``templates`` or ``slots``.

    Returns:
        the sorted directory names; empty when the category exists in no packaged root. An empty
        result is not cached, so a category added later is picked up.
    """
    cache_key = f"{_PROMPT_RESOURCES_ROOT}/{category}"
    cached = _directory_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        names = tuple(
            sorted(entry.name for entry in _resource_files(_PACKAGE).joinpath(cache_key).iterdir() if entry.is_dir())
        )
    except OSError:
        return ()
    if names:
        _directory_cache[cache_key] = names
    return names


def load_category_files(category: str, file_name: str) -> dict[str, str]:
    """Walk one packaged category tree and return every matching file's text.

    The directory-driven enumeration behind the template catalog (Java
    ``PromptTemplateCatalog.captureClasspathTemplates``): the whole ``<category>/`` subtree of the
    packaged tree is walked recursively, so every extension directory that appears under it —
    including extensions bundled later — is discovered instead of being listed. Unlike
    :func:`load_text` the result is not cached here: the catalog captures the walked entries once
    into its own frozen snapshot, which is the D9 freeze point of this read family.

    Args:
        category: category directory under ``prompt_resources/``, such as ``templates``.
        file_name: file name of the payloads to collect, such as ``template.md``.

    Returns:
        a mapping of category-relative path (forward slashes, e.g.
        ``Negotiation-T/common/abort/v1/zh-CN/template.md``) to the UTF-8 text payload; empty when
        the category exists in no packaged root. A file that cannot be read is skipped.
    """
    root = _resource_files(_PACKAGE).joinpath(_PROMPT_RESOURCES_ROOT, category)
    entries: dict[str, str] = {}
    _collect_files(root, "", file_name, entries)
    return entries


def _collect_files(directory: Traversable, prefix: str, file_name: str, entries: dict[str, str]) -> None:
    """Recursively collect one category subtree's matching files into the entries map.

    ``prefix`` accumulates the category-relative directory path of ``directory`` so the collected
    keys need no parent traversal (``Traversable`` has no parent accessor).
    """
    try:
        children = list(directory.iterdir())
    except (OSError, FileNotFoundError, NotADirectoryError):
        return
    for child in children:
        if child.is_dir():
            _collect_files(child, f"{prefix}/{child.name}" if prefix else child.name, file_name, entries)
        elif child.name == file_name:
            try:
                entries[f"{prefix}/{child.name}" if prefix else child.name] = child.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue


def prompt_resources_root() -> Path | None:
    """Return the filesystem path of the packaged prompt resource root when one exists.

    Returns:
        the packaged ``prompt_resources`` directory, or ``None`` when the package is not backed by
        a real filesystem location (zipapp layouts); used only to recognize a local root that points
        at the packaged tree itself, so a ``None`` result simply disables that heuristic.
    """
    root = _resource_files(_PACKAGE).joinpath(_PROMPT_RESOURCES_ROOT)
    if isinstance(root, Path):
        return root
    return None


class PackagedResourceReader:
    """Reader over the packaged prompt resource tree — the ``packaged`` half of the D31 routing.

    Implements the read seam consumed by :mod:`~a2a_t.common.prompt_resources.template_source` and
    :mod:`~a2a_t.common.prompt_resources.json_source`: text reads go through the module-level frozen
    cache and category discovery through the module-level directory cache.
    """

    def read_text(self, key: PromptResourceKey) -> str:
        """Read one UTF-8 text resource from the package (cached; missing is never cached).

        Args:
            key: resource key identifying the file under ``prompt_resources/``.

        Returns:
            the text payload of the resource.

        Raises:
            ResourceNotFoundError: when the resource does not exist in the package.
            OSError: when an existing resource cannot be read.
        """
        return load_text(key)

    def category_types(self, category: str) -> tuple[str, ...]:
        """Return the first-level directory names available under one packaged category.

        Args:
            category: category directory under ``prompt_resources/``, such as ``templates``.

        Returns:
            the sorted directory names; empty when the category exists in no packaged root.
        """
        return list_category_directories(category)

    def category_files(self, category: str, file_name: str) -> dict[str, str]:
        """Return every file of one packaged category matching a file name (reader seam).

        Args:
            category: category directory under ``prompt_resources/``, such as ``templates``.
            file_name: file name of the payloads to collect, such as ``template.md``.

        Returns:
            a mapping of category-relative path (forward slashes) to the UTF-8 text payload; empty
            when the category exists in no packaged root.
        """
        return load_category_files(category, file_name)


def _resource_of(relative_path: str) -> Traversable:
    """Return the traversable resource for one package-relative path."""
    return _resource_files(_PACKAGE).joinpath(relative_path)
