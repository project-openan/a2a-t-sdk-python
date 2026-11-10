"""Read-only evidence access for one AI evaluation batch.

The Web UI truncates the wire inspector to the newest 200 records, which loses the initial
``message:send`` of a long Task. This module reads the artifacts the sample already writes in full:
``wire.jsonl`` / ``wire-error.jsonl``, the client store (``runs``, ``task_snapshots``) and the two
Task/binding databases. Nothing here calls HTTP, writes files or raises on a missing schema, so the
evidence floor still works after the UI has stopped.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Final

#: File names of the two wire logs inside ``runs/<runId>/``.
WIRE_FILENAME: Final[str] = "wire.jsonl"
WIRE_ERROR_FILENAME: Final[str] = "wire-error.jsonl"

#: Client-store and Task-store file names inside ``RUNS_DIR``.
CLIENT_STATE_FILENAME: Final[str] = "client-state.sqlite3"
TASK_STATE_FILENAME: Final[str] = "task-status.sqlite3"
OSS_TASK_STATE_FILENAME: Final[str] = "oss-task-status.sqlite3"

#: Key fragments that mark a credential value; the whole value is replaced by ``***``.
_SENSITIVE_KEY_PARTS: Final[tuple[str, ...]] = (
    "token",
    "password",
    "secret",
    "credential",
    "authorization",
)

#: Task-id keys searched recursively inside one wire record.
_TASK_ID_KEYS: Final[frozenset[str]] = frozenset({"taskId", "task_id", "id"})

#: Header values such as ``Bearer abc`` / ``Basic dXNlcjpwYXNz`` inside an arbitrary string.
_AUTH_VALUE_RE: Final[re.Pattern[str]] = re.compile(r"(?i)\b(Bearer|Basic)\s+[^\s\"',;]+")


def read_wire(
    path: Path,
    *,
    direction: str | None = None,
    kind: str | None = None,
    task_id: str | None = None,
    grep: str | None = None,
    limit: int | None = None,
) -> list[dict[str, object]]:
    """Read a wire JSONL file in full and filter its records.

    Args:
        path: The ``wire.jsonl`` file, or the run directory that holds it.
        direction: Keep records whose ``dir`` equals this value (``in`` / ``out``).
        kind: Keep records whose ``type``/``kind``/``method``, or nested ``body.method`` /
            ``body.performative``, equals this value.
        task_id: Keep records that carry this value under any nested ``taskId``/``task_id``/``id``.
        grep: Case-insensitive substring over the serialized record.
        limit: Return only the first ``limit`` matches (``None`` means every match). This is how
            the initial ``message:send`` stays reachable on a long Task.

    Returns:
        Matching records in file order. Malformed lines are skipped.
    """
    if limit is not None and limit <= 0:
        return []
    needle = grep.lower() if grep is not None else None
    matches: list[dict[str, object]] = []
    for line, record in _read_jsonl_lines(_resolve_file(path, WIRE_FILENAME)):
        if direction is not None and record.get("dir") != direction:
            continue
        if kind is not None and not _kind_matches(record, kind):
            continue
        if task_id is not None and not _has_task_id(record, task_id):
            continue
        if needle is not None and needle not in line.lower():
            continue
        matches.append(record)
        if limit is not None and len(matches) >= limit:
            break
    return matches


def read_wire_errors(path: Path) -> list[dict[str, object]]:
    """Read the whole ``wire-error.jsonl`` of a run, skipping malformed lines.

    Args:
        path: The ``wire-error.jsonl`` file, or the run directory that holds it.
    """
    return _read_jsonl(_resolve_file(path, WIRE_ERROR_FILENAME))


def read_run_row(runs_dir: Path, run_id: str) -> dict[str, object] | None:
    """Return the ``runs`` row of one run from ``client-state.sqlite3``.

    Returns ``None`` when the database, the table or the row is absent; a missing schema never
    raises.
    """
    rows = _query(
        Path(runs_dir) / CLIENT_STATE_FILENAME,
        "SELECT * FROM runs WHERE run_id=?",
        (run_id,),
    )
    return rows[0] if rows else None


def read_transcript(runs_dir: Path, run_id: str) -> Mapping[str, object] | None:
    """Return the run's persisted ``transcript_json`` document, or ``None``."""
    row = read_run_row(runs_dir, run_id)
    if row is None:
        return None
    return _parse_mapping(row.get("transcript_json"))


def read_trace(runs_dir: Path, run_id: str) -> Mapping[str, object] | None:
    """Return the run's persisted ``trace_json`` document, or ``None``."""
    row = read_run_row(runs_dir, run_id)
    if row is None:
        return None
    return _parse_mapping(row.get("trace_json"))


def read_task_snapshot(runs_dir: Path, run_id: str) -> dict[str, object] | None:
    """Return the last observed Task snapshot of one run.

    The ``payload_json`` column is also exposed as a parsed ``payload`` key. Returns ``None`` when
    the database, the table or the row is absent.
    """
    rows = _query(
        Path(runs_dir) / CLIENT_STATE_FILENAME,
        "SELECT * FROM task_snapshots WHERE run_id=?",
        (run_id,),
    )
    if not rows:
        return None
    row = rows[0]
    row["payload"] = _parse_mapping(row.get("payload_json")) or {}
    return row


def lookup_bindings(
    runs_dir: Path,
    *,
    task_id: str | None = None,
    context_id: str | None = None,
) -> list[dict[str, object]]:
    """Find local Task ownership for one task/context id.

    Two sources are searched, in this order: ``task-status.sqlite3.tasks`` (the RAN server's Task
    rows) and ``oss-task-status.sqlite3.downstream_bindings`` (the pure-A2A outer/inner binding).
    Each result is tagged with ``source`` = ``task-store`` or ``oss-binding``. A missing database or
    table yields an empty list.

    Args:
        runs_dir: The ``RUNS_DIR`` that holds both databases.
        task_id: Match a Task id, a Task context id, or a binding's inner/outer Task id.
        context_id: Match a Task context id or a binding's inner context id.
    """
    if task_id is None and context_id is None:
        return []

    results: list[dict[str, object]] = []
    task_rows = _query(
        Path(runs_dir) / TASK_STATE_FILENAME,
        "SELECT task_id, context_id, state, run_id, summary FROM tasks "
        "ORDER BY task_id, context_id",
        (),
    )
    for row in task_rows:
        if _matches_task_row(row, task_id=task_id, context_id=context_id):
            row["source"] = "task-store"
            results.append(row)

    binding_rows = _query(
        Path(runs_dir) / OSS_TASK_STATE_FILENAME,
        "SELECT inner_task_id, inner_context_id, outer_task_id, run_id, owner, protocol, state "
        "FROM downstream_bindings ORDER BY inner_task_id, inner_context_id",
        (),
    )
    for row in binding_rows:
        if _matches_binding_row(row, task_id=task_id, context_id=context_id):
            row["source"] = "oss-binding"
            results.append(row)
    return results


def redact(value: object) -> object:
    """Return ``value`` with credentials replaced by ``***``.

    Secrets are recognized two ways: a mapping key whose lowercase name contains ``token`` /
    ``password`` / ``secret`` / ``credential`` / ``authorization``, and a ``Bearer <x>`` /
    ``Basic <x>`` sequence inside any string. Mappings and lists are recursed; scalar types are
    preserved.
    """
    if isinstance(value, Mapping):
        return {
            str(key): "***" if _is_sensitive_key(key) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        return _AUTH_VALUE_RE.sub(lambda match: f"{match.group(1)} ***", value)
    return value


def excerpt(value: object, *, max_lines: int = 20) -> str:
    """Render one evidence value as redacted text, capped at ``max_lines`` lines.

    Mappings and lists are JSON-serialized with two-space indentation; strings are used as-is.
    When the text is cut, a trailing ``… (truncated)`` line marks the omission.
    """
    safe = redact(value)
    if isinstance(safe, str):
        text = safe
    else:
        try:
            text = json.dumps(safe, indent=2, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(safe)
    lines = text.splitlines()
    bounded = max(max_lines, 0)
    if len(lines) > bounded:
        lines = lines[:bounded]
        lines.append("… (truncated)")
    return "\n".join(lines)


def _resolve_file(path: Path, filename: str) -> Path:
    """Return ``path`` itself, or ``path/filename`` when a run directory was passed."""
    resolved = Path(path)
    return resolved / filename if resolved.is_dir() else resolved


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    """Read one JSONL file; skip malformed or non-object lines and never raise."""
    return [record for _line, record in _read_jsonl_lines(path)]


def _read_jsonl_lines(path: Path) -> list[tuple[str, dict[str, object]]]:
    """Read one JSONL file, keeping each raw line next to its parsed object."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    records: list[tuple[str, dict[str, object]]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, Mapping):
            records.append((line, dict(parsed)))
    return records


def _kind_matches(record: Mapping[str, object], kind: str) -> bool:
    """Return whether a record's type/method or nested body method/performative is ``kind``."""
    for key in ("type", "kind", "method"):
        if record.get(key) == kind:
            return True
    body = record.get("body")
    if isinstance(body, Mapping):
        for key in ("method", "performative"):
            if body.get(key) == kind:
                return True
    return False


def _has_task_id(value: object, task_id: str) -> bool:
    """Return whether ``value`` carries ``task_id`` under any nested task-id key."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in _TASK_ID_KEYS and _as_text(item) == task_id:
                return True
            if _has_task_id(item, task_id):
                return True
        return False
    if isinstance(value, list):
        return any(_has_task_id(item, task_id) for item in value)
    return False


def _as_text(value: object) -> str | None:
    """Return a scalar identity as text, or ``None`` for non-scalars."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _matches_task_row(
    row: Mapping[str, object],
    *,
    task_id: str | None,
    context_id: str | None,
) -> bool:
    """Return whether a Task row matches the supplied task or context id."""
    candidates = {_as_text(row.get("task_id")), _as_text(row.get("context_id"))}
    eliminated = candidates - {None}
    if task_id is not None and task_id in eliminated:
        return True
    return context_id is not None and context_id in eliminated


def _matches_binding_row(
    row: Mapping[str, object],
    *,
    task_id: str | None,
    context_id: str | None,
) -> bool:
    """Return whether a downstream binding matches the supplied task or context id."""
    if task_id is not None:
        if task_id in {
            _as_text(row.get("inner_task_id")),
            _as_text(row.get("outer_task_id")),
        }:
            return True
    return context_id is not None and context_id == _as_text(row.get("inner_context_id"))


def _query(
    path: Path,
    sql: str,
    params: tuple[object, ...],
) -> list[dict[str, object]]:
    """Run one read-only query, returning an empty list on a missing file, table or database."""
    connection = _readonly_connection(path)
    if connection is None:
        return []
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(sql, params).fetchall()
        return [{str(key): row[key] for key in row.keys()} for row in rows]
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def _readonly_connection(path: Path) -> sqlite3.Connection | None:
    """Open one read-only connection; ``None`` when the file does not exist or cannot be opened."""
    database = Path(path).expanduser()
    if not database.is_file():
        return None
    try:
        return sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


def _parse_mapping(value: object) -> Mapping[str, object] | None:
    """Parse one JSON text column, returning ``None`` unless it is a JSON object."""
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def _is_sensitive_key(key: object) -> bool:
    """Return whether a mapping key names a credential."""
    name = str(key).lower()
    return any(part in name for part in _SENSITIVE_KEY_PARTS)


__all__ = [
    "CLIENT_STATE_FILENAME",
    "OSS_TASK_STATE_FILENAME",
    "TASK_STATE_FILENAME",
    "WIRE_ERROR_FILENAME",
    "WIRE_FILENAME",
    "excerpt",
    "lookup_bindings",
    "read_run_row",
    "read_task_snapshot",
    "read_trace",
    "read_transcript",
    "read_wire",
    "read_wire_errors",
    "redact",
]
