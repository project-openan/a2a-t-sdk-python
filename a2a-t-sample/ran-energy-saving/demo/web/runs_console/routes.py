"""HTTP routes of the internal run console.

The console is a diagnostic page for the test team: it lists every persisted client run, shows
where the request came from, and opens one run's metrics, negotiation events, transcript, wire
payloads and server trace. It reuses the persisted client store and the live ``RunManager``; it
does not implement protocol logic or start runs.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from protocol.client_service import RunManager
from protocol.client_store import (
    RUN_STATUSES,
    count_client_runs,
    read_client_run_detail,
    read_client_run_summaries,
    read_run_metrics,
)
from shared.run_source import RUN_SOURCE_KINDS
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from demo.web.runs_console.service import RunConsoleService
from demo.web.wire_records import read_wire_records

#: Directory of the console page assets.
RUN_CONSOLE_DIR: Final[Path] = Path(__file__).resolve().parent

#: Largest transcript document the console returns (20 MB).
MAX_TRANSCRIPT_BYTES: Final[int] = 20 * 1024 * 1024

#: Newest server-trace records returned by the detail route.
MAX_SERVER_SPANS: Final[int] = 5000


def build_run_console_routes(
    *,
    runs: RunManager,
    client_db_path: Path | None,
    runs_dir: Path,
    protocols: Sequence[str],
    scenarios_by_protocol: Mapping[str, Sequence[str]],
    language: str,
    run_dir: Path | None = None,
) -> list[Route]:
    """Build the internal run-console routes."""
    service = RunConsoleService(
        client_db_path=client_db_path,
        runs_dir=runs_dir,
        run_dir=run_dir,
        runs=runs,
    )

    async def page(request: Request) -> Response:
        """Serve the run-console shell."""
        del request
        return FileResponse(
            RUN_CONSOLE_DIR / "index.html",
            headers={"Cache-Control": "no-store"},
        )

    async def stylesheet(request: Request) -> Response:
        """Serve the console stylesheet."""
        del request
        return FileResponse(
            RUN_CONSOLE_DIR / "app.css",
            media_type="text/css",
            headers={"Cache-Control": "no-store"},
        )

    async def script(request: Request) -> Response:
        """Serve the console module."""
        del request
        return FileResponse(
            RUN_CONSOLE_DIR / "main.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    async def meta(request: Request) -> Response:
        """Describe what the console may offer."""
        del request
        enabled = client_db_path is not None and Path(client_db_path).is_file()
        return JSONResponse(
            {
                "enabled": enabled,
                "database": str(client_db_path) if client_db_path is not None else None,
                "runsDir": str(runs_dir),
                "language": language,
                "protocols": list(protocols),
                "scenariosByProtocol": {
                    key: list(values) for key, values in scenarios_by_protocol.items()
                },
                "sourceKinds": list(RUN_SOURCE_KINDS),
                "statuses": list(RUN_STATUSES),
            }
        )

    async def list_runs(request: Request) -> Response:
        """Return one filtered page of persisted runs."""
        if client_db_path is None:
            return JSONResponse({"enabled": False, "runs": [], "total": 0})
        limit = _int_param(request, "limit", 100, minimum=1, maximum=500)
        offset = _int_param(request, "offset", 0, minimum=0, maximum=1_000_000)
        filters = {
            "protocol": _text_param(request, "protocol"),
            "status": _text_param(request, "status"),
            "source_kind": _text_param(request, "source"),
            "scenario": _text_param(request, "scenario"),
            "query": _text_param(request, "query"),
        }
        rows, total = await asyncio.gather(
            asyncio.to_thread(
                read_client_run_summaries,
                client_db_path,
                limit,
                offset=offset,
                **filters,
            ),
            asyncio.to_thread(count_client_runs, client_db_path, **filters),
        )
        return JSONResponse(
            {
                "enabled": True,
                "runs": rows,
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    async def transcript_download(request: Request) -> Response:
        """Return one persisted transcript as JSON or Markdown (the live route only knows runs)."""
        if client_db_path is None:
            return JSONResponse({"error": "The client database is disabled."}, status_code=404)
        run_id = request.path_params["run_id"]
        detail = await asyncio.to_thread(read_client_run_detail, client_db_path, run_id)
        if detail is None:
            return JSONResponse({"error": "Unknown run.", "code": "unknown_run"}, status_code=404)
        suffix = "json" if request.url.path.endswith(".json") else "md"
        key = "transcriptJson" if suffix == "json" else "transcriptMarkdown"
        path = detail.get(key)
        text = await asyncio.to_thread(_read_text, path, runs_dir)
        if text is None:
            return JSONResponse({"error": "Transcript not found."}, status_code=404)
        media = "application/json" if suffix == "json" else "text/markdown"
        return Response(text, media_type=media)

    async def run_detail(request: Request) -> Response:
        """Return one run with its metrics, transcript, wire records and server spans."""
        if client_db_path is None:
            return JSONResponse(
                {"error": "The client database is disabled.", "code": "client_db_disabled"},
                status_code=404,
            )
        run_id = request.path_params["run_id"]
        detail = await asyncio.to_thread(read_client_run_detail, client_db_path, run_id)
        if detail is None:
            return JSONResponse({"error": "Unknown run.", "code": "unknown_run"}, status_code=404)
        metrics = await asyncio.to_thread(read_run_metrics, client_db_path, run_id)
        if metrics is not None:
            detail["metrics"] = metrics
        detail["wire"] = await asyncio.to_thread(read_wire_records, runs_dir, run_id)
        transcript = await asyncio.to_thread(
            _read_transcript, detail.get("transcriptJson"), runs_dir
        )
        if transcript is not None:
            detail["transcript"] = transcript
        server_spans = await asyncio.to_thread(
            _read_jsonl, runs_dir / run_id / "server-trace.jsonl", MAX_SERVER_SPANS
        )
        if server_spans:
            detail["serverTrace"] = server_spans
        live = runs.get(run_id)
        if live is not None:
            detail["live"] = live.snapshot()
        return JSONResponse(detail)

    async def summary(request: Request) -> Response:
        """Return the conclusion/identity/metrics block of one run."""
        payload = await asyncio.to_thread(service.summary, request.path_params["run_id"])
        if payload is None:
            return _unknown_run(request.path_params["run_id"])
        return JSONResponse(payload)

    async def timeline(request: Request) -> Response:
        """Return one filtered page of the merged log stream."""
        sources = _csv_param(request, "source")
        phase = _text_param(request, "phase")
        query = _text_param(request, "q") or _text_param(request, "query")
        offset = _int_param(request, "offset", 0, minimum=0, maximum=1_000_000)
        limit = _int_param(request, "limit", 200, minimum=1, maximum=500)
        payload = await asyncio.to_thread(
            service.timeline,
            request.path_params["run_id"],
            sources=sources,
            phase=phase,
            query=query,
            offset=offset,
            limit=limit,
        )
        if payload is None:
            return _unknown_run(request.path_params["run_id"])
        return JSONResponse(payload)

    async def timeline_entry(request: Request) -> Response:
        """Return one timeline entry and its payload."""
        payload = await asyncio.to_thread(
            service.entry_payload,
            request.path_params["run_id"],
            request.path_params["entry_id"],
        )
        if payload is None:
            return JSONResponse(
                {"error": "Unknown run or entry.", "code": "entry_not_found"},
                status_code=404,
            )
        return JSONResponse(payload)

    async def negotiation(request: Request) -> Response:
        """Return the structured turns and negotiation events of one run."""
        payload = await asyncio.to_thread(
            service.negotiation, request.path_params["run_id"]
        )
        if payload is None:
            return _unknown_run(request.path_params["run_id"])
        return JSONResponse(payload)

    async def files(request: Request) -> Response:
        """Return the logical raw-file list of one run."""
        payload = await asyncio.to_thread(service.files, request.path_params["run_id"])
        if payload is None:
            return _unknown_run(request.path_params["run_id"])
        return JSONResponse(payload)

    async def raw_file(request: Request) -> Response:
        """Return one whitelisted raw file as text."""
        result = await asyncio.to_thread(
            service.read_file,
            request.path_params["run_id"],
            request.path_params["name"],
        )
        if result is None:
            return JSONResponse(
                {"error": "Unknown or unavailable file.", "code": "file_not_found"},
                status_code=404,
            )
        text, truncated = result
        return Response(
            text,
            media_type="text/plain; charset=utf-8",
            headers={"X-Truncated": "true" if truncated else "false"},
        )

    return [
        Route("/runs", page),
        Route("/runs/", page),
        Route("/runs/app.css", stylesheet),
        Route("/runs/main.js", script),
        Route("/api/run-console/meta", meta),
        Route("/api/run-console/runs", list_runs),
        Route("/api/run-console/runs/{run_id}/transcript.json", transcript_download),
        Route("/api/run-console/runs/{run_id}/transcript.md", transcript_download),
        Route("/api/run-console/runs/{run_id}/summary", summary),
        Route("/api/run-console/runs/{run_id}/timeline", timeline),
        Route("/api/run-console/runs/{run_id}/timeline/{entry_id}", timeline_entry),
        Route("/api/run-console/runs/{run_id}/negotiation", negotiation),
        Route("/api/run-console/runs/{run_id}/files", files),
        Route("/api/run-console/runs/{run_id}/files/{name}", raw_file),
        Route("/api/run-console/runs/{run_id}", run_detail),
    ]


def _text_param(request: Request, name: str) -> str | None:
    """Return one trimmed query parameter, ``None`` when empty."""
    value = request.query_params.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _csv_param(request: Request, name: str) -> list[str]:
    """Return one comma-separated query parameter as trimmed values."""
    value = request.query_params.get(name)
    if value is None:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _unknown_run(run_id: str) -> JSONResponse:
    """Build the shared unknown-run error response."""
    return JSONResponse(
        {"error": f"Unknown run: {run_id}", "code": "unknown_run"},
        status_code=404,
    )


def _int_param(request: Request, name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Return one clamped integer query parameter."""
    raw = request.query_params.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _resolve_run_file(path_value: object, runs_dir: Path) -> Path | None:
    """Resolve one stored path only when it points at a file inside the runs directory."""
    if not isinstance(path_value, str) or not path_value:
        return None
    try:
        resolved = Path(path_value).resolve()
    except OSError:
        return None
    root = runs_dir.resolve()
    if resolved != root and root not in resolved.parents:
        return None
    if not resolved.is_file() or resolved.stat().st_size > MAX_TRANSCRIPT_BYTES:
        return None
    return resolved


def _read_transcript(path_value: object, runs_dir: Path) -> dict[str, object] | None:
    """Read one persisted transcript only when its path is inside the runs directory."""
    resolved = _resolve_run_file(path_value, runs_dir)
    if resolved is None:
        return None
    try:
        document = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _read_text(path_value: object, runs_dir: Path) -> str | None:
    """Read one persisted transcript file (JSON or Markdown) with the same path guard."""
    resolved = _resolve_run_file(path_value, runs_dir)
    if resolved is None:
        return None
    try:
        return resolved.read_text(encoding="utf-8")
    except OSError:
        return None


def _read_jsonl(path: Path, limit: int) -> list[dict[str, object]]:
    """Read at most ``limit`` JSON objects from one JSONL file."""
    if not path.is_file():
        return []
    records: list[dict[str, object]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if len(records) >= limit:
                    break
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
        return records
    return records
