"""Read-only catalog of the OSP energy-saving evaluation cases.

The tester console lists the catalog, lets the operator filter it, and starts one batch item per
(case, protocol). The YAML files stay the source of truth; this module only projects the fields the
console needs and keeps the full document for execution.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from eval.osp.runner import DEFAULT_CASES_DIR, load_cases

PROTOCOL_A2A_T: Final[str] = "a2a-t"
PROTOCOL_A2A: Final[str] = "a2a"
PROTOCOLS: Final[tuple[str, ...]] = (PROTOCOL_A2A_T, PROTOCOL_A2A)


@dataclass(frozen=True)
class CaseSummary:
    """One catalog row shown by the tester console."""

    id: int
    name: str
    dimension: str
    operation: str
    tags: tuple[str, ...]
    validate: str | None
    validate_label: str
    protocols: tuple[str, ...]
    a2a_negotiation_rounds: int
    a2at_negotiation_rounds: int
    file: str

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly catalog row."""
        return {
            "id": self.id,
            "name": self.name,
            "dimension": self.dimension,
            "operation": self.operation,
            "tags": list(self.tags),
            "validate": self.validate,
            "validateLabel": self.validate_label,
            "protocols": list(self.protocols),
            "a2aNegotiationRounds": self.a2a_negotiation_rounds,
            "a2aTNegotiationRounds": self.a2at_negotiation_rounds,
            "file": self.file,
        }


class CatalogReloadError(RuntimeError):
    """A reload could not read the case directory; the previous catalog is still active."""


@dataclass(frozen=True)
class CatalogReload:
    """Outcome of one successful catalog reload."""

    count: int
    added: tuple[int, ...]
    removed: tuple[int, ...]
    changed_files: tuple[str, ...]
    removed_files: tuple[str, ...]
    loaded_at: str
    version: str

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly reload outcome."""
        return {
            "count": self.count,
            "added": list(self.added),
            "removed": list(self.removed),
            "changedFiles": list(self.changed_files),
            "removedFiles": list(self.removed_files),
            "loadedAt": self.loaded_at,
            "version": self.version,
        }


#: One reloadable view of the case directory: readers take the snapshot once, so a reload never
#: exposes a half-updated catalog.
@dataclass(frozen=True)
class _CatalogSnapshot:
    cases: dict[int, dict[str, Any]]
    summaries: dict[int, CaseSummary]
    #: (file name, mtime_ns, size) per case file; also the cheap change detector of one reload.
    signature: tuple[tuple[str, int, int], ...]
    loaded_at: str
    version: str


class CaseCatalog:
    """Loaded OSP cases plus the filter the console applies.

    The YAML files stay the source of truth. ``reload()`` re-reads the directory and swaps the
    snapshot atomically; a failed reload raises :class:`CatalogReloadError` and keeps the previous
    one, so a malformed file never empties the console.
    """

    def __init__(self, cases_dir: Path = DEFAULT_CASES_DIR) -> None:
        self._cases_dir = Path(cases_dir)
        self._reload_lock = threading.Lock()
        self._snapshot = self._build_snapshot()

    @property
    def count(self) -> int:
        """Number of loaded cases."""
        return len(self._snapshot.cases)

    def get(self, case_id: int) -> dict[str, Any] | None:
        """Return one full case document, or ``None`` when unknown."""
        return self._snapshot.cases.get(case_id)

    def summaries(self) -> list[CaseSummary]:
        """Return every catalog row, ordered by id."""
        snapshot = self._snapshot
        return [snapshot.summaries[case_id] for case_id in sorted(snapshot.summaries)]

    def reload(self) -> CatalogReload:
        """Re-read the case directory and swap the snapshot atomically.

        Raises:
            CatalogReloadError: when the directory or one YAML file cannot be read; the catalog
                keeps serving the previous snapshot.
        """
        with self._reload_lock:
            previous = self._snapshot
            try:
                current = self._build_snapshot()
            except Exception as exc:  # noqa: BLE001 - any read/parse failure keeps the old catalog
                raise CatalogReloadError(str(exc)) from exc
            self._snapshot = current
        return _reload_outcome(previous, current)

    def _build_snapshot(self) -> _CatalogSnapshot:
        signature = _signature(self._cases_dir)
        cases = {int(document["id"]): document for document in load_cases(self._cases_dir)}
        summaries = {case_id: self._summarize(document) for case_id, document in cases.items()}
        return _CatalogSnapshot(
            cases=cases,
            summaries=summaries,
            signature=signature,
            loaded_at=_now_iso(),
            version=_version(signature),
        )

    def filter(
        self,
        *,
        query: str = "",
        dimensions: tuple[str, ...] = (),
        tags: tuple[str, ...] = (),
        operations: tuple[str, ...] = (),
        validates: tuple[str, ...] = (),
        protocol: str | None = None,
    ) -> list[CaseSummary]:
        """Return the rows matching every supplied filter (tags match any selected tag)."""
        text = query.strip().lower()
        selected_tags = set(tags)
        selected_validates = set(validates)
        result: list[CaseSummary] = []
        for summary in self.summaries():
            if text and text not in summary.name.lower() and text not in str(summary.id):
                continue
            if dimensions and summary.dimension not in dimensions:
                continue
            if selected_tags and not selected_tags.intersection(summary.tags):
                continue
            if operations and summary.operation not in operations:
                continue
            if selected_validates:
                marker = summary.validate or "unfilled"
                if marker not in selected_validates:
                    continue
            if protocol is not None and protocol not in summary.protocols:
                continue
            result.append(summary)
        return result

    @staticmethod
    def _summarize(document: dict[str, Any]) -> CaseSummary:
        protocols = document["expect"]["protocols"]
        a2at = protocols["a2a-t"]
        a2a = protocols["a2a"]
        path = document.get("_path")
        return CaseSummary(
            id=int(document["id"]),
            name=str(document["name"]),
            dimension=str(document.get("dimension") or ""),
            operation=str(document.get("operation") or ""),
            tags=tuple(str(tag) for tag in document.get("tags") or ()),
            validate=a2at.get("validate"),
            validate_label=str(a2at.get("validate_label") or ""),
            protocols=PROTOCOLS,
            a2a_negotiation_rounds=int(a2a["negotiation"]["expected_rounds"]),
            a2at_negotiation_rounds=int(a2at["negotiation"]["expected_rounds"]),
            file=path.name if isinstance(path, Path) else "",
        )


def _signature(cases_dir: Path) -> tuple[tuple[str, int, int], ...]:
    """Return the (file name, mtime_ns, size) fingerprint of the case directory."""
    parts: list[tuple[str, int, int]] = []
    for path in sorted(cases_dir.glob("*.yaml")):
        try:
            stat = path.stat()
        except OSError:
            parts.append((path.name, -1, -1))
        else:
            parts.append((path.name, stat.st_mtime_ns, stat.st_size))
    return tuple(parts)


def _version(signature: tuple[tuple[str, int, int], ...]) -> str:
    """Return the short stable version id of one case-directory fingerprint."""
    digest = hashlib.sha256(repr(signature).encode("utf-8")).hexdigest()
    return digest[:12]


def _now_iso() -> str:
    """Return the reload time in ISO 8601 UTC form."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _reload_outcome(previous: _CatalogSnapshot, current: _CatalogSnapshot) -> CatalogReload:
    """Diff two catalog snapshots for the reload response."""
    added = tuple(sorted(set(current.cases) - set(previous.cases)))
    removed = tuple(sorted(set(previous.cases) - set(current.cases)))
    before = {name: (mtime, size) for name, mtime, size in previous.signature}
    after = {name: (mtime, size) for name, mtime, size in current.signature}
    # A new file is reported through ``added``; ``changed_files`` only covers files present in
    # both snapshots, so one edit is not counted twice.
    changed_files = tuple(
        name for name, mtime, size in current.signature if name in before and before[name] != (mtime, size)
    )
    removed_files = tuple(name for name, _, _ in previous.signature if name not in after)
    return CatalogReload(
        count=len(current.cases),
        added=added,
        removed=removed,
        changed_files=changed_files,
        removed_files=removed_files,
        loaded_at=current.loaded_at,
        version=current.version,
    )
