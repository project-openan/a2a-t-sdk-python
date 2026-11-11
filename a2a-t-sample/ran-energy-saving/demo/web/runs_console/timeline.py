"""Normalize client, server, wire and process records into one filterable timeline.

The run console keeps payloads out of the index: an entry stores only a short summary, a search
prefix and a ``ref`` that locates the original record. This module deliberately has no I/O.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Final, Literal

TimelineSource = Literal["client", "server", "wire", "process"]

_SOURCE_ORDER: Final[dict[str, int]] = {
    "client": 0,
    "server": 1,
    "wire": 2,
    "process": 3,
}
_SEARCH_LIMIT: Final[int] = 4096
_PAYLOAD_PREFIX_LIMIT: Final[int] = 2048

_TEXT_LOG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\S+)\s+(?P<level>[A-Z]+)\s+(?P<logger>\S+)\s*"
    r"(?:\[(?P<context>[^\]]*)\])?\s*(?P<message>.*)$"
)
_RUN_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?:^|\s)run_id=([^\s\]]+)")


@dataclass(slots=True)
class ProcessLine:
    """One process-log line that carries the selected run id."""

    file: str
    line_no: int
    ts: str | None
    level: str
    text: str
    detail: str = ""


@dataclass(slots=True)
class TimelineEntry:
    """One normalized log entry; payload is fetched separately through ``ref``."""

    id: str
    source: TimelineSource
    ts: str | None
    phase: str | None
    phase_inferred: bool
    status: str
    title: str
    summary: str
    search_text: str
    payload_available: bool
    ref: dict[str, object]

    def to_public(self) -> dict[str, object]:
        """Return the JSON shape sent to the browser (no payload)."""
        return {
            "id": self.id,
            "source": self.source,
            "ts": self.ts,
            "phase": self.phase,
            "phaseInferred": self.phase_inferred,
            "status": self.status,
            "title": self.title,
            "summary": self.summary,
            "payloadAvailable": self.payload_available,
        }


@dataclass(slots=True)
class TimelineDocument:
    """Raw sources one timeline is built from."""

    client_events: Sequence[Mapping[str, object]] = ()
    decisions: Sequence[Mapping[str, object]] = ()
    server_spans: Sequence[Mapping[str, object]] = ()
    wire: Sequence[Mapping[str, object]] = ()
    process: Sequence[ProcessLine | Mapping[str, object]] = ()
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False
    dropped_count: int = 0


def build_entries(document: TimelineDocument) -> list[TimelineEntry]:
    """Merge every source into one timestamp-ordered list with inferred phases."""
    entries: list[TimelineEntry] = []
    for index, event in enumerate(document.client_events):
        entries.append(_client_entry(event, index, "event"))
    for index, decision in enumerate(document.decisions):
        entries.append(_client_entry(decision, index, "decision"))
    entries.extend(_server_entries(document.server_spans))
    entries.extend(_wire_entries(document.wire))
    entries.extend(_process_entries(document.process))
    entries.sort(key=_sort_key)
    return assign_inferred_phases(entries)


def assign_inferred_phases(entries: Sequence[TimelineEntry]) -> list[TimelineEntry]:
    """Give phase-less entries the phase of the nearest anchored entry."""
    anchors = sorted(
        (entry for entry in entries if entry.phase and entry.phase != "unknown"),
        key=_sort_key,
    )
    resolved: list[TimelineEntry] = []
    for entry in entries:
        if entry.phase and entry.phase != "unknown":
            resolved.append(entry)
            continue
        phase = _nearest_phase(entry.ts, anchors)
        resolved.append(replace(entry, phase=phase or "unknown", phase_inferred=True))
    return resolved


def filter_entries(
    entries: Sequence[TimelineEntry],
    *,
    sources: Iterable[str] | None = None,
    phase: str | None = None,
    query: str | None = None,
) -> list[TimelineEntry]:
    """Apply the console's source/phase/keyword filters in one pass."""
    source_set = {source for source in (sources or ()) if source}
    needle = (query or "").strip().lower()
    filtered: list[TimelineEntry] = []
    for entry in entries:
        if source_set and entry.source not in source_set:
            continue
        if phase and entry.phase != phase:
            continue
        if needle and needle not in entry.search_text.lower():
            continue
        filtered.append(entry)
    return filtered


def page_entries(
    entries: Sequence[TimelineEntry],
    *,
    offset: int,
    limit: int,
) -> tuple[list[TimelineEntry], int | None]:
    """Return one page and the next offset, ``None`` when the list is exhausted."""
    resolved_offset = max(0, offset)
    resolved_limit = max(1, min(limit, 500))
    page = list(entries[resolved_offset : resolved_offset + resolved_limit])
    consumed = resolved_offset + len(page)
    next_offset = consumed if consumed < len(entries) else None
    return page, next_offset


def parse_process_log(text: str, *, file: str, run_id: str) -> list[ProcessLine]:
    """Keep the lines of one process log that carry ``run_id``.

    Both the text and JSON log formats are supported. A traceback or other continuation line
    without a timestamp is attached to the preceding matched line.
    """
    lines: list[ProcessLine] = []
    previous: ProcessLine | None = None
    for line_no, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("{"):
            parsed = _parse_json_line(stripped)
            if parsed is None:
                previous = None
                continue
            line_ts, line_level, line_message, line_run_id = parsed
            if line_run_id != run_id:
                previous = None
                continue
            previous = ProcessLine(
                file=file,
                line_no=line_no,
                ts=line_ts,
                level=line_level,
                text=line_message,
            )
            lines.append(previous)
            continue
        match = _TEXT_LOG_PATTERN.match(raw)
        if match is None:
            if previous is not None:
                previous.detail = f"{previous.detail}\n{raw}".lstrip("\n")
            continue
        context = match.group("context") or ""
        matched_run = _RUN_ID_PATTERN.search(context)
        if matched_run is None or matched_run.group(1) != run_id:
            previous = None
            continue
        previous = ProcessLine(
            file=file,
            line_no=line_no,
            ts=match.group("ts"),
            level=match.group("level"),
            text=match.group("message").strip(),
        )
        lines.append(previous)
    return lines


def _client_entry(
    record: Mapping[str, object],
    index: int,
    kind: Literal["event", "decision"],
) -> TimelineEntry:
    entry_type = str(record.get("entryType") or kind)
    kind_name = "decision" if entry_type == "decision" else "event"
    seq = record.get("seq")
    identifier = f"client:{kind_name}:{seq}" if isinstance(seq, int) else f"client:{kind_name}:{index}"
    title = _text(record.get("summary")) or _text(record.get("kind")) or kind_name
    summary_parts = [
        _text(record.get("performative")),
        _text(record.get("kind")),
        _text(record.get("status")),
    ]
    summary = " · ".join(part for part in summary_parts if part)
    search_parts = [
        title,
        summary,
        _text(record.get("rationale")),
        _text(record.get("contextId")),
        _text(record.get("taskId")),
        _text(record.get("negotiationId")),
    ]
    return TimelineEntry(
        id=identifier,
        source="client",
        ts=_text(record.get("ts")) or None,
        phase=_text(record.get("phase")) or None,
        phase_inferred=False,
        status=_text(record.get("status")) or "ok",
        title=title,
        summary=summary,
        search_text=_search_text(search_parts),
        payload_available=True,
        ref={"collection": kind_name, "index": index},
    )


def _server_entries(spans: Sequence[Mapping[str, object]]) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    seen: set[str] = set()
    for index, span in enumerate(spans):
        span_id = _text(span.get("spanId"))
        if span_id:
            if span_id in seen:
                continue
            seen.add(span_id)
        identifier = f"server:{span_id}" if span_id else f"server:{index}"
        title = _text(span.get("title")) or _text(span.get("summary")) or _text(span.get("kind")) or "span"
        summary_parts = [
            _text(span.get("kind")),
            _text(span.get("stage")),
            _text(span.get("actor")),
            _text(span.get("status")),
        ]
        entries.append(
            TimelineEntry(
                id=identifier,
                source="server",
                ts=_text(span.get("startedAt")) or _text(span.get("ts")) or None,
                phase=_text(span.get("phase")) or None,
                phase_inferred=False,
                status=_text(span.get("status")) or "ok",
                title=title,
                summary=" · ".join(part for part in summary_parts if part),
                search_text=_search_text(
                    [
                        title,
                        _text(span.get("stage")),
                        _text(span.get("actor")),
                        _text(span.get("kind")),
                        _text(span.get("contextId")),
                        _text(span.get("taskId")),
                    ]
                ),
                payload_available=True,
                ref={"collection": "server", "index": index},
            )
        )
    return entries


def _wire_entries(records: Sequence[Mapping[str, object]]) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for index, record in enumerate(records):
        method = _text(record.get("method")) or _text(record.get("kind")) or "wire"
        url = _text(record.get("url")) or _text(record.get("path")) or ""
        direction = _text(record.get("dir")) or _text(record.get("direction")) or ""
        status_code = record.get("status")
        summary_parts = [direction, str(status_code) if status_code is not None else ""]
        entries.append(
            TimelineEntry(
                id=f"wire:{index}",
                source="wire",
                ts=_text(record.get("ts")) or None,
                phase=None,
                phase_inferred=False,
                status="ok",
                title=f"{method} {url}".strip(),
                summary=" · ".join(part for part in summary_parts if part),
                search_text=_search_text(
                    [method, url, direction, _payload_prefix(record)],
                ),
                payload_available=True,
                ref={"collection": "wire", "index": index},
            )
        )
    return entries


def _process_entries(
    lines: Sequence[ProcessLine | Mapping[str, object]],
) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for index, raw in enumerate(lines):
        line = raw if isinstance(raw, ProcessLine) else _coerce_process_line(raw)
        if line is None:
            continue
        title = f"{line.file} · {line.level}"
        entries.append(
            TimelineEntry(
                id=f"process:{line.file}:{line.line_no}",
                source="process",
                ts=line.ts,
                phase=None,
                phase_inferred=False,
                status="error" if line.level in {"ERROR", "CRITICAL"} else "ok",
                title=title,
                summary=line.text,
                search_text=_search_text([line.file, line.level, line.text, line.detail]),
                payload_available=True,
                ref={"collection": "process", "index": index},
            )
        )
    return entries


def _coerce_process_line(raw: Mapping[str, object]) -> ProcessLine | None:
    file = _text(raw.get("file"))
    line_no = raw.get("line_no")
    if not file or not isinstance(line_no, int):
        return None
    return ProcessLine(
        file=file,
        line_no=line_no,
        ts=_text(raw.get("ts")) or None,
        level=_text(raw.get("level")) or "INFO",
        text=_text(raw.get("text")),
        detail=_text(raw.get("detail")),
    )


def _nearest_phase(ts: str | None, anchors: Sequence[TimelineEntry]) -> str | None:
    if not ts or not anchors:
        return None
    preceding = [anchor for anchor in anchors if (anchor.ts or "") <= ts]
    if preceding:
        return preceding[-1].phase
    following = [anchor for anchor in anchors if (anchor.ts or "") > ts]
    return following[0].phase if following else None


def _sort_key(entry: TimelineEntry) -> tuple[int, str, int, str]:
    return (
        1 if entry.ts is None else 0,
        entry.ts or "",
        _SOURCE_ORDER.get(entry.source, 99),
        entry.id,
    )


def _text(value: object) -> str:
    return value if isinstance(value, str) else "" if value is None else str(value)


def _search_text(parts: Iterable[str]) -> str:
    return " ".join(part for part in parts if part)[:_SEARCH_LIMIT]


def _payload_prefix(record: Mapping[str, object]) -> str:
    try:
        return json.dumps(record, ensure_ascii=False)[:_PAYLOAD_PREFIX_LIMIT]
    except (TypeError, ValueError):
        return str(record)[:_PAYLOAD_PREFIX_LIMIT]


def _parse_json_line(line: str) -> tuple[str | None, str, str, str] | None:
    try:
        payload = json.loads(line)
    except ValueError:
        return None
    if not isinstance(payload, Mapping):
        return None
    return (
        _text(payload.get("ts")) or None,
        _text(payload.get("level")) or "INFO",
        _text(payload.get("message")),
        _text(payload.get("runId")),
    )


__all__ = [
    "ProcessLine",
    "TimelineDocument",
    "TimelineEntry",
    "assign_inferred_phases",
    "build_entries",
    "filter_entries",
    "page_entries",
    "parse_process_log",
]
