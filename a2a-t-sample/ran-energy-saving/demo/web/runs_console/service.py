"""Read one run's summary, timeline, negotiation and raw files for the run console.

The service is the only place that combines the client store, the transcript file, the wire logs,
the server trace and the process logs. The browser never receives a whole run dump: the timeline
is filtered and paginated here, and payloads are fetched one entry at a time.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Protocol

from protocol.client_store import read_client_run_detail, read_run_metrics

from demo.web.runs_console.indexer import (
    MAX_SERVER_SPANS,
    PROCESS_LOG_NAMES,
    TimelineCache,
    cap_wire_records,
    merge_server_spans,
    read_process_logs,
    read_server_trace_jsonl,
    split_transcript,
)
from demo.web.runs_console.timeline import (
    ProcessLine,
    TimelineDocument,
    TimelineEntry,
    build_entries,
    filter_entries,
    page_entries,
)
from demo.web.wire_records import read_wire_records

_LOGGER = logging.getLogger("ran_energy_saving.run_console")

#: Maximum characters of one entry payload returned to the browser.
MAX_PAYLOAD_BYTES: Final[int] = 8 * 1024

#: Maximum bytes of one raw file returned to the browser.
MAX_FILE_BYTES: Final[int] = 5 * 1024 * 1024

#: Logical raw files shown by the console.
RAW_FILE_NAMES: Final[tuple[str, ...]] = (
    "transcript.json",
    "transcript.md",
    "metrics.json",
    "wire.jsonl",
    "wire-oss.jsonl",
    "server-trace.jsonl",
)


class LiveRuns(Protocol):
    """The one ``RunManager`` method the console needs."""

    def get(self, run_id: str) -> Any | None: ...


@dataclass(slots=True)
class RunConsoleService:
    """Resolve one run's console views from the store, files and the live manager."""

    client_db_path: Path | None
    runs_dir: Path
    run_dir: Path | None
    runs: LiveRuns | None
    cache: TimelineCache = field(default_factory=TimelineCache)
    _live_documents: dict[str, TimelineDocument] = field(default_factory=dict)

    def summary(self, run_id: str) -> dict[str, object] | None:
        """Return the conclusion/identity/metrics block of one run."""
        detail = self._detail(run_id)
        if detail is None:
            return None
        entries, document = self._index(run_id, detail)
        metrics = self._metrics(run_id)
        live = self._live(run_id)
        run = {
            "runId": detail.get("runId"),
            "conversationId": detail.get("conversationId"),
            "traceId": detail.get("traceId", ""),
            "chainId": detail.get("chainId", ""),
            "parentRunId": detail.get("parentRunId", ""),
            "protocol": detail.get("protocol"),
            "scenario": detail.get("scenario"),
            "sourceKind": detail.get("sourceKind"),
            "status": _live_status(live) or detail.get("status"),
            "finalState": _live_final_state(live) if live is not None else detail.get("finalState"),
            "error": _live_error(live) if live is not None else detail.get("error"),
            "createdAt": detail.get("createdAt"),
            "finishedAt": detail.get("finishedAt"),
            "serverTaskId": detail.get("serverTaskId"),
            "serverContextId": detail.get("serverContextId"),
            "downstreamTaskId": detail.get("downstreamTaskId"),
            "downstreamContextId": detail.get("downstreamContextId"),
            "downstreamHop": detail.get("downstreamHop"),
            "durationMs": _duration_ms(metrics),
        }
        by_source = Counter(entry.source for entry in entries)
        by_phase = Counter(entry.phase or "unknown" for entry in entries)
        return {
            "run": run,
            "metrics": metrics,
            "counts": {
                "bySource": dict(by_source),
                "byPhase": dict(by_phase),
            },
            "truncated": document.truncated,
            "warnings": list(document.warnings),
        }

    def timeline(
        self,
        run_id: str,
        *,
        sources: Sequence[str] = (),
        phase: str | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, object] | None:
        """Return one filtered, paginated page of the merged log stream."""
        detail = self._detail(run_id)
        if detail is None:
            return None
        entries, document = self._index(run_id, detail)
        filtered = filter_entries(entries, sources=sources, phase=phase, query=query)
        page, next_offset = page_entries(filtered, offset=offset, limit=limit)
        return {
            "entries": [entry.to_public() for entry in page],
            "total": len(filtered),
            "offset": max(0, offset),
            "limit": max(1, min(limit, 500)),
            "nextOffset": next_offset,
            "truncated": document.truncated,
            "droppedCount": document.dropped_count,
            "warnings": list(document.warnings),
            "builtAt": _now(),
        }

    def entry_payload(self, run_id: str, entry_id: str) -> dict[str, object] | None:
        """Return one entry together with its payload, fetched on demand."""
        detail = self._detail(run_id)
        if detail is None:
            return None
        entries, document = self._index(run_id, detail)
        entry = next((candidate for candidate in entries if candidate.id == entry_id), None)
        if entry is None:
            return None
        payload = _payload_for(entry, document)
        if payload is None:
            return {
                "entry": entry.to_public(),
                "payload": "",
                "payloadTruncated": False,
                "payloadBytes": 0,
            }
        text = _render_payload(payload)
        encoded = text.encode("utf-8")
        truncated = len(encoded) > MAX_PAYLOAD_BYTES
        if truncated:
            text = encoded[:MAX_PAYLOAD_BYTES].decode("utf-8", errors="replace")
        return {
            "entry": entry.to_public(),
            "payload": text,
            "payloadTruncated": truncated,
            "payloadBytes": len(encoded),
        }

    def negotiation(self, run_id: str) -> dict[str, object] | None:
        """Return the structured negotiation table source of one run."""
        detail = self._detail(run_id)
        if detail is None:
            return None
        transcript = self._transcript(detail, self._live(run_id))
        raw_decisions = transcript.get("decisions")
        decisions = (
            [decision for decision in raw_decisions if isinstance(decision, Mapping)]
            if isinstance(raw_decisions, list)
            else []
        )
        return {
            "turns": detail.get("turns") or [],
            "negotiationEvents": detail.get("negotiationEvents") or [],
            "decisions": decisions,
        }

    def files(self, run_id: str) -> dict[str, object] | None:
        """Return the logical raw-file list of one run."""
        detail = self._detail(run_id)
        if detail is None:
            return None
        metrics = self._metrics(run_id)
        files: list[dict[str, object]] = []
        for name in RAW_FILE_NAMES:
            path = self._raw_path(detail, name)
            if name == "metrics.json" and metrics is not None:
                content = _metrics_text(metrics)
                files.append(
                    _file_info(
                        name,
                        size=len(content.encode("utf-8")),
                        modified_at=str(detail.get("updatedAt") or ""),
                        available=True,
                    )
                )
                continue
            if path is None:
                files.append(
                    _file_info(name, size=0, modified_at="", available=False, reason="文件不存在")
                )
                continue
            try:
                stat = path.stat()
            except OSError:
                files.append(
                    _file_info(name, size=0, modified_at="", available=False, reason="文件不可读")
                )
                continue
            files.append(
                _file_info(
                    name,
                    size=stat.st_size,
                    modified_at=_iso_time(stat.st_mtime),
                    available=True,
                )
            )
        if self.run_dir is not None:
            for name in PROCESS_LOG_NAMES:
                path = self.run_dir / f"{name}.log"
                try:
                    stat = path.stat()
                except OSError:
                    continue
                files.append(
                    _file_info(
                        f"{name}.log",
                        size=stat.st_size,
                        modified_at=_iso_time(stat.st_mtime),
                        available=True,
                    )
                )
        return {"files": files}

    def read_file(self, run_id: str, name: str) -> tuple[str, bool] | None:
        """Return one whitelisted raw file, capped, ``None`` when unknown or unavailable."""
        if name not in RAW_FILE_NAMES and name not in {
            f"{process}.log" for process in PROCESS_LOG_NAMES
        }:
            return None
        detail = self._detail(run_id)
        if detail is None:
            return None
        metrics = self._metrics(run_id)
        if name == "metrics.json":
            if metrics is None:
                return None
            return _metrics_text(metrics), False
        path = self._raw_path(detail, name)
        if path is None:
            return None
        return _read_capped(path, MAX_FILE_BYTES)

    def _detail(self, run_id: str) -> Mapping[str, object] | None:
        if self.client_db_path is None:
            return None
        detail = read_client_run_detail(self.client_db_path, run_id)
        return detail if isinstance(detail, Mapping) else None

    def _metrics(self, run_id: str) -> Mapping[str, object] | None:
        if self.client_db_path is None:
            return None
        metrics = read_run_metrics(self.client_db_path, run_id)
        return metrics if isinstance(metrics, Mapping) else None

    def _live(self, run_id: str) -> Any | None:
        if self.runs is None:
            return None
        return self.runs.get(run_id)

    def _index(
        self,
        run_id: str,
        detail: Mapping[str, object],
    ) -> tuple[list[TimelineEntry], TimelineDocument]:
        live = self._live(run_id)
        if live is not None:
            document = self._build_document(run_id, detail, live)
            self._live_documents[run_id] = document
            return build_entries(document), document
        fingerprint = self._fingerprint(run_id, detail)
        entries = self.cache.get(
            run_id,
            fingerprint,
            lambda: self._build_document(run_id, detail, None),
        )
        document = self.cache.document(run_id)
        if document is None:
            document = TimelineDocument()
        return entries, document

    def _build_document(
        self,
        run_id: str,
        detail: Mapping[str, object],
        live: Any | None,
    ) -> TimelineDocument:
        transcript = self._transcript(detail, live)
        client_events, decisions, transcript_server = split_transcript(transcript)
        live_spans = list(live.spans.values()) if live is not None else []
        durable_spans, span_warnings = read_server_trace_jsonl(
            self.runs_dir / run_id / "server-trace.jsonl",
            limit=MAX_SERVER_SPANS,
        )
        server_spans = merge_server_spans(
            [*live_spans, *transcript_server],
            durable_spans,
        )
        wire_records = read_wire_records(self.runs_dir, run_id)
        wire_records, wire_warning = cap_wire_records(wire_records)
        process_lines, process_warnings = read_process_logs(self.run_dir, run_id)
        warnings = [*span_warnings, *process_warnings]
        if wire_warning is not None:
            warnings.append(wire_warning)
        return TimelineDocument(
            client_events=tuple(client_events),
            decisions=tuple(decisions),
            server_spans=tuple(server_spans),
            wire=tuple(wire_records),
            process=tuple(process_lines),
            warnings=warnings,
            truncated=bool(warnings),
        )

    def _transcript(
        self,
        detail: Mapping[str, object],
        live: Any | None,
    ) -> Mapping[str, object]:
        if live is not None:
            transcript = getattr(live, "transcript", None)
            if transcript is not None:
                document = transcript.to_dict()
                return document if isinstance(document, Mapping) else {}
        path = self._resolve_inside(detail.get("transcriptJson"), self.runs_dir)
        if path is None:
            return {}
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return document if isinstance(document, Mapping) else {}

    def _fingerprint(self, run_id: str, detail: Mapping[str, object]) -> tuple[object, ...]:
        paths: list[Path | None] = [
            self._resolve_inside(detail.get("transcriptJson"), self.runs_dir),
            self._resolve_inside(detail.get("transcriptMarkdown"), self.runs_dir),
            self.runs_dir / run_id / "wire.jsonl",
            self.runs_dir / run_id / "wire-oss.jsonl",
            self.runs_dir / run_id / "server-trace.jsonl",
        ]
        if self.run_dir is not None:
            paths.extend(self.run_dir / f"{name}.log" for name in PROCESS_LOG_NAMES)
        if self.client_db_path is not None:
            paths.extend(
                [
                    self.client_db_path,
                    Path(f"{self.client_db_path}-wal"),
                    Path(f"{self.client_db_path}-shm"),
                ]
            )
        parts: list[object] = []
        for path in paths:
            if path is None:
                parts.append(("", -1, -1))
                continue
            try:
                stat = path.stat()
            except OSError:
                parts.append((str(path), -1, -1))
                continue
            parts.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(parts)

    def _raw_path(
        self,
        detail: Mapping[str, object],
        name: str,
    ) -> Path | None:
        if name == "metrics.json":
            return None
        if name == "transcript.json":
            return self._resolve_inside(detail.get("transcriptJson"), self.runs_dir)
        if name == "transcript.md":
            return self._resolve_inside(detail.get("transcriptMarkdown"), self.runs_dir)
        if name in {"wire.jsonl", "wire-oss.jsonl", "server-trace.jsonl"}:
            run_id = str(detail.get("runId") or "")
            path = self.runs_dir / run_id / name
            return path if path.is_file() else None
        if name.endswith(".log") and self.run_dir is not None:
            path = self.run_dir / name
            return path if path.is_file() else None
        return None

    @staticmethod
    def _resolve_inside(value: object, root: Path) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            resolved = Path(value).resolve()
            root_resolved = root.resolve()
        except OSError:
            return None
        if resolved != root_resolved and root_resolved not in resolved.parents:
            return None
        return resolved if resolved.is_file() else None


def _payload_for(entry: TimelineEntry, document: TimelineDocument) -> object | None:
    collection = entry.ref.get("collection")
    index = entry.ref.get("index")
    if not isinstance(index, int):
        return None
    if collection == "event":
        return document.client_events[index] if index < len(document.client_events) else None
    if collection == "decision":
        return document.decisions[index] if index < len(document.decisions) else None
    if collection == "server":
        return document.server_spans[index] if index < len(document.server_spans) else None
    if collection == "wire":
        return document.wire[index] if index < len(document.wire) else None
    if collection == "process":
        return document.process[index] if index < len(document.process) else None
    return None


def _render_payload(value: object) -> str:
    if isinstance(value, ProcessLine):
        header = f"{value.ts or ''} {value.level} {value.file}:{value.line_no}".strip()
        body = value.text if not value.detail else f"{value.text}\n{value.detail}"
        return f"{header}\n{body}".strip()
    try:
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _live_status(live: Any | None) -> str | None:
    if live is None:
        return None
    return str(getattr(live, "status", "")) or None


def _live_final_state(live: Any) -> str | None:
    result = getattr(live, "result", None)
    if result is None or getattr(live, "status", "") == "running":
        return None
    value = getattr(result, "final_state", None)
    return str(value) if value is not None else None


def _live_error(live: Any) -> str | None:
    value = getattr(live, "error", None)
    return str(value) if value is not None else None


def _duration_ms(metrics: Mapping[str, object] | None) -> object | None:
    if not isinstance(metrics, Mapping):
        return None
    latency = metrics.get("latency")
    if not isinstance(latency, Mapping):
        return None
    return latency.get("totalMs")


def _metrics_text(metrics: Mapping[str, object]) -> str:
    return json.dumps(metrics, indent=2, ensure_ascii=False, default=str)


def _file_info(
    name: str,
    *,
    size: int,
    modified_at: str,
    available: bool,
    reason: str | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "size": size,
        "modifiedAt": modified_at,
        "available": available,
        "reason": reason,
    }


def _read_capped(path: Path, limit: int) -> tuple[str, bool] | None:
    try:
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        return None
    truncated = len(data) > limit
    text = data[:limit].decode("utf-8", errors="replace")
    return text, truncated


def _iso_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


__all__ = ["MAX_FILE_BYTES", "MAX_PAYLOAD_BYTES", "RAW_FILE_NAMES", "RunConsoleService"]
