"""HTTP routes of the acceptance review console.

A separate page from ``/tester`` on purpose: the tester serves the batch a browser started and is
scoped to that browser's cookie, while acceptance works across every batch of a window and its unit
of work is one case × protocol verdict plus a human decision. It never starts a run; the tester and
the run console stay the place where a failing case is re-executed.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from eval.osp.catalog import CaseCatalog
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from demo.web.review.export import build_payload, payload_json, payload_yaml, report_markdown
from demo.web.review.service import PROTOCOLS, STATUSES, ReviewService
from demo.web.review.store import CATEGORIES, DECISIONS, ReviewStore
from demo.web.review.traces import TraceStore

#: Directory of the console page assets.
REVIEW_DIR: Final[Path] = Path(__file__).resolve().parent

#: Anonymous operator identity, shared with the tester and the annotation console.
CLIENT_COOKIE: Final[str] = "a2at_tester_client"

#: Review windows look further back than annotation: acceptance covers several batches and rounds.
DEFAULT_DAYS: Final[float] = 7.0
MAX_DAYS: Final[float] = 90.0
MAX_BATCHES: Final[int] = 500


def build_review_routes(
    *,
    catalog: CaseCatalog,
    runs_dir: Path,
    client_db_path: Path | None,
    store_path: Path | None = None,
    trace_store: TraceStore | None = None,
    trace_fetch_enabled: bool = True,
) -> list[Route]:
    """Build the acceptance review routes.

    ``trace_store`` defaults to ``<runs-dir>/review/langfuse``; ``trace_fetch_enabled`` lets the
    standalone read-only instance forbid Langfuse calls.
    """
    resolved_store = ReviewStore(
        store_path
        if store_path is not None
        else Path(runs_dir) / "review" / "review.sqlite3"
    )
    resolved_store.ensure()
    service = ReviewService(
        store=resolved_store,
        catalog=catalog,
        runs_dir=Path(runs_dir),
        client_db_path=client_db_path,
        trace_store=trace_store,
        trace_fetch_enabled=trace_fetch_enabled,
    )

    async def page(request: Request) -> Response:
        """Serve the console shell."""
        del request
        return FileResponse(REVIEW_DIR / "index.html", headers={"Cache-Control": "no-store"})

    async def stylesheet(request: Request) -> Response:
        """Serve the console stylesheet."""
        del request
        return FileResponse(
            REVIEW_DIR / "app.css",
            media_type="text/css",
            headers={"Cache-Control": "no-store"},
        )

    async def script(request: Request) -> Response:
        """Serve the console module."""
        del request
        return FileResponse(
            REVIEW_DIR / "main.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    async def meta(request: Request) -> Response:
        """Describe what the console may offer."""
        del request
        return JSONResponse(
            {
                "protocols": list(PROTOCOLS),
                "statuses": list(STATUSES),
                "decisions": list(DECISIONS),
                "categories": list(CATEGORIES),
                "runsDir": str(runs_dir),
                "clientDb": str(client_db_path) if client_db_path is not None else None,
                "store": str(resolved_store.path),
                "corpusVersion": catalog.version,
                "defaultDays": DEFAULT_DAYS,
                "maxDays": MAX_DAYS,
                "maxBatches": MAX_BATCHES,
            }
        )

    async def cases(request: Request) -> Response:
        """Return every case with its two protocol verdicts and decisions."""
        rows = await asyncio.to_thread(service.cases)
        progress = await asyncio.to_thread(service.progress, rows)
        return JSONResponse({"cases": rows, **progress})

    async def batches(request: Request) -> Response:
        """Return the ingested batches, newest first, without judging them."""
        rows = await asyncio.to_thread(service.batches)
        return JSONResponse({"batches": rows})

    async def batch(request: Request) -> Response:
        """Return every case × protocol judged against one batch, optionally one of its rounds."""
        batch_id = str(request.path_params.get("batch_id") or "")
        round_param = request.query_params.get("round")
        round_value: int | None = None
        if round_param not in (None, ""):
            try:
                round_value = max(1, int(round_param))
            except (TypeError, ValueError):
                return JSONResponse({"error": "round 必须是整数。"}, status_code=400)
        try:
            payload = await asyncio.to_thread(service.batch_rows, batch_id, round_value)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(payload)

    async def scan(request: Request) -> Response:
        """Collect the evidence of the recent tester batches."""
        days = min(MAX_DAYS, max(0.05, _float_param(request, "days", DEFAULT_DAYS)))
        limit = max(1, min(MAX_BATCHES, _int_param(request, "maxBatches", MAX_BATCHES)))
        summary = await asyncio.to_thread(service.scan, days=days, max_batches=limit)
        rows = await asyncio.to_thread(service.cases)
        progress = await asyncio.to_thread(service.progress, rows)
        return JSONResponse({"scan": summary, **progress})

    async def detail(request: Request) -> Response:
        """Return every round of one case × protocol, judged, plus its decision."""
        try:
            case_id = int(request.path_params["case_id"])
        except (KeyError, TypeError, ValueError):
            return JSONResponse({"error": "caseId 必须是整数。"}, status_code=400)
        protocol = request.path_params["protocol"]
        if protocol not in PROTOCOLS:
            return JSONResponse(
                {"error": f"protocol 只能是 {', '.join(PROTOCOLS)}。"}, status_code=400
            )
        try:
            payload = await asyncio.to_thread(service.detail, case_id, protocol)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(payload)

    async def decision(request: Request) -> Response:
        """Record one human decision for a case × protocol."""
        try:
            case_id = int(request.path_params["case_id"])
        except (KeyError, TypeError, ValueError):
            return JSONResponse({"error": "caseId 必须是整数。"}, status_code=400)
        protocol = request.path_params["protocol"]
        if protocol not in PROTOCOLS:
            return JSONResponse(
                {"error": f"protocol 只能是 {', '.join(PROTOCOLS)}。"}, status_code=400
            )
        body = await _json_body(request)
        status = str(body.get("status") or "")
        category = str(body.get("category") or "")
        note = body.get("note")
        try:
            payload = await asyncio.to_thread(
                service.set_decision,
                case_id,
                protocol,
                status=status,
                category=category,
                note=note if isinstance(note, str) else "",
                reviewer=_actor(request),
                actor=_actor(request),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "detail": payload})

    async def fetch_trace(request: Request) -> Response:
        """Fetch one run's trace snapshot from Langfuse (explicit opt-in)."""
        try:
            case_id = int(request.path_params["case_id"])
        except (KeyError, TypeError, ValueError):
            return JSONResponse({"error": "caseId 必须是整数。"}, status_code=400)
        protocol = request.path_params["protocol"]
        if protocol not in PROTOCOLS:
            return JSONResponse(
                {"error": f"protocol 只能是 {', '.join(PROTOCOLS)}。"}, status_code=400
            )
        try:
            payload = await asyncio.to_thread(service.fetch_trace, case_id, protocol)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "trace": payload})

    async def export_all_json(request: Request) -> Response:
        """Return the review payload as JSON."""
        del request
        payload = await asyncio.to_thread(_payload, service)
        return Response(payload_json(payload), media_type="application/json; charset=utf-8")

    async def export_all_yaml(request: Request) -> Response:
        """Return the review payload as YAML."""
        del request
        payload = await asyncio.to_thread(_payload, service)
        return Response(
            payload_yaml(payload),
            media_type="application/yaml; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="osp-review.yaml"'},
        )

    async def report(request: Request) -> Response:
        """Return the acceptance report as Markdown."""
        del request
        payload = await asyncio.to_thread(_payload, service)
        body = report_markdown(payload, window=service.last_scan)
        return Response(
            body,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="osp-review-report.md"'},
        )

    return [
        Route("/review", page),
        Route("/review/app.css", stylesheet),
        Route("/review/main.js", script),
        Route("/api/review/meta", meta),
        Route("/api/review/cases", cases),
        Route("/api/review/batches", batches),
        Route("/api/review/batch/{batch_id}", batch),
        Route("/api/review/scan", scan, methods=["POST"]),
        Route("/api/review/case/{case_id}/{protocol}", detail),
        Route(
            "/api/review/case/{case_id}/{protocol}/decision",
            decision,
            methods=["POST"],
        ),
        Route(
            "/api/review/case/{case_id}/{protocol}/trace",
            fetch_trace,
            methods=["POST"],
        ),
        Route("/api/review/export.json", export_all_json),
        Route("/api/review/export.yaml", export_all_yaml),
        Route("/api/review/report.md", report),
    ]


def _payload(service: ReviewService) -> dict[str, object]:
    """Build the export payload from one consistent projection."""
    rows = service.cases()
    progress = service.progress(rows)
    return build_payload(rows, counts=progress, corpus_version=service.catalog.version)


async def _json_body(request: Request) -> Mapping[str, object]:
    """Return the request body as a mapping."""
    try:
        payload = json.loads((await request.body()) or b"{}")
    except ValueError:
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _actor(request: Request) -> str:
    """Return the operator identity the console records with a decision."""
    return (request.cookies.get(CLIENT_COOKIE) or "").strip()


def _int_param(request: Request, name: str, default: int) -> int:
    """Return one integer query parameter, falling back to the default."""
    try:
        return int(request.query_params.get(name, ""))
    except (TypeError, ValueError):
        return default


def _float_param(request: Request, name: str, default: float) -> float:
    """Return one float query parameter, falling back to the default."""
    try:
        return float(request.query_params.get(name, ""))
    except (TypeError, ValueError):
        return default


__all__ = ["REVIEW_DIR", "build_review_routes"]
