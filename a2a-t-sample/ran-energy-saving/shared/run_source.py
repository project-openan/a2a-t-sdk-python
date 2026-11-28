"""Normalization of the request origin recorded on every client run.

A run can start from several places: the presentation page, the technical Run Explorer, the
Excel tester console, the A2A gateway, a direct API call or the CLI. The origin is recorded on the
run so the internal run console can answer "who/where did this request come from" without reading
access logs. Values are plain JSON and must never contain credentials.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

#: Source kinds the console knows how to label.
RUN_SOURCE_KINDS: Final[tuple[str, ...]] = (
    "presentation",
    "explorer",
    "tester",
    "gateway",
    "api",
    "cli",
)

#: Kind used when a caller does not declare one.
DEFAULT_RUN_SOURCE_KIND: Final[str] = "api"


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def normalize_run_source(
    source: Mapping[str, object] | None,
    *,
    default_kind: str = DEFAULT_RUN_SOURCE_KIND,
) -> dict[str, object]:
    """Return one JSON-serializable source document with a known kind and timestamp.

    Unknown kinds fall back to ``default_kind`` so a stale page cannot invent a new label. Empty
    values are dropped; nested values are kept as-is because callers only pass identifiers.
    """
    data: dict[str, object] = dict(source) if isinstance(source, Mapping) else {}
    kind = str(data.get("kind") or default_kind)
    if kind not in RUN_SOURCE_KINDS:
        kind = default_kind
    data["kind"] = kind
    data.setdefault("receivedAt", now_iso())
    return {key: value for key, value in data.items() if value not in (None, "")}


__all__ = [
    "DEFAULT_RUN_SOURCE_KIND",
    "RUN_SOURCE_KINDS",
    "normalize_run_source",
    "now_iso",
]
