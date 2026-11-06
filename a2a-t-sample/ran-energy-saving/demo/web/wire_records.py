"""Read the redacted wire logs of one run.

``wire.jsonl`` holds the engineer-client hop; the OSS Agent writes its own ``wire-oss.jsonl`` so
two processes never append to one file. Both are merged here in event order. The records are
already redacted by the writer, so no second scrub is applied.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

#: Newest records kept by the inspectors (a long run must not return an unbounded body).
WIRE_RECORD_LIMIT: Final[int] = 200


def read_wire_records(runs_dir: Path, run_id: str) -> list[dict[str, object]]:
    """Return the redacted wire records of one run, bounded and in event order."""
    records: list[dict[str, object]] = []
    for name in ("wire.jsonl", "wire-oss.jsonl"):
        records.extend(_read_wire_file(Path(runs_dir) / run_id / name))
    records.sort(key=lambda record: str(record.get("ts") or ""))
    return records[-WIRE_RECORD_LIMIT:]


def _read_wire_file(path: Path) -> list[dict[str, object]]:
    """Read one wire log file, skipping malformed lines."""
    if not path.is_file():
        return []
    records: list[dict[str, object]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError:
        return []
    return records


__all__ = ["WIRE_RECORD_LIMIT", "read_wire_records"]
