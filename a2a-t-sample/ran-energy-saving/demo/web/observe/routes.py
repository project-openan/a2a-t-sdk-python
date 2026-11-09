"""HTTP routes of the Langfuse observe console (design 2026-11-09 §6.5-§6.6).

The page is a thin shell over three pure/owned pieces: :mod:`demo.web.observe.table` renders the
CSV contract, :mod:`demo.web.observe.service` runs the background export job, and this module only
wires them to Starlette. Every route is a plain ``Route`` with an async handler, like
:mod:`demo.web.tester.routes`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from protocol.client_store import read_client_run_summaries, read_run_metrics
from shared.config import SampleConfig
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from demo.web.observe.service import (
    DEFAULT_MAX_TRACES,
    InvalidTraceIdError,
    ObserveBusyError,
    ObserveJobManager,
    TooManyTracesError,
    build_exporter,
)
from demo.web.observe.table import (
    FIELD_NOTES,
    MAIN_COLUMNS,
    ROUNDS_COLUMNS,
    build_row,
    fields_csv,
    main_csv,
    rounds_csv,
)

#: Directory of the observe page assets.
OBSERVE_DIR: Final[Path] = Path(__file__).resolve().parent

#: Default / maximum number of local runs returned by ``/api/observe/runs``.
DEFAULT_RUNS_LIMIT: Final[int] = 50
MAX_RUNS_LIMIT: Final[int] = 200

#: Default / maximum number of persisted jobs returned by ``/api/observe/jobs``.
DEFAULT_JOBS_LIMIT: Final[int] = 20
MAX_JOBS_LIMIT: Final[int] = 200

LocalRunLookup = Callable[[str], Mapping[str, object] | None]


def build_observe_routes(
    *,
    config: SampleConfig,
    runs_dir: Path,
    env_path: Path | None,
    client_db_path: Path | None,
    manager: ObserveJobManager | None = None,
) -> list[Route]:
    """Build the ``/observe`` page and its ``/api/observe/*`` endpoints."""
    resolved_manager = manager or ObserveJobManager(
        runs_dir=runs_dir,
        exporter=build_exporter(config, env_path, runs_dir),
        local_run=_local_run_lookup(client_db_path),
    )

    async def page(request: Request) -> Response:
        """Serve the observe console shell."""
        del request
        return FileResponse(OBSERVE_DIR / "index.html")

    async def stylesheet(request: Request) -> Response:
        """Serve the observe stylesheet."""
        del request
        return FileResponse(OBSERVE_DIR / "app.css", media_type="text/css")

    async def script(request: Request) -> Response:
        """Serve the observe module."""
        del request
        return FileResponse(OBSERVE_DIR / "main.js", media_type="text/javascript")

    async def meta(request: Request) -> Response:
        """Describe the Langfuse configuration the page may use (no secrets)."""
        del request
        from langfuse_export import __version__

        return JSONResponse(
            {
                "configured": resolved_manager.configured,
                "hostLabel": _host_label(config.langfuse_base_url),
                "uiBaseUrl": config.langfuse_ui_base_url,
                "toolVersion": __version__,
                "maxTraces": DEFAULT_MAX_TRACES,
                "runningJobId": resolved_manager.running_job_id,
            }
        )

    async def fields(request: Request) -> Response:
        """Return the field legend shared with ``fields.csv``."""
        del request
        return JSONResponse(
            {
                "mainColumns": list(MAIN_COLUMNS),
                "roundsColumns": list(ROUNDS_COLUMNS),
                "fields": [dataclasses.asdict(note) for note in FIELD_NOTES],
            }
        )

    async def runs(request: Request) -> Response:
        """Return the recent local runs as trace candidates for the input box."""
        limit = _bounded_int(
            request.query_params.get("limit"),
            default=DEFAULT_RUNS_LIMIT,
            minimum=1,
            maximum=MAX_RUNS_LIMIT,
        )
        if client_db_path is None:
            return JSONResponse({"runs": []})
        rows = await asyncio.to_thread(read_client_run_summaries, client_db_path, limit)
        candidates = [
            {
                "runId": row.get("runId"),
                "traceId": row.get("traceId"),
                "protocol": row.get("protocol"),
                "scenario": row.get("scenario"),
                "status": row.get("status"),
                "caseId": row.get("caseId"),
                "batchId": row.get("batchId"),
                "createdAt": row.get("createdAt"),
            }
            for row in rows
            if str(row.get("traceId") or "")
        ]
        return JSONResponse({"runs": candidates})

    async def list_jobs(request: Request) -> Response:
        """List the persisted job summaries (newest first)."""
        limit = _bounded_int(
            request.query_params.get("limit"),
            default=DEFAULT_JOBS_LIMIT,
            minimum=1,
            maximum=MAX_JOBS_LIMIT,
        )
        return JSONResponse({"jobs": resolved_manager.list_jobs(limit=limit)})

    async def create_job(request: Request) -> Response:
        """Start one export job over the pasted trace ids."""
        if not resolved_manager.configured:
            return _error(
                "langfuse_not_configured",
                "Langfuse 未配置：请设置 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY。",
                409,
            )
        payload = await _json_body(request)
        if payload is None:
            return _error("invalid_request", "请求体必须是合法 JSON 对象。", 400)
        trace_ids = payload.get("traceIds")
        if not isinstance(trace_ids, list) or not all(isinstance(item, str) for item in trace_ids):
            return _error("invalid_request", "traceIds 必须是字符串数组。", 400)
        full = payload.get("full", False)
        if not isinstance(full, bool):
            return _error("invalid_request", "full 必须是布尔值。", 400)
        try:
            snapshot = resolved_manager.create(trace_ids, full=full)
        except InvalidTraceIdError as exc:
            return _error("invalid_trace_id", str(exc), 400)
        except TooManyTracesError as exc:
            return _error("too_many_traces", str(exc), 400)
        except ObserveBusyError as exc:
            return _error("observe_busy", str(exc), 409)
        except RuntimeError as exc:
            return _error("langfuse_not_configured", str(exc), 409)
        return JSONResponse(snapshot, status_code=201)

    async def get_job(request: Request) -> Response:
        """Return the job snapshot, optionally only results at or after ``since``."""
        job_id = request.path_params["job_id"]
        since = _int_or_default(request.query_params.get("since"), 0)
        snapshot = resolved_manager.get(job_id, since=since)
        if snapshot is None:
            return _error("job_not_found", f"未知 job：{job_id}", 404)
        return JSONResponse(snapshot)

    async def metrics_file(request: Request) -> Response:
        """Download the main table (one row per trace)."""
        job_id = request.path_params["job_id"]
        snapshot = resolved_manager.get(job_id)
        rows = resolved_manager.rows(job_id)
        if snapshot is None or rows is None:
            return _error("job_not_found", f"未知 job：{job_id}", 404)
        if snapshot.get("status") == "running":
            return _error("observe_not_finished", "job 尚未结束，CSV 需在完成后下载。", 409)
        rendered = [
            build_row(
                str(row.get("traceId") or ""),
                str(row.get("status") or ""),
                _mapping_or_none(row.get("metrics")),
                _string_list(row.get("warnings")),
                _mapping_or_none(row.get("localRun")),
            )
            for row in rows
        ]
        return _csv_response(main_csv(rendered), f"langfuse-metrics-{_stamp()}.csv")

    async def rounds_file(request: Request) -> Response:
        """Download the negotiation round detail (only when the job has rounds)."""
        job_id = request.path_params["job_id"]
        snapshot = resolved_manager.get(job_id)
        rows = resolved_manager.rows(job_id)
        if snapshot is None or rows is None:
            return _error("job_not_found", f"未知 job：{job_id}", 404)
        if snapshot.get("status") == "running":
            return _error("observe_not_finished", "job 尚未结束，CSV 需在完成后下载。", 409)
        entries: list[tuple[str, Mapping[str, object]]] = []
        for row in rows:
            if row.get("status") != "ok":
                continue
            metrics = _mapping_or_none(row.get("metrics"))
            if metrics is None:
                continue
            entries.append((str(row.get("traceId") or ""), metrics))
        text = rounds_csv(entries)
        if text is None:
            return _error("no_negotiation_rounds", "没有协商轮次可导出。", 404)
        return _csv_response(text, f"langfuse-rounds-{_stamp()}.csv")

    async def fields_file(request: Request) -> Response:
        """Download the field legend; available for any known job."""
        job_id = request.path_params["job_id"]
        if resolved_manager.get(job_id) is None:
            return _error("job_not_found", f"未知 job：{job_id}", 404)
        return _csv_response(fields_csv(), "langfuse-fields.csv")

    async def table_file(request: Request) -> Response:
        """Download the Issue #1 AF–AW table as an XLSX workbook."""
        job_id = request.path_params["job_id"]
        snapshot = resolved_manager.get(job_id)
        rows = resolved_manager.rows(job_id)
        if snapshot is None or rows is None:
            return _error("job_not_found", f"未知 job：{job_id}", 404)
        if snapshot.get("status") == "running":
            return _error("observe_not_finished", "job 尚未结束，XLSX 需在完成后下载。", 409)
        metrics_list: list[dict[str, Any]] = []
        for row in rows:
            if row.get("status") != "ok":
                continue
            metrics = _mapping_or_none(row.get("metrics"))
            if metrics is None:
                continue
            metrics_list.append(dict(metrics))
        if not metrics_list:
            return _error("no_metrics", "没有可导出的指标行。", 404)
        # Lazy import keeps the page importable without the vendored package's side effects,
        # matching the ``meta`` handler's deferred ``langfuse_export`` import.
        from langfuse_export.table import build_table
        from langfuse_export.writer import write_xlsx

        headers, table_rows = build_table(metrics_list)
        path = runs_dir / "observability" / "jobs" / f"{job_id}-table.xlsx"
        write_xlsx(path, headers, table_rows)
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=f"langfuse-table-{job_id}.xlsx",
        )

    async def archive(request: Request) -> Response:
        """Download one trace's full-export zip when the job produced it."""
        job_id = request.path_params["job_id"]
        trace_id = request.path_params["trace_id"]
        path = resolved_manager.archive_path(job_id, trace_id)
        if path is None:
            return _error("archive_not_found", f"该 trace 没有可下载的完整导出：{trace_id}", 404)
        return FileResponse(path, media_type="application/zip", filename=f"{trace_id}.zip")

    return [
        Route("/observe", page),
        Route("/observe/app.css", stylesheet),
        Route("/observe/main.js", script),
        Route("/api/observe/meta", meta),
        Route("/api/observe/fields", fields),
        Route("/api/observe/runs", runs),
        Route("/api/observe/jobs", list_jobs),
        Route("/api/observe/jobs", create_job, methods=["POST"]),
        Route("/api/observe/jobs/{job_id}", get_job),
        Route("/api/observe/jobs/{job_id}/metrics.csv", metrics_file),
        Route("/api/observe/jobs/{job_id}/rounds.csv", rounds_file),
        Route("/api/observe/jobs/{job_id}/fields.csv", fields_file),
        Route("/api/observe/jobs/{job_id}/table.xlsx", table_file),
        Route("/api/observe/jobs/{job_id}/archives/{trace_id}.zip", archive),
    ]


def _local_run_lookup(path: Path | None) -> LocalRunLookup | None:
    """Build the H-group join used by the job manager, ``None`` without a client database."""
    if path is None:
        return None

    def lookup(trace_id: str) -> dict[str, object] | None:
        try:
            rows = read_client_run_summaries(path, limit=1, trace_id=trace_id)
            if not rows:
                return None
            row = rows[0]
            run_id = str(row.get("runId") or "")
            accuracy: Mapping[str, object] = {}
            if run_id:
                metrics = read_run_metrics(path, run_id)
                if isinstance(metrics, Mapping):
                    value = metrics.get("accuracy")
                    if isinstance(value, Mapping):
                        accuracy = {str(key): item for key, item in value.items()}
            return {
                "runId": run_id,
                "status": row.get("status"),
                "finalState": row.get("finalState"),
                "passed": accuracy.get("passed"),
                "accuracy": accuracy.get("score"),
                "startedAt": row.get("createdAt"),
                "finishedAt": row.get("finishedAt"),
            }
        except Exception:  # noqa: BLE001 - the local join is best effort
            return None

    return lookup


async def _json_body(request: Request) -> Mapping[str, Any] | None:
    """Parse one JSON object body, ``None`` when malformed."""
    try:
        payload = json.loads((await request.body()) or b"{}")
    except ValueError:
        return None
    if not isinstance(payload, Mapping):
        return None
    return payload


def _mapping_or_none(value: object) -> Mapping[str, object] | None:
    """Return one object as a plain string-keyed mapping, ``None`` otherwise."""
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def _string_list(value: object) -> list[str]:
    """Return one warning list as strings (a missing value is an empty list)."""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value]


def _bounded_int(raw: str | None, *, default: int, minimum: int, maximum: int) -> int:
    """Parse one query integer and clamp it into ``[minimum, maximum]``."""
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _int_or_default(raw: str | None, default: int) -> int:
    """Parse one query integer, falling back to ``default``."""
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _host_label(url: str) -> str:
    """Return the netloc of ``url`` without scheme, path or credentials."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return netloc or url


def _stamp() -> str:
    """Return the local download timestamp used in CSV filenames."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _csv_response(text: str, filename: str) -> Response:
    """Encode one CSV for Excel (UTF-8 BOM) and offer it as a download."""
    return Response(
        text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    """Build the shared error envelope of the observe API."""
    return JSONResponse({"error": code, "message": message}, status_code=status_code)


__all__ = ["OBSERVE_DIR", "build_observe_routes"]
