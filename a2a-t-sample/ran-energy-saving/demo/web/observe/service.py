"""Langfuse trace exporter and background job manager for the /observe page.

Design: ``docs-local/2026-11-09-langfuse-observer-design.md`` §6.2-§6.5.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from dotenv import dotenv_values
from langfuse_export.cli import export_full, export_metrics
from langfuse_export.client import AuthError, NetworkError, NotFoundError
from langfuse_export.config import Config, load_config
from shared.config import SampleConfig
from shared.run_constants import FINAL_STATE_INPUT_INVALID, FINAL_STATE_INTENT_NOT_CONFIRMED

TRACE_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
DEFAULT_MAX_TRACES = 200
DEFAULT_TRACE_TIMEOUT_SECONDS = 180.0
DEFAULT_JOB_TIMEOUT_SECONDS = 3600.0

_SERVER_SIDE_WARNING = "no server-side observations yet; re-export after the run flushes"
_JOB_TIME_LIMIT_WARNING = "job time limit reached"
_TRACE_TIMEOUT_WARNING = "trace export timed out"


class ObserveBusyError(RuntimeError):
    """Raised when a second observe job is requested while one is running."""


class InvalidTraceIdError(ValueError):
    """Raised when the request contains no usable or a malformed trace id."""


class TooManyTracesError(ValueError):
    """Raised when a request exceeds the configured number of traces."""


class TraceExporter(Protocol):
    """Minimal upstream surface the job manager needs."""

    def metrics(
        self,
        trace_id: str,
        *,
        run_id: str | None = None,
        round_mode: str = "legacy",
    ) -> tuple[Mapping[str, Any], Sequence[str]]: ...

    def full_export(
        self,
        trace_id: str,
        *,
        run_id: str | None = None,
        round_mode: str = "legacy",
    ) -> Mapping[str, Any]: ...


_PRE_SEND_BLOCKED_STATES = frozenset({FINAL_STATE_INPUT_INVALID, FINAL_STATE_INTENT_NOT_CONFIRMED})


def select_primary_run(
    runs: Sequence[Mapping[str, object]],
) -> Mapping[str, object] | None:
    """Choose the attempt that should represent a shared trace in the observe row.

    A correction trace contains a blocked preflight and the corrected retry. The retry reached
    the wire (a Task/context or downstream hop) and is not a pre-send block; among candidates the
    newest wins. Falling back to createdAt keeps two blocked attempts deterministic.
    """
    candidates = [run for run in runs if isinstance(run, Mapping)]
    if not candidates:
        return None

    def reached_wire(run: Mapping[str, object]) -> bool:
        return any(
            str(run.get(key) or "").strip()
            for key in (
                "serverTaskId",
                "serverContextId",
                "downstreamTaskId",
                "downstreamContextId",
                "downstreamHop",
            )
        )

    def rank(run: Mapping[str, object]) -> tuple[int, int, str, str]:
        blocked = str(run.get("finalState") or "") in _PRE_SEND_BLOCKED_STATES
        return (
            1 if reached_wire(run) else 0,
            0 if blocked else 1,
            str(run.get("createdAt") or ""),
            str(run.get("runId") or ""),
        )

    return max(candidates, key=rank)


def _langfuse_environ(env_path: Path | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    if env_path is not None and env_path.is_file():
        for key, value in dotenv_values(env_path).items():
            if key.startswith("LANGFUSE_") and isinstance(value, str):
                merged[key] = value
    for key, value in os.environ.items():
        if key.startswith("LANGFUSE_"):
            merged[key] = value
    return merged


class LangfuseExporter:
    """Adapter around the vendored ``langfuse_export`` package."""

    def __init__(
        self,
        *,
        base_url: str,
        public_key: str,
        secret_key: str,
        env_path: Path | None,
        runs_dir: Path,
    ) -> None:
        self._base_url = base_url
        self._public_key = public_key
        self._secret_key = secret_key
        self._env_path = env_path
        self._runs_dir = runs_dir
        self._config: Config | None = None

    def _get_config(self) -> Config:
        if self._config is None:
            self._config = load_config(
                host=self._base_url,
                public_key=self._public_key,
                secret_key=self._secret_key,
                environ=_langfuse_environ(self._env_path),
                cwd=self._runs_dir,
            )
        return self._config

    def metrics(
        self,
        trace_id: str,
        *,
        run_id: str | None = None,
        round_mode: str = "legacy",
    ) -> tuple[Mapping[str, Any], Sequence[str]]:
        result = export_metrics(self._get_config(), trace_id, run_id=run_id, round_mode=round_mode)
        return result["metrics"], tuple(result.get("warnings") or ())

    def full_export(
        self,
        trace_id: str,
        *,
        run_id: str | None = None,
        round_mode: str = "legacy",
    ) -> Mapping[str, Any]:
        return export_full(
            self._get_config(),
            trace_id,
            self._runs_dir / "observability",
            zip_file=True,
            run_id=run_id,
            round_mode=round_mode,
        )


def build_exporter(
    config: SampleConfig,
    env_path: Path | None,
    runs_dir: Path,
) -> LangfuseExporter | None:
    """Return an exporter when Langfuse credentials are configured."""
    if not config.langfuse_configured:
        return None
    return LangfuseExporter(
        base_url=config.langfuse_base_url,
        public_key=config.langfuse_public_key,
        secret_key=config.langfuse_secret_key,
        env_path=env_path,
        runs_dir=runs_dir,
    )


@dataclass
class _Job:
    job_id: str
    created_at: str
    full: bool
    trace_ids: list[str]
    round_mode: str = "legacy"
    status: str = "running"
    results: list[dict[str, Any]] = field(default_factory=list)
    task: asyncio.Task[None] | None = None


class ObserveJobManager:
    """Sequentially export Langfuse traces in a single background job."""

    def __init__(
        self,
        *,
        runs_dir: Path,
        exporter: TraceExporter | None,
        local_run: Callable[[str], Mapping[str, object] | None] | None = None,
        local_runs: Callable[[str], Sequence[Mapping[str, object]]] | None = None,
        max_traces: int = DEFAULT_MAX_TRACES,
        trace_timeout_seconds: float = DEFAULT_TRACE_TIMEOUT_SECONDS,
        job_timeout_seconds: float = DEFAULT_JOB_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runs_dir = runs_dir
        self._exporter = exporter
        self._local_run = local_run
        self._local_runs = local_runs
        self._max_traces = max_traces
        self._trace_timeout_seconds = trace_timeout_seconds
        self._job_timeout_seconds = job_timeout_seconds
        self._clock = clock
        self._jobs: dict[str, _Job] = {}
        self._running_job_id: str | None = None

    @property
    def configured(self) -> bool:
        return self._exporter is not None

    @property
    def running_job_id(self) -> str | None:
        """Return the job the page should resume polling, if one is running."""
        return self._running_job_id

    def create(
        self,
        trace_ids: Sequence[str],
        *,
        full: bool = False,
        round_mode: str = "legacy",
    ) -> dict[str, object]:
        if self._exporter is None:
            raise RuntimeError("langfuse is not configured")
        stripped = [value.strip() for value in trace_ids]
        if not stripped or any(not value for value in stripped):
            raise InvalidTraceIdError("no trace ids provided")
        unique = list(dict.fromkeys(stripped))
        for value in unique:
            if not TRACE_ID_PATTERN.fullmatch(value):
                raise InvalidTraceIdError(f"invalid trace id: {value}")
        if len(unique) > self._max_traces:
            raise TooManyTracesError(f"at most {self._max_traces} traces per job")
        if self._running_job_id is not None:
            raise ObserveBusyError("an observe job is already running")
        job_id = uuid.uuid4().hex
        job = _Job(
            job_id=job_id,
            created_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            full=full,
            trace_ids=unique,
            round_mode=round_mode,
        )
        self._jobs[job_id] = job
        self._running_job_id = job_id
        job.task = asyncio.create_task(self._run(job_id))
        snapshot = self.get(job_id)
        assert snapshot is not None
        return snapshot

    async def _run(self, job_id: str) -> None:
        job = self._jobs[job_id]
        started = self._clock()
        try:
            for index, trace_id in enumerate(job.trace_ids):
                seq = index + 1
                elapsed = self._clock() - started
                if elapsed >= self._job_timeout_seconds:
                    job.results.append(
                        self._entry(
                            seq,
                            trace_id,
                            "timeout",
                            0,
                            [_JOB_TIME_LIMIT_WARNING],
                            None,
                        )
                    )
                    continue
                job.results.append(await self._run_one(seq, trace_id, job.full, job.round_mode))
        finally:
            job.status = "done"
            self._running_job_id = None
            try:
                self._persist(job)
            except OSError:
                pass

    async def _run_one(self, seq: int, trace_id: str, full: bool, round_mode: str) -> dict[str, Any]:
        started = self._clock()
        try:
            entry = await asyncio.wait_for(
                asyncio.to_thread(self._one, seq, trace_id, full, round_mode, started),
                timeout=self._trace_timeout_seconds,
            )
        except asyncio.TimeoutError:
            entry = self._entry(
                seq,
                trace_id,
                "timeout",
                self._duration_ms(started),
                [_TRACE_TIMEOUT_WARNING],
                None,
            )
        return entry

    def _one(self, seq: int, trace_id: str, full: bool, round_mode: str, started: float) -> dict[str, Any]:
        assert self._exporter is not None
        warnings: list[str]
        metrics: Mapping[str, Any] | None = None
        export_dir: str | None = None
        archive: str | None = None
        candidates = self._lookup_local_runs(trace_id)
        primary = select_primary_run(candidates)
        selected_run_id = _text(primary.get("runId")) if primary is not None else None
        try:
            if full:
                result = self._exporter.full_export(
                    trace_id,
                    run_id=selected_run_id,
                    round_mode=round_mode,
                )
                raw_metrics = result.get("metrics")
                metrics = raw_metrics if isinstance(raw_metrics, Mapping) else None
                warnings = [str(item) for item in result.get("warnings") or ()]
                export_dir = _text(result.get("exportDir"))
                archive = _text(result.get("zip"))
            else:
                raw_metrics, raw_warnings = self._exporter.metrics(
                    trace_id,
                    run_id=selected_run_id,
                    round_mode=round_mode,
                )
                metrics = raw_metrics
                warnings = [str(item) for item in raw_warnings]
        except NotFoundError as exc:
            return self._entry(seq, trace_id, "not_found", self._duration_ms(started), _failure_warnings(exc), None)
        except AuthError as exc:
            return self._entry(seq, trace_id, "auth", self._duration_ms(started), _failure_warnings(exc), None)
        except NetworkError as exc:
            return self._entry(seq, trace_id, "network", self._duration_ms(started), _failure_warnings(exc), None)
        except SystemExit as exc:
            # argparse inside the export wrappers raises SystemExit, a BaseException that
            # would otherwise escape this thread and kill the whole server.
            return self._entry(seq, trace_id, "error", self._duration_ms(started), [f"SystemExit: {exc.code}"], None)
        except Exception as exc:  # noqa: BLE001 - one trace must not abort the job
            return self._entry(seq, trace_id, "error", self._duration_ms(started), _failure_warnings(exc), None)

        if _server_observations(metrics) == 0:
            warnings.append(_SERVER_SIDE_WARNING)
        local_run = dict(primary) if primary is not None else None
        if local_run is not None and len(candidates) > 1:
            local_run["attemptCount"] = len(candidates)
            local_run["attemptRunIds"] = [_text(run.get("runId")) for run in candidates if _text(run.get("runId"))]
        return self._entry(
            seq,
            trace_id,
            "ok",
            self._duration_ms(started),
            warnings,
            metrics,
            local_run=local_run,
            export_dir=export_dir,
            archive=archive,
        )

    def _lookup_local_runs(self, trace_id: str) -> list[dict[str, Any]]:
        if self._local_runs is not None:
            try:
                return [dict(run) for run in self._local_runs(trace_id)]
            except Exception:
                return []
        if self._local_run is None:
            return []
        try:
            value = self._local_run(trace_id)
        except Exception:
            return []
        return [dict(value)] if value is not None else []

    def _duration_ms(self, started: float) -> int:
        return int((self._clock() - started) * 1000)

    @staticmethod
    def _entry(
        seq: int,
        trace_id: str,
        status: str,
        duration_ms: int,
        warnings: Sequence[str],
        metrics: Mapping[str, Any] | None,
        *,
        local_run: Mapping[str, Any] | None = None,
        export_dir: str | None = None,
        archive: str | None = None,
    ) -> dict[str, Any]:
        return {
            "seq": seq,
            "traceId": trace_id,
            "status": status,
            "durationMs": duration_ms,
            "warnings": list(warnings),
            "metrics": dict(metrics) if metrics is not None else None,
            "localRun": dict(local_run) if local_run is not None else None,
            "exportDir": export_dir,
            "archive": archive,
        }

    def get(self, job_id: str, *, since: int = 0) -> dict[str, Any] | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        return self._snapshot(job, since=since)

    def _snapshot(self, job: _Job, *, since: int = 0) -> dict[str, Any]:
        done = sum(1 for row in job.results if row["status"] == "ok")
        return {
            "jobId": job.job_id,
            "createdAt": job.created_at,
            "full": job.full,
            "roundMode": job.round_mode,
            "status": job.status,
            "total": len(job.trace_ids),
            "done": done,
            "failed": len(job.results) - done,
            "nextSeq": job.results[-1]["seq"] + 1 if job.results else 1,
            "results": [dict(row) for row in job.results if row["seq"] >= since],
        }

    async def wait(self, job_id: str) -> dict[str, Any]:
        job = self._jobs[job_id]
        if job.task is not None:
            await job.task
        snapshot = self.get(job_id)
        assert snapshot is not None
        return snapshot

    def rows(self, job_id: str) -> list[dict[str, Any]] | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        return [dict(row) for row in job.results]

    @property
    def _jobs_dir(self) -> Path:
        return self._runs_dir / "observability" / "jobs"

    def _persist(self, job: _Job) -> None:
        directory = self._jobs_dir
        directory.mkdir(parents=True, exist_ok=True)
        payload = self._snapshot(job)
        payload["traceIds"] = list(job.trace_ids)
        path = directory / f"{job.job_id}.json"
        temporary = directory / f"{job.job_id}.json.tmp"
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def list_jobs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        directory = self._jobs_dir
        if not directory.is_dir():
            return []
        entries: list[tuple[tuple[str, int], Mapping[str, Any]]] = []
        for path in directory.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, Mapping) or not payload.get("jobId"):
                continue
            try:
                mtime = path.stat().st_mtime_ns
            except OSError:
                mtime = 0
            entries.append(((str(payload.get("createdAt", "")), mtime), payload))
        entries.sort(key=lambda item: item[0], reverse=True)
        summaries: list[dict[str, Any]] = []
        for _, payload in entries[: max(limit, 0)]:
            summaries.append(
                {
                    "jobId": payload.get("jobId"),
                    "createdAt": payload.get("createdAt"),
                    "full": payload.get("full"),
                    "roundMode": payload.get("roundMode", "legacy"),
                    "total": payload.get("total"),
                    "done": payload.get("done"),
                    "failed": payload.get("failed"),
                    "status": payload.get("status"),
                }
            )
        return summaries

    def archive_path(self, job_id: str, trace_id: str) -> Path | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        for row in job.results:
            if row.get("traceId") != trace_id:
                continue
            archive = row.get("archive")
            if not isinstance(archive, str) or not archive:
                return None
            try:
                resolved = Path(archive).resolve()
                root = (self._runs_dir / "observability").resolve()
            except OSError:
                return None
            if resolved.is_file() and resolved.is_relative_to(root):
                return resolved
            return None
        return None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _failure_warnings(exc: Exception) -> list[str]:
    """Render one export failure as a CSV warning, keeping the diagnostic message."""
    message = str(exc).strip()
    return [f"{type(exc).__name__}: {message}" if message else type(exc).__name__]


def _server_observations(metrics: Mapping[str, Any] | None) -> int | None:
    if metrics is None:
        return None
    counts = metrics.get("counts")
    if not isinstance(counts, Mapping):
        return None
    value = counts.get("serverObservations")
    return value if isinstance(value, int) else None
