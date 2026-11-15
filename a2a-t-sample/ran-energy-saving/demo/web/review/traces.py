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
SNAPSHOT_SCHEMA: Final[str] = "review-trace/v1"

EXPORT_STATE_EXPORTED: Final[str] = "exported"
EXPORT_STATE_PARTIAL: Final[str] = "partial"
EXPORT_STATE_FAILED: Final[str] = "failed"
EXPORT_STATE_NOT_EXPORTED: Final[str] = "not_exported"

NMS_SCOPE_MARKER: Final[str] = "a2a-t-server"

#: One snapshot larger than this is truncated to a ``partial`` state instead of stalling a scan.
MAX_OBSERVATIONS: Final[int] = 5000
MAX_OBSERVATION_BYTES: Final[int] = 32 * 1024 * 1024
#: Task-T / request text kept in the snapshot payload. The producer already caps some fields at
#: 4000 characters; keeping 16 KiB covers the full rendered Task-T and stays well below a
#: customer-pack problem.
MAX_PROMPT_TEXT: Final[int] = 16384

#: A fresh snapshot is served without a Langfuse request. The page fetches one trace lazily when
#: it is opened, so a 200-target scan never consumes the API quota up front.
DEFAULT_TRACE_TTL_SECONDS: Final[float] = 300.0

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
    verdict: str
    interception: bool
    run_id: str
    scope: str
    span_id: str
    parent_id: str
    user_request: str = ""
    prompt_text: str = ""
    prompt_truncated: bool = False
    params: Mapping[str, object] | None = None

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
        candidates = [
            path
            for path in self.root.glob(f"{trace_id}_*")
            if path.is_dir() and self._is_inside(path)
        ]
        if not candidates:
            return None
        return max(candidates, key=self._rank)

    def load(self, trace_id: str) -> TraceSnapshot:
        """Load one snapshot; a missing directory is ``not_exported``, not an error."""
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
                manifest_sha256=_sha256(manifest_path) if manifest_path.is_file() else "",
                export_dir_sha256=_directory_sha256(directory),
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
            manifest_sha256=_sha256(manifest_path) if manifest_path.is_file() else "",
            export_dir_sha256=_directory_sha256(directory),
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
    ) -> dict[str, object]:
        """Return the compact trace block stored beside one review observation."""
        return self._payload(
            self.load(trace_id),
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
        ttl_seconds: float = DEFAULT_TRACE_TTL_SECONDS,
        force: bool = False,
        local_points: Sequence[str] | None = None,
        local_known: Sequence[str] | None = None,
    ) -> dict[str, object]:
        """Return the cached snapshot, fetching lazily only when it is missing or stale.

        ``scan`` must never pass a config: the review page opens this on one case at a time, and
        a fresh snapshot is served without consuming Langfuse quota. Callers should serialize
        fetches per trace so two browser tabs do not issue the same request twice.
        """
        snapshot = self.load(trace_id)
        stale = not self._is_fresh(snapshot, ttl_seconds)
        if config is not None and (force or stale):
            self.fetch(trace_id, config, run_id=run_id)
            snapshot = self.load(trace_id)
            stale = not self._is_fresh(snapshot, ttl_seconds)
        warnings = list(snapshot.warnings)
        if stale and config is None:
            warnings.append("snapshot is older than the cache TTL; lazy fetch is disabled")
        return self._payload(
            snapshot,
            run_id=run_id,
            nms_kind=nms_kind,
            local_points=local_points,
            local_known=local_known,
            warnings=warnings,
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

    def _is_fresh(self, snapshot: TraceSnapshot, ttl_seconds: float) -> bool:
        if snapshot.export_state not in {EXPORT_STATE_EXPORTED, EXPORT_STATE_PARTIAL}:
            return False
        captured = _parse_iso(snapshot.captured_at)
        if captured is None:
            return False
        return (datetime.now(UTC) - captured).total_seconds() <= ttl_seconds

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
    """Project raw Langfuse observation rows onto the review fields."""
    observations: list[Observation] = []
    for row in rows:
        raw_metadata = row.get("metadata")
        metadata: Mapping[str, Any] = raw_metadata if isinstance(raw_metadata, Mapping) else {}
        raw_detail = metadata.get("detail")
        detail: Mapping[str, Any] = raw_detail if isinstance(raw_detail, Mapping) else {}
        raw_type = _text(row.get("type")).upper()
        kind = _text(metadata.get("kind"))
        if not kind:
            kind = "llm" if raw_type == "GENERATION" else "tool" if raw_type == "TOOL" else ""
        side, side_source = _side(row, metadata)
        prompt_text, prompt_truncated = _clip(_text(detail.get("promptText")), MAX_PROMPT_TEXT)
        raw_params = detail.get("params")
        observations.append(
            Observation(
                name=_text(row.get("name")),
                type=raw_type,
                kind=kind,
                stage=_text(metadata.get("stage")),
                side=side,
                side_source=side_source,
                round=_round(metadata),
                performative=_text(metadata.get("attributes.gen_ai.agent.a2at.negotiation.performative")),
                status=_text(metadata.get("status")),
                verdict=_text(detail.get("verdict")),
                interception=_text(detail.get("source")) == "interception",
                run_id=_text(metadata.get("runId")),
                scope=_scope(metadata),
                span_id=_text(row.get("id")),
                parent_id=_text(row.get("parentObservationId")),
                user_request=_text(detail.get("userRequest")),
                prompt_text=prompt_text,
                prompt_truncated=prompt_truncated,
                params=dict(raw_params) if isinstance(raw_params, Mapping) else None,
            )
        )
    return tuple(observations)


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
        if observation.side == "server" and observation.name in _SEND_SPAN_NAMES
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
    nms_group = None
    if nms_kind == "real":
        if len(candidates) == 1:
            nms_group = candidates[0]
        elif len(by_scope) == 1:
            nms_group = next(iter(by_scope.values()))
    expected = 1 + rounds if rounds is not None and nms_group is not None else None
    return {
        "rounds": rounds,
        "client": {"runId": run_id, "observed": client_observed},
        "nms": {
            "expected": expected,
            "observed": (_int(nms_group["count"]) or 0) if nms_group is not None else None,
            "scope": str(nms_group["scope"]) if nms_group is not None else None,
        },
        "otherHops": [
            {"runId": other_run, "observed": count} for other_run, count in sorted(other.items())
        ],
        "serverSendCount": {
            "total": len(server),
            "byScope": list(by_scope.values()),
        },
    }


def _side(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> tuple[str, str]:
    side = _row_side(dict(row))
    if side in {"client", "server"}:
        source = "metadata" if _text(metadata.get("side")) else "scope"
        return side, source
    attribute = _text(
        metadata.get("attributes.a2at.span.side")
        or metadata.get("attributes.gen_ai.agent.a2at.span.side")
    ).lower()
    if attribute in {"client", "server"}:
        return attribute, "attribute"
    return side or "unknown", "unknown"


def _scope(metadata: Mapping[str, Any]) -> str:
    return _text(metadata.get("scope.name") or metadata.get("resourceAttributes.service.name"))


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
