"""Read-only catalog of the OSP energy-saving evaluation cases.

The tester console lists the catalog, lets the operator filter it, and starts one batch item per
(case, protocol). The YAML files stay the source of truth for the generated fields; the two
hand-authored expectation blocks (``slot_errors`` and ``negotiation.messages``) may instead live in
one overlay file next to the case directory. The overlay is applied at load time, so every consumer
(the evaluation runner and the tester) sees the same merged document.

Why an overlay: in the yellow zone the generated case files are hot-replaced between runs, so a
direct edit is lost. Keeping the hand-authored values in one separate file makes them survive the
replacement and turns a 100-file merge into a one-file replacement.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import threading
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml

from eval.osp.expectations import negotiation_message_expectation, slot_error_expectation
from eval.osp.runner import DEFAULT_CASES_DIR, load_cases

_LOGGER = logging.getLogger("ran_energy_saving.osp_catalog")

#: One overlay value, keyed by case id and protocol. Both the YAML file and the annotation
#: database are reduced to this shape before they are merged.
OverlayBlocks = Mapping[int, Mapping[str, Mapping[str, object]]]

PROTOCOL_A2A_T: Final[str] = "a2a-t"
PROTOCOL_A2A: Final[str] = "a2a"
PROTOCOLS: Final[tuple[str, ...]] = (PROTOCOL_A2A_T, PROTOCOL_A2A)

#: Format marker of the hand-authored overlay.
OVERLAY_SCHEMA: Final[str] = "osp-expectations-overlay/v1"

#: Default overlay: one file next to the case directory, outside the hot-replaced corpus.
DEFAULT_OVERLAY_PATH: Final[Path] = DEFAULT_CASES_DIR.parent / "expectations-overlay.yaml"

#: The only keys an overlay may contribute: the corpus stays authoritative for everything else.
_OVERLAY_PROTOCOL_KEYS: Final[frozenset[str]] = frozenset({"slot_errors", "negotiation"})
_OVERLAY_NEGOTIATION_KEYS: Final[frozenset[str]] = frozenset({"messages"})


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

    def __init__(
        self,
        cases_dir: Path = DEFAULT_CASES_DIR,
        *,
        overlay_path: Path | None = None,
        annotation_overlay: Callable[[], OverlayBlocks] | None = None,
        annotation_token: Callable[[], str] | None = None,
    ) -> None:
        resolved_cases = Path(cases_dir)
        self._cases_dir = resolved_cases
        self._overlay_path = (
            Path(overlay_path)
            if overlay_path is not None
            else (
                DEFAULT_OVERLAY_PATH
                if resolved_cases.resolve() == DEFAULT_CASES_DIR.resolve()
                else resolved_cases.parent / "expectations-overlay.yaml"
            )
        )
        #: Optional live source of hand-authored values (the annotation database). It is merged
        #: after the YAML overlay, so the operator's latest confirmation wins without an export.
        self._annotation_overlay = annotation_overlay
        self._annotation_token = annotation_token
        self._reload_lock = threading.Lock()
        self._snapshot = self._build_snapshot()

    @property
    def overlay_path(self) -> Path:
        """Return the overlay file this catalog merges (it may not exist)."""
        return self._overlay_path

    @property
    def count(self) -> int:
        """Number of loaded cases."""
        return len(self._snapshot.cases)

    @property
    def version(self) -> str:
        """Return the catalog version; it changes when a case file or the overlay changes."""
        return self._snapshot.version

    def get(self, case_id: int) -> dict[str, Any] | None:
        """Return one full case document, or ``None`` when unknown."""
        return self._snapshot.cases.get(case_id)

    def documents(self) -> list[dict[str, Any]]:
        """Return every full case document, ordered by id.

        The annotation console derives its whole target space from the catalog, so it needs the
        documents rather than the summary rows.
        """
        snapshot = self._snapshot
        return [snapshot.cases[case_id] for case_id in sorted(snapshot.cases)]

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
        signature = _signature(self._cases_dir, self._overlay_path)
        cases = {int(document["id"]): document for document in load_cases(self._cases_dir)}
        _apply_overlay(
            cases,
            _load_overlay(self._overlay_path),
            source=f"overlay {self._overlay_path}",
        )
        token = ""
        if self._annotation_overlay is not None:
            try:
                blocks = self._annotation_overlay()
            except Exception as exc:  # noqa: BLE001 - a broken live source must not kill the corpus
                _LOGGER.warning("annotation overlay could not be read: %s", exc)
                blocks = {}
            _apply_overlay(cases, blocks, lenient=True, source="annotation store")
            if self._annotation_token is not None:
                token = self._annotation_token()
        summaries = {case_id: self._summarize(document) for case_id, document in cases.items()}
        return _CatalogSnapshot(
            cases=cases,
            summaries=summaries,
            signature=signature,
            loaded_at=_now_iso(),
            version=_version(signature, token),
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


def _signature(
    cases_dir: Path,
    overlay_path: Path,
) -> tuple[tuple[str, int, int], ...]:
    """Return the (file name, mtime_ns, size) fingerprint of the corpus and the overlay."""
    parts: list[tuple[str, int, int]] = []
    for path in sorted(cases_dir.glob("*.yaml")):
        try:
            stat = path.stat()
        except OSError:
            parts.append((path.name, -1, -1))
        else:
            parts.append((path.name, stat.st_mtime_ns, stat.st_size))
    if overlay_path.is_file():
        try:
            stat = overlay_path.stat()
        except OSError:
            parts.append((f"<overlay:{overlay_path.name}>", -1, -1))
        else:
            parts.append((f"<overlay:{overlay_path.name}>", stat.st_mtime_ns, stat.st_size))
    return tuple(parts)


def _load_overlay(
    path: Path,
) -> dict[int, dict[str, dict[str, object]]]:
    """Read the hand-authored overlay, or return an empty mapping when it does not exist.

    Raises:
        ValueError: when the file is unreadable, malformed, or names an unknown case/key. A typo
            must fail the load instead of silently leaving a case un-annotated.
    """
    if not path.is_file():
        return {}
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"overlay {path} cannot be read: {exc}") from exc
    if document is None:
        return {}
    if not isinstance(document, Mapping):
        raise ValueError(f"overlay {path} must be a mapping")
    schema = document.get("schema")
    if schema not in (None, OVERLAY_SCHEMA):
        raise ValueError(f"overlay {path} has an unknown schema {schema!r}")
    entries = document.get("cases")
    if entries is None:
        return {}
    if not isinstance(entries, list):
        raise ValueError(f"overlay {path} cases must be a list")
    overlay: dict[int, dict[str, dict[str, object]]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("id"), int):
            raise ValueError(f"overlay {path} has an entry without an integer id")
        case_id = int(entry["id"])
        if case_id in overlay:
            raise ValueError(f"overlay {path} names case {case_id} twice")
        blocks: dict[str, dict[str, object]] = {}
        for protocol, block in entry.items():
            if protocol == "id":
                continue
            if protocol not in PROTOCOLS:
                raise ValueError(f"overlay case {case_id} has an unknown protocol {protocol!r}")
            if not isinstance(block, Mapping):
                raise ValueError(f"overlay case {case_id} {protocol} must be a mapping")
            extra = set(block) - _OVERLAY_PROTOCOL_KEYS
            if extra:
                raise ValueError(
                    f"overlay case {case_id} {protocol} may only set "
                    f"{sorted(_OVERLAY_PROTOCOL_KEYS)}, not {sorted(extra)}"
                )
            negotiation = block.get("negotiation")
            if negotiation is not None:
                if not isinstance(negotiation, Mapping):
                    raise ValueError(
                        f"overlay case {case_id} {protocol}.negotiation must be a mapping"
                    )
                extra = set(negotiation) - _OVERLAY_NEGOTIATION_KEYS
                if extra:
                    raise ValueError(
                        f"overlay case {case_id} {protocol}.negotiation may only set "
                        f"{sorted(_OVERLAY_NEGOTIATION_KEYS)}, not {sorted(extra)}"
                    )
            blocks[protocol] = dict(block)
        overlay[case_id] = blocks
    return overlay


def _apply_overlay(
    cases: MutableMapping[int, dict[str, Any]],
    overlay: OverlayBlocks,
    *,
    lenient: bool = False,
    source: str = "overlay",
) -> None:
    """Merge the overlay into the loaded case documents in place.

    A YAML overlay is strict: a typo fails the load instead of leaving a case silently un-annotated.
    The live annotation database is lenient: one rejected case (for example, an old message block
    whose expected round count has since changed) is logged and skipped rather than taking the
    whole catalog down.

    Raises:
        ValueError: when a strict overlay targets an unknown case, puts ``slot_errors`` on a case
            that does not expect an interception, or leaves a block the expectation parser rejects.
    """
    unknown = sorted(set(overlay) - set(cases))
    if unknown:
        if lenient:
            _LOGGER.warning("%s names unknown cases %s; skipped", source, unknown)
        else:
            raise ValueError(f"overlay names unknown cases: {unknown}")
    for case_id, blocks in overlay.items():
        case = cases.get(case_id)
        if case is None:
            continue
        merged = copy.deepcopy(case) if lenient else case
        try:
            _merge_case(merged, case_id, blocks)
        except (ValueError, KeyError, TypeError) as exc:
            if not lenient:
                raise ValueError(f"{source} case {case_id}: {exc}") from exc
            _LOGGER.warning("%s case %s rejected: %s", source, case_id, exc)
            continue
        if lenient:
            cases[case_id] = merged


def _merge_case(
    case: dict[str, Any],
    case_id: int,
    blocks: Mapping[str, Mapping[str, object]],
) -> None:
    """Merge one case's overlay blocks and validate the merged document."""
    protocols = case["expect"]["protocols"]
    for protocol, block in blocks.items():
        target = protocols.get(protocol)
        if not isinstance(target, dict):
            raise ValueError(f"no {protocol} protocol block")
        if "slot_errors" in block:
            if protocol != PROTOCOL_A2A_T:
                raise ValueError("slot_errors belongs to a2a-t only")
            if target.get("validate") != "blocked":
                raise ValueError("slot_errors on a non-blocked case")
            target["slot_errors"] = block["slot_errors"]
        negotiation = block.get("negotiation")
        if isinstance(negotiation, Mapping) and "messages" in negotiation:
            current = target.get("negotiation")
            if not isinstance(current, dict):
                raise ValueError(f"{protocol} has no negotiation block to extend")
            current["messages"] = negotiation["messages"]
    # Parse the merged document with the same functions the judges use, so a malformed overlay
    # fails at load time instead of turning every run red.
    slot_error_expectation(case, PROTOCOL_A2A_T)
    for protocol in PROTOCOLS:
        negotiation_message_expectation(case, protocol)


def _version(signature: tuple[tuple[str, int, int], ...], token: str = "") -> str:
    """Return the short stable version id of one corpus + overlay fingerprint."""
    material = repr(signature) + (f"\nannotation:{token}" if token else "")
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
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
