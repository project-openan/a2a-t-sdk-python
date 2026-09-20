"""Built-in fallback for missing ``local_file`` business content (Java ADR 0005 overlay).

Port of the Java ``BuiltinFallbackWarnings`` helper plus the classpath fallback of the
``LocalFilePrompt*Loader`` trio: in ``local_file`` mode a routed resource that is missing from the
local frozen snapshot falls back to the packaged copy, so customers only need to place the
templates, slot schemas, scenario catalogs and vocabularies they actually override under the local
root. The packaged copy is only consulted when the local snapshot misses the path — a locally
captured file always wins (local-first overlay). Bare scenario codes resolve in the Java two-phase
order: the whole local snapshot is probed first (a local file wins whatever its type directory or
layout), and only then the packaged candidates (:func:`resolve_bare_code`).

Every successful packaged fallback is reported with a one-time warning per resource path:

.. code-block:: text

    prompt_resource_builtin_fallback path=prompt_resources/templates/.../template.md source=packaged

(``source=packaged`` is this port's term for the Java warning's ``source=classpath``.)

The warning path is the ``prompt_resources/``-prefixed resource path — exact for a path-form
identifier, and the same wildcard locator the Java classpath loaders use for a bare scenario code
(``prompt_resources/<category>/*/network-layer/<code>/v1/<language>/<file> (or the layout without
the network-layer segment)``). It is emitted only after the packaged load succeeded — a resource
that is missing both locally and in the package fails with the plain resource-not-found error and
never warns. The dedup key is the reported path itself: one access object warns at most once per
locator/exact path, so the same physical file addressed once as a bare code and once as a full URI
may warn twice (Java behaves the same, its dedup key being the ``fallbackPath`` string). The
warning is at-most-once in practice; under a tight multi-thread race two threads can pass the
membership check together and both log (plain set mutation is GIL-atomic, but the check-then-add
sequence is not, unlike the atomic ``ConcurrentHashMap``-backed dedup of the Java port).
"""

from __future__ import annotations

import logging
from typing import Final, Sequence

from a2a_t.core.errors.exceptions import ResourceNotFoundError
from a2a_t.core.prompt_resource_key import PromptResourceKey

from .json_source import ResourceReader
from .models import SOURCE_LOCAL, SOURCE_PACKAGED

__all__ = ["BuiltinFallbackReader", "resolve_bare_code", "warn_once"]

logger = logging.getLogger(__name__)

#: Log message marker of the one-time built-in fallback warning (Java ``BuiltinFallbackWarnings``).
_FALLBACK_MESSAGE: Final[str] = "prompt_resource_builtin_fallback path=%s source=%s"


def warn_once(warned_paths: set[str], resource_path: str) -> None:
    """Emit the built-in fallback warning for one resource path at most once.

    Args:
        warned_paths: dedup set shared by every fallback of one access object; the path is added
            on the first warning. At-most-once in practice — under a tight multi-thread race two
            threads can both log (see the module docstring).
        resource_path: the reported path of the resource served from the package (the exact
            ``prompt_resources/``-prefixed path, or the wildcard locator for a bare code).
    """
    if resource_path in warned_paths:
        return
    warned_paths.add(resource_path)
    logger.warning(_FALLBACK_MESSAGE, resource_path, SOURCE_PACKAGED)


def resolve_bare_code(
    reader: ResourceReader,
    keys: Sequence[PromptResourceKey],
    probe_hint: str,
) -> tuple[PromptResourceKey, str]:
    """Resolve one bare scenario code in the Java ``LocalFilePrompt*Loader`` two-phase order.

    Phase 1 probes the whole local snapshot across the candidate keys, so a locally captured file
    wins no matter which type directory or layout it sits under (a packaged candidate that comes
    earlier in the probe order must not shadow it). Phase 2 probes the packaged tree; for the
    overlay reader a packaged hit emits the built-in fallback warning once with ``probe_hint`` —
    the wildcard locator of the bare code — after the packaged load succeeded. A reader without
    the overlay (packaged mode) simply probes its candidates in order without warning.

    The two-phase capability is dispatched on the concrete :class:`BuiltinFallbackReader` — the
    only reader in the codebase that fuses two sources — so single-source readers keep the plain
    probe; a future second overlay reader must extend this dispatch.

    Args:
        reader: routed reader resolving the category.
        keys: candidate resource keys of the bare code, in probe order.
        probe_hint: the Java-parity wildcard locator used for the fallback warning and the
            not-found failure.

    Returns:
        the resolved resource key and its text payload.

    Raises:
        ResourceNotFoundError: when no candidate matches in either phase.
    """
    if isinstance(reader, BuiltinFallbackReader):
        return reader._resolve_bare_code(keys, probe_hint)
    for key in keys:
        try:
            return key, reader.read_text(key)
        except ResourceNotFoundError:
            continue
    raise ResourceNotFoundError("Prompt resource file does not exist.", probe_hint)


class BuiltinFallbackReader:
    """Reader that overlays the packaged tree under a local frozen snapshot.

    Satisfies the :class:`~a2a_t.common.prompt_resources.json_source.ResourceReader` seam consumed
    by the generic loaders, so it slots into :class:`~a2a_t.common.prompt_resources.resource_access.
    LocalFilePromptResourceAccess` as the routed reader without touching the loaders. Local reads
    ride the captured snapshot (D9); only snapshot misses fall through to the package.
    """

    def __init__(self, snapshot: ResourceReader, packaged_reader: ResourceReader) -> None:
        """Wire one overlay reader over a local snapshot and the packaged tree.

        Args:
            snapshot: the frozen local snapshot serving the locally captured files.
            packaged_reader: the packaged reader serving the built-in fallback copies.
        """
        self._snapshot = snapshot
        self._packaged_reader = packaged_reader
        self._warned_paths: set[str] = set()

    def read_text(self, key: PromptResourceKey) -> str:
        """Read one resource from the local snapshot, falling back to the package on a miss.

        Args:
            key: resource key identifying the file under ``prompt_resources/``.

        Returns:
            the locally captured text when present, otherwise the packaged text.

        Raises:
            ResourceNotFoundError: when the resource is missing both locally and in the package;
                no fallback warning is emitted for a double miss.
        """
        try:
            return self._snapshot.read_text(key)
        except ResourceNotFoundError:
            text = self._packaged_reader.read_text(key)
            warn_once(self._warned_paths, key.relative_path())
            return text

    def _resolve_bare_code(self, keys: Sequence[PromptResourceKey], probe_hint: str) -> tuple[PromptResourceKey, str]:
        """Run the two-phase bare-code resolution over the snapshot and the package (Java order)."""
        for key in keys:
            try:
                return key, self._snapshot.read_text(key)
            except ResourceNotFoundError:
                continue
        for key in keys:
            try:
                text = self._packaged_reader.read_text(key)
            except ResourceNotFoundError:
                continue
            warn_once(self._warned_paths, probe_hint)
            return key, text
        raise ResourceNotFoundError("Prompt resource file does not exist.", probe_hint)

    def category_types(self, category: str) -> tuple[str, ...]:
        """Return the union of the local and packaged first-level directory names.

        The union keeps bare scenario-code probing able to discover built-in extension types that
        the local root does not shadow, mirroring the Java classpath-loader discovery.

        Args:
            category: category directory under ``prompt_resources/``, such as ``templates``.

        Returns:
            the sorted directory names available in either source.
        """
        return tuple(
            sorted({*self._snapshot.category_types(category), *self._packaged_reader.category_types(category)})
        )

    def category_files(self, category: str, file_name: str) -> dict[str, str]:
        """Return the overlay of one category's files: the package under the local root.

        Args:
            category: category directory under ``prompt_resources/``, such as ``templates``.
            file_name: file name of the payloads to collect, such as ``template.md``.

        Returns:
            a mapping of category-relative path to the text payload; a locally captured file wins
            over its packaged counterpart.
        """
        merged = dict(self._packaged_reader.category_files(category, file_name))
        merged.update(self._snapshot.category_files(category, file_name))
        return merged

    def category_files_with_origin(self, category: str, file_name: str) -> dict[str, tuple[str, str]]:
        """Return one category's overlay files with their effective origin per path.

        Args:
            category: category directory under ``prompt_resources/``, such as ``templates``.
            file_name: file name of the payloads to collect, such as ``template.md``.

        Returns:
            a mapping of category-relative path to ``(text, origin)`` pairs, the origin being
            :data:`~a2a_t.common.prompt_resources.models.SOURCE_LOCAL` for locally captured files
            and :data:`~a2a_t.common.prompt_resources.models.SOURCE_PACKAGED` for the packaged
            copies.
        """
        merged: dict[str, tuple[str, str]] = {
            path: (text, SOURCE_PACKAGED)
            for path, text in self._packaged_reader.category_files(category, file_name).items()
        }
        merged.update(
            {path: (text, SOURCE_LOCAL) for path, text in self._snapshot.category_files(category, file_name).items()}
        )
        return merged
