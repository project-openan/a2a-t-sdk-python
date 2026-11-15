"""Expose the annotation database as a case-keyed expectation overlay.

The annotation console is the editor; the review console and the tester need the confirmed values
as an oracle. Requiring an export step in between means the operator has to remember to regenerate
a file after every confirmation, and a stale export silently judges against yesterday's values.

Reading the database directly removes that step: the catalog asks for the confirmed overlay by
case id, and the annotation console stays the only writer. The YAML overlay remains available for
values that must be committed or carried to another environment.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from demo.web.annotate.store import AnnotationStore


def confirmed_overlay(store_path: Path) -> dict[int, dict[str, dict[str, object]]]:
    """Return the confirmed annotation values as ``{case_id: {protocol: block}}``.

    The shape is the one :class:`eval.osp.catalog.CaseCatalog` merges; a missing database is not
    an error (the console may never have been opened in this environment).
    """
    payload = AnnotationStore(Path(store_path)).read_payload()
    if payload is None:
        return {}
    cases = payload.get("cases")
    overlay: dict[int, dict[str, dict[str, object]]] = {}
    for entry in cases if isinstance(cases, list) else ():
        if not isinstance(entry, Mapping):
            continue
        case_id = entry.get("id")
        if not isinstance(case_id, int):
            continue
        blocks = {
            str(protocol): dict(block)
            for protocol, block in entry.items()
            if protocol != "id" and isinstance(block, Mapping)
        }
        if blocks:
            overlay[case_id] = blocks
    return overlay


def overlay_token(store_path: Path) -> str:
    """Return a cheap change token of the annotation database, used for the catalog version."""
    try:
        stat = Path(store_path).stat()
    except OSError:
        return ""
    return f"{stat.st_mtime_ns}:{stat.st_size}"


__all__ = ["confirmed_overlay", "overlay_token"]
