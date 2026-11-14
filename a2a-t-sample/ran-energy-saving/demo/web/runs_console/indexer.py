"""Assemble timeline sources, cache per-run indexes and read process-log windows."""

from __future__ import annotations

import json
import os
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from demo.web.runs_console.timeline import (
    ProcessLine,
    TimelineDocument,
    TimelineEntry,
    WireRef,
    build_entries,
    parse_process_log,
)

#: Process-log files the launcher writes into ``RUN_DIR``.
PROCESS_LOG_NAMES: Final[tuple[str, ...]] = (
    "ui",
    "server",
    "oss",
    "gateway",
    "mie",
    "nms-sim",
    "registry",
)

#: Bytes read from each end of an oversized process log.
DEFAULT_LOG_WINDOW_BYTES: Final[int] = 2 * 1024 * 1024

#: Entry budget shared by every cached run.
DEFAULT_CACHE_ENTRIES: Final[int] = 100_000

#: Safety cap of the durable server trace of one run.
MAX_SERVER_SPANS: Final[int] = 20_000

#: Safety cap of the wire records of one run.
MAX_WIRE_RECORDS: Final[int] = 100_000

_SERVER_SPAN_PREFIXES: Final[tuple[str, ...]] = ("llm_server_", "negsrv_", "srv_")


def split_transcript(
    transcript: Mapping[str, object] | None,
) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]], list[Mapping[str, object]]]:
    """Split one transcript into client events, decisions and server trace rows."""
    if not isinstance(transcript, Mapping):
        return [], [], []
    raw_events = transcript.get("events")
    raw_decisions = transcript.get("decisions")
    events = [event for event in raw_events if isinstance(event, Mapping)] if isinstance(raw_events, list) else []
    decisions = (
        [decision for decision in raw_decisions if isinstance(decision, Mapping)]
        if isinstance(raw_decisions, list)
        else []
    )
    client_events: list[Mapping[str, object]] = []
    server_spans: list[Mapping[str, object]] = []
    for event in events:
        if _is_server_trace_row(event):
            server_spans.append(event)
        else:
            client_events.append(event)
    return client_events, decisions, server_spans


def merge_server_spans(
    primary: Sequence[Mapping[str, object]],
    fallback: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    """Return ``primary`` then the fallback spans whose ``spanId`` is not already present."""
    merged = list(primary)
    seen = {_span_id(span) for span in primary if _span_id(span)}
    for span in fallback:
        span_id = _span_id(span)
        if span_id:
            if span_id in seen:
                continue
            seen.add(span_id)
        merged.append(span)
    return merged


@dataclass(slots=True)
class _Cached:
    fingerprint: tuple[object, ...]
    document: TimelineDocument
    entries: list[TimelineEntry]
    touched: int


@dataclass(slots=True)
class TimelineCache:
    """An LRU-by-total-entries cache of normalized timelines."""

    max_entries: int = DEFAULT_CACHE_ENTRIES
    _entries: dict[str, _Cached] = field(default_factory=dict)
    _clock: int = 0

    def get(
        self,
        key: str,
        fingerprint: tuple[object, ...],
        build: Callable[[], TimelineDocument],
    ) -> list[TimelineEntry]:
        """Return the cached entries, rebuilding when the fingerprint changed."""
        cached = self._entries.get(key)
        if cached is not None and cached.fingerprint == fingerprint:
            self._clock += 1
            cached.touched = self._clock
            return cached.entries
        document = build()
        entries = build_entries(document)
        self._clock += 1
        self._entries[key] = _Cached(
            fingerprint=fingerprint,
            document=document,
            entries=entries,
            touched=self._clock,
        )
        self._evict()
        return entries

    def document(self, key: str) -> TimelineDocument | None:
        """Return the raw sources behind one cached timeline."""
        cached = self._entries.get(key)
        return cached.document if cached is not None else None

    def invalidate(self, key: str) -> None:
        """Drop one run from the cache."""
        self._entries.pop(key, None)

    @property
    def entry_count(self) -> int:
        """Return how many entries are cached across every run."""
        return sum(len(cached.entries) for cached in self._entries.values())

    def _evict(self) -> None:
        while self.entry_count > self.max_entries and len(self._entries) > 1:
            victim = min(self._entries, key=lambda key: self._entries[key].touched)
            del self._entries[victim]


def read_server_trace_jsonl(
    path: Path,
    *,
    limit: int = MAX_SERVER_SPANS,
) -> tuple[list[Mapping[str, object]], list[str]]:
    """Read one durable server trace, keeping both ends when it exceeds ``limit``."""
    if not path.is_file():
        return [], []
    half = max(1, limit // 2)
    first: list[Mapping[str, object]] = []
    tail: deque[Mapping[str, object]] = deque(maxlen=limit - half)
    total = 0
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except ValueError:
                    continue
                if not isinstance(record, Mapping):
                    continue
                total += 1
                if len(first) < half:
                    first.append(record)
                else:
                    tail.append(record)
    except OSError:
        return [], []
    spans = [*first, *tail]
    if total <= limit:
        return spans, []
    return spans, [f"server-trace.jsonl 超过 {limit} 条，仅保留首尾各 {half} 条"]


def cap_wire_records(
    records: Sequence[Mapping[str, object]],
    *,
    limit: int = MAX_WIRE_RECORDS,
) -> tuple[list[Mapping[str, object]], str | None]:
    """Keep both ends of a runaway wire log instead of an unbounded index."""
    if len(records) <= limit:
        return list(records), None
    half = max(1, limit // 2)
    kept = [*records[:half], *records[len(records) - (limit - half) :]]
    return kept, f"wire 记录超过 {limit} 条，仅保留首尾各 {half} 条"


def read_wire_refs(
    runs_dir: Path,
    run_id: str,
    *,
    limit: int = MAX_WIRE_RECORDS,
) -> tuple[list[WireRef], list[str]]:
    """Index both wire logs without keeping their payloads in memory."""
    refs: list[WireRef] = []
    for name in ("wire.jsonl", "wire-oss.jsonl"):
        path = runs_dir / run_id / name
        if not path.is_file():
            continue
        try:
            with path.open(encoding="utf-8") as handle:
                for line_no, line in enumerate(handle, start=1):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        record = json.loads(stripped)
                    except ValueError:
                        continue
                    if not isinstance(record, Mapping):
                        continue
                    refs.append(
                        WireRef(
                            file=name,
                            line_no=line_no,
                            ts=_text(record.get("ts")) or None,
                            dir=_text(record.get("dir")) or _text(record.get("direction")),
                            method=_text(record.get("method")) or _text(record.get("kind")),
                            url=_text(record.get("url")) or _text(record.get("path")),
                            status=record.get("status"),
                            search_prefix=json.dumps(record, ensure_ascii=False)[:512],
                        )
                    )
        except OSError:
            continue
    refs.sort(key=lambda ref: (ref.ts is None, ref.ts or "", ref.file, ref.line_no))
    if len(refs) <= limit:
        return refs, []
    half = max(1, limit // 2)
    kept = [*refs[:half], *refs[len(refs) - (limit - half) :]]
    return kept, [f"wire 记录超过 {limit} 条，仅保留首尾各 {half} 条"]


def read_process_logs(
    run_dir: Path | None,
    run_id: str,
    *,
    window_bytes: int = DEFAULT_LOG_WINDOW_BYTES,
    names: Sequence[str] = PROCESS_LOG_NAMES,
) -> tuple[list[ProcessLine], list[str]]:
    """Read the run-id lines from the whitelisted process logs.

    A file larger than ``2 * window_bytes`` is read from both ends only; the UI is told that the
    middle was skipped instead of silently pretending the log is complete.
    """
    if run_dir is None or not run_dir.is_dir():
        return [], []
    lines: list[ProcessLine] = []
    warnings: list[str] = []
    for name in names:
        path = run_dir / f"{name}.log"
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size <= 2 * window_bytes:
            chunk = _read_bytes(path, 0, size)
            lines.extend(parse_process_log(chunk, file=name, run_id=run_id))
            continue
        head = _read_head(path, window_bytes)
        tail = _read_tail(path, window_bytes)
        lines.extend(parse_process_log(head, file=name, run_id=run_id))
        tail_lines = parse_process_log(tail, file=name, run_id=run_id)
        lines.extend(tail_lines)
        warnings.append(f"{name}.log 过大，仅索引首尾各 {window_bytes // 1024}KB")
    for index, line in enumerate(lines, start=1):
        line.line_no = index
    return lines, warnings


def _text(value: object) -> str:
    return value if isinstance(value, str) else "" if value is None else str(value)


def _is_server_trace_row(event: Mapping[str, object]) -> bool:
    if str(event.get("entryType") or "") != "trace":
        return False
    side = str(event.get("side") or "").lower()
    if side == "server":
        return True
    if side == "client":
        return False
    span_id = str(event.get("spanId") or "")
    return span_id.startswith(_SERVER_SPAN_PREFIXES)


def _span_id(span: Mapping[str, object]) -> str:
    value = span.get("spanId")
    return value if isinstance(value, str) else ""


def _read_bytes(path: Path, offset: int, size: int) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            return handle.read(size).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _read_head(path: Path, window_bytes: int) -> str:
    data = _read_bytes(path, 0, window_bytes).encode("utf-8", errors="replace")
    if not data.endswith(b"\n") and b"\n" in data:
        data = data.rsplit(b"\n", 1)[0] + b"\n"
    return data.decode("utf-8", errors="replace")


def _read_tail(path: Path, window_bytes: int) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - window_bytes))
            data = handle.read()
    except OSError:
        return ""
    if b"\n" in data:
        data = data.split(b"\n", 1)[1]
    return data.decode("utf-8", errors="replace")


__all__ = [
    "DEFAULT_CACHE_ENTRIES",
    "DEFAULT_LOG_WINDOW_BYTES",
    "MAX_SERVER_SPANS",
    "MAX_WIRE_RECORDS",
    "PROCESS_LOG_NAMES",
    "TimelineCache",
    "cap_wire_records",
    "merge_server_spans",
    "read_process_logs",
    "read_server_trace_jsonl",
    "read_wire_refs",
    "split_transcript",
]
