"""Offline Langfuse trace evidence for the acceptance review console.

The review page never calls the Langfuse API. Snapshots live under
``<runs>/review/langfuse/<traceId>_<stamp>/`` and are produced either by the opt-in fetch here or
by the vendored ``langfuse_export`` CLI. The directory layout is the one ``export_full`` writes
(``manifest.json``, ``trace-summary.json``, ``metrics.json``, ``raw/observations.jsonl``), so a
customer acceptance pack can be rebuilt from the same files without the observe page.

``load`` is read-only and deterministic. ``fetch`` is the only method that touches the network and
is never called while rendering a page.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from langfuse_export.cli import export_full
from langfuse_export.config import Config
from langfuse_export.metrics import _row_side

TRACE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{32}$")
#: Payload contract of the normalized trace block. Bumped whenever the normalizer changes which
#: fields a stored payload carries: the review re-derives a payload of an older schema from the
#: snapshot on disk instead of judging with the fields the old code happened to record.
#: v3 reads the push channel: ``SendMessage-pushNotification`` carries the same A2A text inside
#: ``response.statusUpdate.status.message.parts``. A payload stored by v2 has no ``response`` on
#: those rows, so it must be re-derived instead of judged. v4 keeps each observation's start time:
#: Langfuse returns observations newest first, and the negotiation-round fallback must order the
#: push frames before numbering them.
SNAPSHOT_SCHEMA: Final[str] = "review-trace/v4"

EXPORT_STATE_EXPORTED: Final[str] = "exported"
EXPORT_STATE_PARTIAL: Final[str] = "partial"
EXPORT_STATE_FAILED: Final[str] = "failed"
EXPORT_STATE_NOT_EXPORTED: Final[str] = "not_exported"

#: States whose export is complete enough to be the permanent cache. A failure marker is not a
#: usable snapshot; `ReviewService` retries it only when the reviewer presses "重试抓取".
_CACHED_STATES: Final[frozenset[str]] = frozenset({EXPORT_STATE_EXPORTED, EXPORT_STATE_PARTIAL})

NMS_SCOPE_MARKER: Final[str] = "a2a-t-server"

#: One snapshot larger than this is truncated to a ``partial`` state instead of stalling a scan.
MAX_OBSERVATIONS: Final[int] = 5000
MAX_OBSERVATION_BYTES: Final[int] = 32 * 1024 * 1024
#: Task-T / request text kept in the snapshot payload. The producer already caps some fields at
#: 4000 characters; keeping 16 KiB covers the full rendered Task-T and stays well below a
#: customer-pack problem.
MAX_PROMPT_TEXT: Final[int] = 16384

_SEND_SPAN_NAMES: Final[frozenset[str]] = frozenset({"SendMessage", "SendStreamingMessage"})
_OBSERVATION_FIELDS: Final[tuple[str, ...]] = (
    "name",
    "type",
    "kind",
    "stage",
    "side",
    "sideSource",
    "round",
    "performative",
    "status",
    "verdict",
    "interception",
    "runId",
    "scope",
    "spanId",
    "parentId",
)


@dataclass(frozen=True)
class Observation:
    """One Langfuse observation projected onto the fields the review console uses."""

    name: str
    type: str
    kind: str
    stage: str
    side: str
    side_source: str
    round: int | None
    performative: str
    status: str
    #: Observed validation verdict of a validation span (``blocked`` / ``pass`` / ``error``).
    verdict: str
    #: Verdict the case expected of the interception probe (``blocked`` when it is a probe).
    expected_verdict: str
    interception: bool
    run_id: str
    scope: str
    span_id: str
    parent_id: str
    user_request: str = ""
    prompt_text: str = ""
    prompt_truncated: bool = False
    params: Mapping[str, object] | None = None
    task_t: str = ""
    #: Task-T read from the A2A request envelope of a SendMessage span. The client's rendered
    #: prompt and send frames can be lost; the request the transport sent still carries it.
    a2a_task_t: str = ""
    negotiation_t: str = ""
    content: str = ""
    response: str = ""
    operation: str = ""
    state: str = ""
    task_id: str = ""
    context_id: str = ""
    final_state: str = ""
    #: The vendor's own acceptance sentence of a status frame ("Task modification succeeded").
    status_text: str = ""
    #: ``taskModifyStatus`` of a status frame; ``SUCCESSFUL`` is the Modify acceptance.
    modify_status: str = ""
    summary: str = ""
    source: str = ""
    #: Observation start time. Langfuse lists observations newest first, so any fallback that has
    #: to number frames by occurrence must sort on this before assigning rounds.
    started_at: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return the compact export form used by the review store."""
        payload: dict[str, object] = {
            "name": self.name,
            "type": self.type,
            "kind": self.kind,
            "stage": self.stage,
            "side": self.side,
            "sideSource": self.side_source,
            "round": self.round,
            "performative": self.performative,
            "status": self.status,
            "verdict": self.verdict,
            "expectedVerdict": self.expected_verdict,
            "interception": self.interception,
            "runId": self.run_id,
            "scope": self.scope,
            "spanId": self.span_id,
            "parentId": self.parent_id,
        }
        if self.user_request:
            payload["userRequest"] = self.user_request
        if self.prompt_text:
            payload["promptText"] = self.prompt_text
            payload["promptTruncated"] = self.prompt_truncated
        if self.params is not None:
            payload["params"] = dict(self.params)
        for key, value in (
            ("taskT", self.task_t),
            ("a2aTaskT", self.a2a_task_t),
            ("negotiationT", self.negotiation_t),
            ("content", self.content),
            ("response", self.response),
            ("operation", self.operation),
            ("state", self.state),
            ("taskId", self.task_id),
            ("contextId", self.context_id),
            ("finalState", self.final_state),
            ("statusText", self.status_text),
            ("modifyStatus", self.modify_status),
            ("summary", self.summary),
            ("source", self.source),
            ("startedAt", self.started_at),
        ):
            if value:
                payload[key] = value
        return payload


@dataclass(frozen=True)
class TraceSnapshot:
    """One loaded snapshot, with the export state as a first-class value."""

    trace_id: str
    export_state: str
    captured_at: str
    export_dir: str
    manifest_sha256: str
    export_dir_sha256: str
    rounds: int | None
    span_level: str
    capture_content: bool | None
    observations: tuple[Observation, ...]
    truncated: bool
    warnings: tuple[str, ...]


class InvalidTraceIdError(ValueError):
    """Raised when a trace id is not a 32-character hexadecimal value."""


class TraceStore:
    """Read and (opt-in) fetch Langfuse snapshots for the review console."""

    def __init__(self, runs_dir: str | Path, store_dir: str | Path | None = None) -> None:
        self._runs_dir = Path(runs_dir)
        self.root = Path(store_dir) if store_dir is not None else self._runs_dir / "review" / "langfuse"

    def latest_dir(self, trace_id: str) -> Path | None:
        """Return the newest snapshot directory of one trace, or ``None``."""
        self._require_trace_id(trace_id)
        if not self.root.is_dir():
            return None
        candidates = [path for path in self.root.glob(f"{trace_id}_*") if path.is_dir() and self._is_inside(path)]
        if not candidates:
            return None
        return max(candidates, key=self._rank)

    def load(self, trace_id: str, *, hashes: bool = True) -> TraceSnapshot:
        """Load one snapshot; a missing directory is ``not_exported``, not an error.

        ``hashes`` is the provenance digest of the export directory. Re-judging a stored payload
        does not need it, and hashing every file on every read is the expensive part of a load.
        """
        directory = self.latest_dir(trace_id)
        if directory is None:
            return TraceSnapshot(
                trace_id=trace_id,
                export_state=EXPORT_STATE_NOT_EXPORTED,
                captured_at="",
                export_dir="",
                manifest_sha256="",
                export_dir_sha256="",
                rounds=None,
                span_level="",
                capture_content=None,
                observations=(),
                truncated=False,
                warnings=(),
            )
        captured_at = self._rank(directory)[1]
        manifest_path = directory / "manifest.json"
        manifest = _read_json(manifest_path) if manifest_path.is_file() else {}
        failure_path = directory / "failure.json"
        if failure_path.is_file():
            failure = _read_json(failure_path)
            return TraceSnapshot(
                trace_id=trace_id,
                export_state=EXPORT_STATE_FAILED,
                captured_at=captured_at,
                export_dir=str(directory),
                manifest_sha256=(
                    _sha256(manifest_path) if hashes and manifest_path.is_file() else ""
                ),
                export_dir_sha256=_directory_sha256(directory) if hashes else "",
                rounds=None,
                span_level="",
                capture_content=None,
                observations=(),
                truncated=False,
                warnings=(_text(failure.get("error")) or "export failed",),
            )
        observations_path = directory / "raw" / "observations.jsonl"
        rows, truncated, warnings = _read_observations(observations_path)
        observations = normalize_rows(rows)
        root_metadata = _root_metadata(rows)
        state = EXPORT_STATE_EXPORTED
        if truncated or warnings or not observations_path.is_file():
            state = EXPORT_STATE_PARTIAL
        if isinstance(manifest.get("warnings"), Sequence) and not isinstance(manifest.get("warnings"), str):
            manifest_warnings = [str(item) for item in manifest["warnings"] if item]
            if manifest_warnings:
                warnings = (*warnings, *manifest_warnings)
                state = EXPORT_STATE_PARTIAL
        return TraceSnapshot(
            trace_id=trace_id,
            export_state=state,
            captured_at=captured_at,
            export_dir=str(directory),
            manifest_sha256=_sha256(manifest_path) if hashes and manifest_path.is_file() else "",
            export_dir_sha256=_directory_sha256(directory) if hashes else "",
            rounds=_int(root_metadata.get("rounds")),
            span_level=_text(root_metadata.get("spanLevel") or root_metadata.get("span_level")),
            capture_content=_first_bool(root_metadata, "captureContent", "capture_content"),
            observations=observations,
            truncated=truncated,
            warnings=warnings,
        )

    def payload(
        self,
        trace_id: str,
        *,
        run_id: str | None,
        nms_kind: str,
        local_points: Sequence[str] | None = None,
        local_known: Sequence[str] | None = None,
        hashes: bool = True,
    ) -> dict[str, object]:
        """Return the compact trace block stored beside one review observation."""
        return self._payload(
            self.load(trace_id, hashes=hashes),
            run_id=run_id,
            nms_kind=nms_kind,
            local_points=local_points,
            local_known=local_known,
        )

    def cached_payload(
        self,
        trace_id: str,
        *,
        run_id: str | None,
        nms_kind: str,
        config: Config | None = None,
        force: bool = False,
        local_points: Sequence[str] | None = None,
        local_known: Sequence[str] | None = None,
        hashes: bool = True,
    ) -> dict[str, object]:
        """Return the cached snapshot, fetching only when no export exists yet.

        The snapshot on disk is a **permanent cache**: a Langfuse export is immutable evidence for
        one trace, so re-reading it must never spend quota. Only a missing or failed export is
        fetched again; ``force`` (the reviewer's "重新抓取") is the explicit way to ask for a new
        copy. ``scan`` passes no config and therefore only reads the snapshot root.
        """
        snapshot = self.load(trace_id, hashes=hashes)
        if config is not None and (force or snapshot.export_state not in _CACHED_STATES):
            self.fetch(trace_id, config, run_id=run_id)
            snapshot = self.load(trace_id, hashes=hashes)
        return self._payload(
            snapshot,
            run_id=run_id,
            nms_kind=nms_kind,
            local_points=local_points,
            local_known=local_known,
        )

    def _payload(
        self,
        snapshot: TraceSnapshot,
        *,
        run_id: str | None,
        nms_kind: str,
        local_points: Sequence[str] | None,
        local_known: Sequence[str] | None = None,
        warnings: Sequence[str] | None = None,
    ) -> dict[str, object]:
        frames = build_frames(
            snapshot.observations,
            run_id=run_id,
            nms_kind=nms_kind,
            rounds=snapshot.rounds,
        )
        return {
            "schema": SNAPSHOT_SCHEMA,
            "traceId": snapshot.trace_id,
            "exportState": snapshot.export_state,
            "capturedAt": snapshot.captured_at,
            "exportDir": snapshot.export_dir,
            "manifestSha256": snapshot.manifest_sha256,
            "exportDirSha256": snapshot.export_dir_sha256,
            "spanLevel": snapshot.span_level,
            "captureContent": snapshot.capture_content,
            "nmsKind": nms_kind,
            "rounds": snapshot.rounds,
            "frames": frames,
            "observations": [observation.to_dict() for observation in snapshot.observations],
            "observationsTruncated": snapshot.truncated,
            "related": [],
            "localPoints": list(local_points) if local_points is not None else None,
            "localKnown": list(local_known) if local_known is not None else None,
            "warnings": list(warnings if warnings is not None else snapshot.warnings),
        }

    def fetch(
        self,
        trace_id: str,
        config: Config,
        *,
        run_id: str | None = None,
    ) -> dict[str, object]:
        """Fetch one trace into the review snapshot root (explicit opt-in).

        The caller owns the Langfuse credentials. A failed fetch leaves a ``failure.json`` marker
        so the page shows ``failed`` instead of silently returning ``not_exported``.
        """
        self._require_trace_id(trace_id)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            result = export_full(
                config,
                trace_id,
                self.root,
                zip_file=False,
                run_id=run_id,
                # Self-hosted Langfuse truncates metadata values unless they are expanded. The
                # acceptance artifact keeps the Task-T and negotiation text in ``detail``;
                # without this the export can contain the span but not the data.
                expand_metadata=["detail"],
            )
        except Exception as exc:  # noqa: BLE001 - the review must survive a vendor outage
            failed = self.root / f"{trace_id}_failed-{_stamp()}"
            failed.mkdir(parents=True, exist_ok=True)
            _write_json(
                failed / "failure.json",
                {"error": f"{type(exc).__name__}: {exc}", "traceId": trace_id},
            )
            return {"traceId": trace_id, "exportState": EXPORT_STATE_FAILED, "error": str(exc)}
        return {
            "traceId": trace_id,
            "exportState": EXPORT_STATE_EXPORTED,
            "exportDir": str(result.get("exportDir") or ""),
            "observations": result.get("observations"),
        }

    def build_archive(self, trace_ids: Sequence[str], destination: Path) -> Path:
        """Write one ``<traceId>/raw/observations.jsonl`` entry per cached snapshot.

        The pack carries the raw Langfuse observations only. A trace without a snapshot is listed
        in ``_missing.txt`` instead of being fetched: a batch export must not spend Langfuse quota
        or stall the download, and the page already has an explicit fetch action.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        missing: list[str] = []
        seen: set[str] = set()
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for trace_id in trace_ids:
                if not trace_id or trace_id in seen:
                    continue
                seen.add(trace_id)
                try:
                    directory = self.latest_dir(trace_id)
                except InvalidTraceIdError:
                    missing.append(f"{trace_id}\tinvalid trace id")
                    continue
                if directory is None:
                    missing.append(f"{trace_id}\tno snapshot")
                    continue
                source = directory / "raw" / "observations.jsonl"
                if not source.is_file():
                    missing.append(f"{trace_id}\traw/observations.jsonl is missing")
                    continue
                archive.write(source, f"{trace_id}/raw/observations.jsonl")
            if missing:
                archive.writestr("_missing.txt", "\n".join(missing) + "\n")
        return destination

    def _require_trace_id(self, trace_id: str) -> None:
        if not TRACE_ID_PATTERN.match(trace_id):
            raise InvalidTraceIdError(f"invalid trace id: {trace_id!r}")

    def _is_inside(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.root.resolve())
        except ValueError:
            return False
        return True

    def _rank(self, path: Path) -> tuple[int, str]:
        """Prefer a readable export over a later failure marker."""
        manifest = path / "manifest.json"
        exported_at = path.name
        if manifest.is_file():
            value = _text(_read_json(manifest).get("exportedAt"))
            if value:
                exported_at = value
            return 1, exported_at
        return 0, exported_at


def normalize_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[Observation, ...]:
    """Project raw Langfuse observation rows onto the review fields.

    Langfuse v4 flattens the exporter's ``detail`` object into dotted metadata keys
    (``detail.promptText``), while v3 and the unit fixtures keep it nested. Both shapes are merged
    here; reading only the nested form silently emptied Task-T, userRequest and verdict on every
    real export.
    """
    observations: list[Observation] = []
    for row in rows:
        raw_metadata = row.get("metadata")
        metadata: Mapping[str, Any] = raw_metadata if isinstance(raw_metadata, Mapping) else {}
        detail = _merged_detail(metadata)
        raw_type = _text(row.get("type")).upper()
        kind = _text(metadata.get("kind"))
        if not kind:
            kind = "llm" if raw_type == "GENERATION" else "tool" if raw_type == "TOOL" else ""
        side, side_source = _side(row, metadata)
        event = _event_map(detail)
        prompt_text, prompt_truncated = _clip(_text(detail.get("promptText")), MAX_PROMPT_TEXT)
        # Pure A2A negotiates in plain message text: the server's reply lives in the SendMessage
        # attributes, not in a propose/decision span.
        response_text, _ = _clip(
            _output_text(row.get("output")) or _a2a_response_text(metadata), MAX_PROMPT_TEXT
        )
        task_t, _ = _clip(_text(detail.get("taskT")), MAX_PROMPT_TEXT)
        a2a_task_t, _ = _clip(_a2a_request_task_t(metadata), MAX_PROMPT_TEXT)
        negotiation_t, _ = _clip(_text(detail.get("negotiationT")), MAX_PROMPT_TEXT)
        user_request = _text(detail.get("userRequest")) or _text(metadata.get("userRequest"))
        observations.append(
            Observation(
                name=_text(row.get("name")),
                type=raw_type,
                kind=kind,
                stage=_text(metadata.get("stage")),
                side=side,
                side_source=side_source,
                round=_round(metadata),
                performative=_text(metadata.get("attributes.gen_ai.agent.a2at.negotiation.performative"))
                or _text(detail.get("performative")),
                status=_text(metadata.get("status")),
                # The probe records what it observed; older spans only carry ``verdict``.
                verdict=_text(detail.get("observedVerdict")) or _text(detail.get("verdict")),
                expected_verdict=_text(detail.get("expectedVerdict")),
                interception=_text(detail.get("source")) == "interception",
                run_id=_text(metadata.get("runId")),
                scope=_scope(metadata),
                span_id=_text(row.get("id")),
                parent_id=_text(row.get("parentObservationId")),
                user_request=user_request,
                prompt_text=prompt_text,
                prompt_truncated=prompt_truncated,
                params=_merged_params(detail),
                task_t=task_t,
                a2a_task_t=a2a_task_t,
                negotiation_t=negotiation_t,
                content=_text(detail.get("content")),
                response=response_text,
                operation=_text(detail.get("operation")),
                # A vendor status frame nests its state and Task identity under ``event``; the
                # Modify acceptance is exactly such a frame (WORKING + modification succeeded).
                state=_text(detail.get("state"))
                or _text(metadata.get("state"))
                or _event_state(event)
                or _a2a_response_state(metadata),
                task_id=_text(metadata.get("taskId"))
                or _text(metadata.get("task_id"))
                or _event_identity(event, "taskId"),
                context_id=_text(metadata.get("contextId"))
                or _text(metadata.get("context_id"))
                or _event_identity(event, "contextId"),
                final_state=_text(detail.get("finalState")) or _text(metadata.get("finalState")),
                status_text=_event_text(event),
                modify_status=_event_modify_status(event),
                summary=_text(detail.get("summary")),
                source=_text(detail.get("source")),
                started_at=_text(row.get("startTime")) or _text(row.get("startedAt")),
            )
        )
    return tuple(observations)


def _merged_detail(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return the span detail from either the nested or the flattened Langfuse shape."""
    detail: dict[str, Any] = {}
    nested = metadata.get("detail")
    if isinstance(nested, Mapping):
        detail.update({str(key): value for key, value in nested.items()})
    for key, value in metadata.items():
        if not isinstance(key, str) or not key.startswith("detail."):
            continue
        parts = key[len("detail.") :].split(".")
        bucket = detail
        for part in parts[:-1]:
            child = bucket.get(part)
            if not isinstance(child, dict):
                child = {}
                bucket[part] = child
            bucket = child
        bucket.setdefault(parts[-1], value)
    return detail


def _mapping(value: object) -> Mapping[str, Any]:
    """Return a mapping value, or an empty mapping."""
    return value if isinstance(value, Mapping) else {}


def _event_map(detail: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the nested ``event`` of a status frame, or an empty mapping."""
    event = detail.get("event")
    return event if isinstance(event, Mapping) else {}


def _event_state(event: Mapping[str, Any]) -> str:
    """Return the Task state a status frame carries, wherever the vendor nests it."""
    state = _text(event.get("state"))
    if state:
        return state
    task = event.get("task")
    if not isinstance(task, Mapping):
        return ""
    state = _text(task.get("state"))
    if state:
        return state
    status = task.get("status")
    return _text(status.get("state")) if isinstance(status, Mapping) else ""


def _event_text(event: Mapping[str, Any]) -> str:
    """Return the human sentence of a status frame."""
    text = _text(event.get("text"))
    if text:
        return text
    parts = _mapping(_mapping(_mapping(event.get("task")).get("status")).get("message")).get("parts")
    if isinstance(parts, list):
        for part in parts:
            text = _text(_mapping(part).get("text"))
            if text:
                return text
    return ""


def _event_modify_status(event: Mapping[str, Any]) -> str:
    """Return the vendor's ``taskModifyStatus`` of a status frame."""
    value = _text(_mapping(event.get("metadata")).get("taskModifyStatus"))
    if value:
        return value
    message = _mapping(_mapping(_mapping(event.get("task")).get("status")).get("message"))
    return _text(_mapping(message.get("metadata")).get("taskModifyStatus"))


def _event_identity(event: Mapping[str, Any], key: str) -> str:
    """Return the Task/context id a status frame carries under ``event``."""
    snake = "task_id" if key == "taskId" else "context_id"
    task = event.get("task")
    if isinstance(task, Mapping):
        value = _text(task.get(key)) or _text(task.get(snake))
        if value:
            return value
    return _text(event.get(key)) or _text(event.get(snake))


def _merged_params(detail: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the structured params of a ``task_created`` span, if any."""
    raw = detail.get("params")
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    return None


def _a2a_request_task_t(metadata: Mapping[str, Any]) -> str:
    """Return the Task-T carried by a pure-A2A request envelope.

    The client's rendered-prompt and send spans can be lost in a real run. The request the
    transport actually sent still names the Task-T extension in
    ``message.metadata[".../extensions/Task-T/v1"]``; read it from the SendMessage observation.
    """
    request = metadata.get("attributes.gen_ai.agent.a2a.request")
    if isinstance(request, Mapping):
        text = _task_t_from_message(request.get("message"))
        if text:
            return text
    # v4 can flatten the whole request object into dotted keys.
    for key, value in metadata.items():
        if isinstance(key, str) and "Task-T/v1" in key:
            text = _json_text(value)
            if text:
                return text
    return ""


def _task_t_from_message(message: object) -> str:
    """Return the Task-T extension of one A2A ``message`` object."""
    meta = _mapping(_mapping(message).get("metadata"))
    for key, value in meta.items():
        if isinstance(key, str) and "Task-T/v1" in key:
            text = _json_text(value)
            if text:
                return text
    return ""


def _json_text(value: object) -> str:
    """Return a string value, decoding the JSON string Task-T metadata carries."""
    if isinstance(value, Mapping):
        for key in ("taskT", "prompt", "content", "text", "value"):
            text = _text(value.get(key))
            if text:
                return text
        for item in value.values():
            text = _text(item)
            if text:
                return text
        return ""
    text = _text(value)
    if not text or text[0] not in {'"', "{", "["}:
        return text
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text
    if isinstance(parsed, str):
        return parsed.strip()
    if isinstance(parsed, Mapping):
        for key in ("taskT", "prompt", "content", "text", "value"):
            decoded = _text(parsed.get(key))
            if decoded:
                return decoded
    return text


def _a2a_response_text(metadata: Mapping[str, Any]) -> str:
    """Return the text of a pure-A2A ``SendMessage`` response.

    The A2A transport records the server reply as observation attributes
    (``attributes.gen_ai.agent.a2a.response.parts[].text``) in the nested shape, or as flattened
    dotted keys on v4. Pure A2A negotiates in this text; there is no Negotiation-T span.

    A server can answer on either channel: the synchronous reply carries ``parts`` directly, while
    a push (``SendMessage-pushNotification``) wraps the same text in a ``statusUpdate`` (or a
    ``task`` snapshot). Both shapes must yield the text, otherwise an exported negotiation is
    reported as missing solely because it arrived by push.
    """
    response = metadata.get("attributes.gen_ai.agent.a2a.response")
    if isinstance(response, Mapping):
        text = _parts_text(response.get("parts")) or _response_status_text(response)
        if text:
            return text
    prefix = "attributes.gen_ai.agent.a2a.response."
    flattened = sorted(
        (
            (key, value)
            for key, value in metadata.items()
            if isinstance(key, str) and key.startswith(prefix) and key.endswith(".text")
        ),
        key=lambda item: item[0],
    )
    for _key, value in flattened:
        text = _text(value)
        if text:
            return text
    return ""


def _response_status_text(response: Mapping[str, Any]) -> str:
    """Return the status message of a push or Task-snapshot response envelope."""
    for key in ("statusUpdate", "artifactUpdate", "task"):
        event = response.get(key)
        if isinstance(event, Mapping):
            text = _status_message_text(event.get("status"))
            if text:
                return text
    return _status_message_text(response.get("status"))


def _status_message_text(status: object) -> str:
    """Return the text parts of one ``TaskStatus.message``."""
    if not isinstance(status, Mapping):
        return ""
    return _parts_text(_mapping(status.get("message")).get("parts"))


def _a2a_response_state(metadata: Mapping[str, Any]) -> str:
    """Return the Task state of a push response, from its attribute or its envelope."""
    state = _text(metadata.get("attributes.gen_ai.agent.a2a.status"))
    if state:
        return state
    response = metadata.get("attributes.gen_ai.agent.a2a.response")
    if isinstance(response, Mapping):
        for key in ("statusUpdate", "task"):
            event = response.get(key)
            if not isinstance(event, Mapping):
                continue
            status = event.get("status")
            if isinstance(status, Mapping):
                state = _text(status.get("state"))
                if state:
                    return state
    return ""


def _parts_text(parts: object) -> str:
    """Return the first non-empty ``text`` of A2A message parts."""
    values = parts.values() if isinstance(parts, Mapping) else parts if isinstance(parts, list) else ()
    for part in values:
        text = _text(_mapping(part).get("text"))
        if text:
            return text
    return ""


def _output_text(value: object) -> str:
    """Return the model output of a GENERATION as text."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (Mapping, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return ""
    return ""


def build_frames(
    observations: Sequence[Observation],
    *,
    run_id: str | None,
    nms_kind: str,
    rounds: int | None,
) -> dict[str, object]:
    """Return the client/NMS frame evidence of one snapshot.

    The NMS hop is the only hard count. ``frames.client`` is corroboration, and every other
    ``runId`` is a display-only hop (a pure A2A trace also contains the UI→OSS hop).
    """
    messages = [observation for observation in observations if observation.kind == "message_sent"]
    client_observed = sum(1 for observation in messages if run_id and observation.run_id == run_id)
    other: dict[str, int] = defaultdict(int)
    for observation in messages:
        if run_id and observation.run_id == run_id:
            continue
        other[observation.run_id or "unknown"] += 1

    server = [
        observation
        for observation in observations
        if observation.name in _SEND_SPAN_NAMES
        # The scope is authoritative. A remote SendMessage can arrive without a ``side``
        # attribute; dropping it just because the metadata is incomplete is what made a real
        # ``a2a-t-server`` frame disappear from the gate.
        and (observation.side == "server" or NMS_SCOPE_MARKER in observation.scope.lower())
    ]
    by_scope: dict[str, dict[str, object]] = {}
    for observation in server:
        scope = observation.scope or "unknown"
        group = by_scope.setdefault(
            scope,
            {"scope": scope, "count": 0, "sideSource": observation.side_source},
        )
        group["count"] = (_int(group["count"]) or 0) + 1
    candidates = [group for group in by_scope.values() if NMS_SCOPE_MARKER in str(group["scope"]).lower()]
    exact = [group for group in by_scope.values() if str(group["scope"]).lower() == NMS_SCOPE_MARKER]
    nms_group = None
    # The downstream identity is display only. Locating the NMS hop must not depend on a batch key:
    # a real-machine batch produced before the tester recorded ``nms``/``nmsSim`` still exported
    # the vendor spans, and silently skipping them would turn a gate into an abstention. The exact
    # scope wins over a lookalike (``a2a-t-server`` over ``a2a-t-server-2``); only a genuinely
    # ambiguous set of marker scopes with no exact match stays unjudged.
    if len(exact) == 1:
        nms_group = exact[0]
    elif len(candidates) == 1:
        nms_group = candidates[0]
    elif len(by_scope) == 1:
        nms_group = next(iter(by_scope.values()))
    if rounds is None:
        # The workflow-root metadata does not always carry ``rounds`` (a neutral root, an older
        # export). The observed run still knows: one client frame per negotiation round plus the
        # initial request, and every negotiation span carries its round.
        observed_rounds = [item.round for item in observations if item.round is not None]
        if observed_rounds:
            rounds = max(observed_rounds)
        elif client_observed > 0:
            rounds = client_observed - 1
        else:
            rounds = 0
    expected = 1 + rounds if nms_group is not None else None
    return {
        "rounds": rounds,
        "client": {"runId": run_id, "observed": client_observed},
        "nms": {
            "expected": expected,
            "observed": (_int(nms_group["count"]) or 0) if nms_group is not None else None,
            "scope": str(nms_group["scope"]) if nms_group is not None else None,
        },
        "otherHops": [{"runId": other_run, "observed": count} for other_run, count in sorted(other.items())],
        "serverSendCount": {
            "total": len(server),
            "byScope": list(by_scope.values()),
        },
    }


#: A status frame in this state hands the turn back to the client, so its text is a proposal.
_TURN_BACK_STATE: Final[str] = "TASK_STATE_INPUT_REQUIRED"
_A2A_SEND_PREFIXES: Final[tuple[str, ...]] = ("SendMessage", "SendStreamingMessage")
_PROPOSAL_KINDS: Final[frozenset[str]] = frozenset({"propose", "decision_requested"})


def trace_negotiation_messages(payload: Mapping[str, object]) -> list[dict[str, object]]:
    """Return the server proposals observable in one trace payload.

    The review's negotiation dimension normally reads the client store's ``negotiation_events``.
    A pure A2A question can arrive as a push notification and the store may keep only the decision;
    the same text is still in the trace (``response.statusUpdate.status.message``). Client
    ``propose`` / ``decision_requested`` rows win because they are what the client observed; a
    server ``SendMessage*`` reply counts only when it hands the turn back, so an execution status
    is not mistaken for a proposal.

    Langfuse returns observations newest first. Frames are sorted by their start time before a
    missing ``round`` is numbered by occurrence; a recorded round always wins.
    """
    observations = payload.get("observations")
    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
        return []
    indexed: list[tuple[int, Mapping[str, Any]]] = [
        (index, row) for index, row in enumerate(observations) if isinstance(row, Mapping)
    ]
    indexed.sort(key=lambda item: (_text(item[1].get("startedAt")), item[0]))
    rows = [row for _index, row in indexed]

    proposed: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in rows:
        kind = _text(row.get("kind"))
        content = _text(row.get("content"))
        if kind not in _PROPOSAL_KINDS or not content or content in seen:
            continue
        seen.add(content)
        proposed.append({"round": _int(row.get("round")), "kind": kind, "content": content})
    if proposed:
        return _with_negotiation_rounds(proposed)

    replies: list[dict[str, object]] = []
    for row in rows:
        if not _text(row.get("name")).startswith(_A2A_SEND_PREFIXES):
            continue
        state = _text(row.get("state"))
        if state and state != _TURN_BACK_STATE:
            continue
        content = _text(row.get("response"))
        if not content or content in seen:
            continue
        seen.add(content)
        replies.append({"round": _int(row.get("round")), "kind": "message", "content": content})
    return _with_negotiation_rounds(replies)


def _with_negotiation_rounds(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    """Number the frames that carry no recorded round, in observation-time order."""
    for index, message in enumerate(messages):
        if not isinstance(message.get("round"), int):
            message["round"] = index + 1
    return messages


def _side(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> tuple[str, str]:
    side = _row_side(dict(row))
    if side in {"client", "server"}:
        source = "metadata" if _text(metadata.get("side")) else "scope"
        return side, source
    attribute = _text(
        metadata.get("attributes.a2at.span.side") or metadata.get("attributes.gen_ai.agent.a2at.span.side")
    ).lower()
    if attribute in {"client", "server"}:
        return attribute, "attribute"
    return side or "unknown", "unknown"


def _scope(metadata: Mapping[str, Any]) -> str:
    """Return the service/scope name of one row.

    Langfuse metadata is usually flattened to dotted keys, but an export can also keep the raw
    nesting (``scope: {name}`` / ``resourceAttributes: {service.name}``). Both shapes must yield
    ``a2a-t-server``; otherwise a real vendor hop would be reported as unidentified.
    """
    scope = _text(metadata.get("scope.name") or metadata.get("resourceAttributes.service.name"))
    if scope:
        return scope
    nested_scope = metadata.get("scope")
    if isinstance(nested_scope, Mapping):
        scope = _text(nested_scope.get("name"))
        if scope:
            return scope
    nested_attributes = metadata.get("resourceAttributes")
    if isinstance(nested_attributes, Mapping):
        service = nested_attributes.get("service.name")
        if not isinstance(service, str):
            raw_service = nested_attributes.get("service")
            service = raw_service.get("name") if isinstance(raw_service, Mapping) else None
        scope = _text(service)
    return scope


def _round(metadata: Mapping[str, Any]) -> int | None:
    for key in ("round", "attributes.gen_ai.agent.a2at.negotiation.round"):
        value = _int(metadata.get(key))
        if value is not None:
            return value
    return None


def _root_metadata(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for row in rows:
        name = _text(row.get("name"))
        if not name.startswith(("run ·", "workflow ·", "creation ·")):
            continue
        metadata = row.get("metadata")
        if isinstance(metadata, Mapping):
            return dict(metadata)
    return {}


def _read_observations(path: Path) -> tuple[list[Mapping[str, Any]], bool, tuple[str, ...]]:
    if not path.is_file():
        return [], False, ("raw/observations.jsonl is missing",)
    rows: list[Mapping[str, Any]] = []
    consumed = 0
    truncated = False
    warnings: list[str] = []
    with path.open("rb") as handle:
        for line in handle:
            consumed += len(line)
            if consumed > MAX_OBSERVATION_BYTES or len(rows) >= MAX_OBSERVATIONS:
                truncated = True
                break
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                warnings.append("raw/observations.jsonl has an unreadable row")
                continue
            if isinstance(value, Mapping):
                rows.append(value)
    return rows, truncated, tuple(warnings)


def _directory_sha256(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
        digest.update(_sha256(path).encode("ascii"))
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clip(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _parse_iso(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _first_bool(metadata: Mapping[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, bool):
            return value
    return None
