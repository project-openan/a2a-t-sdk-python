"""HTTP routes of the tester console.

The page is separate from the presentation and Run Explorer pages but shares the same process,
run manager and client store. It only adds Excel import, batch execution and result export.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final
from urllib.parse import unquote

from protocol.client_service import RunManager
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from demo.web.tester.excel import parse_test_cases, write_results, write_template
from demo.web.tester.runner import TestBatchManager

#: Directory of the tester page assets.
TESTER_DIR: Final[Path] = Path(__file__).resolve().parent

#: Largest workbook accepted by the import endpoint (10 MB).
MAX_UPLOAD_BYTES: Final[int] = 10 * 1024 * 1024

#: Media type of an ``.xlsx`` workbook.
XLSX_MEDIA_TYPE: Final[str] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def build_tester_routes(
    *,
    runs: RunManager,
    protocols: Sequence[str],
    scenarios_by_protocol: Mapping[str, Sequence[str]],
    protocol_defaults: Mapping[str, Mapping[str, str]],
    default_language: str,
    server_url: str | None,
    server_auth_token: str | None,
    mie_url: str | None,
    runs_dir: Path | None = None,
    nms_enabled: bool = False,
    language: str = "",
    model_mode: str = "",
    model_label: str = "",
    metrics_grace_seconds: float = 0.0,
) -> tuple[list[Route], TestBatchManager]:
    """Build the tester routes and the process-level batch manager."""
    batches = TestBatchManager(
        runs=runs,
        protocol_defaults=protocol_defaults,
        server_url=server_url,
        server_auth_token=server_auth_token,
        mie_url=mie_url,
        runs_dir=runs_dir,
        language=language,
        model_mode=model_mode,
        model_label=model_label,
        metrics_grace_seconds=metrics_grace_seconds,
    )

    async def page(request: Request) -> Response:
        """Serve the tester console shell."""
        del request
        return FileResponse(TESTER_DIR / "index.html")

    async def stylesheet(request: Request) -> Response:
        """Serve the tester stylesheet."""
        del request
        return FileResponse(TESTER_DIR / "app.css", media_type="text/css")

    async def script(request: Request) -> Response:
        """Serve the tester module."""
        del request
        return FileResponse(TESTER_DIR / "main.js", media_type="text/javascript")

    async def meta(request: Request) -> Response:
        """Describe what the page may offer."""
        del request
        return JSONResponse(
            {
                "protocols": list(protocols),
                "scenariosByProtocol": {name: list(names) for name, names in scenarios_by_protocol.items()},
                "defaultLanguage": "zh" if default_language.startswith("zh") else "en",
                "defaultScenario": "night",
                "approvals": ["auto", "approve", "reject"],
                "maxUploadBytes": MAX_UPLOAD_BYTES,
                "nmsEnabled": nms_enabled,
            }
        )

    async def template(request: Request) -> Response:
        """Download the import template."""
        del request
        return Response(
            write_template(),
            media_type=XLSX_MEDIA_TYPE,
            headers={"Content-Disposition": 'attachment; filename="a2a-test-cases.xlsx"'},
        )

    async def import_workbook(request: Request) -> Response:
        """Parse one uploaded workbook and register its cases as a draft batch."""
        data = await request.body()
        if not data:
            return JSONResponse(
                {"error": "请上传 .xlsx 测试用例文件。", "code": "file_empty"},
                status_code=400,
            )
        if len(data) > MAX_UPLOAD_BYTES:
            return JSONResponse(
                {"error": "文件超过 10 MB 上限。", "code": "file_too_large"},
                status_code=413,
            )
        try:
            cases = parse_test_cases(
                data,
                protocols=protocols,
                scenarios_by_protocol=scenarios_by_protocol,
                default_language=default_language,
            )
        except ValueError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "workbook_invalid"},
                status_code=400,
            )
        source_name = unquote(request.headers.get("x-file-name", "")).strip()[:200]
        return JSONResponse(
            batches.create(cases, source_name=source_name),
            status_code=201,
        )

    async def list_batches(request: Request) -> Response:
        """List recent persisted batches (newest first)."""
        try:
            limit = int(request.query_params.get("limit", "20"))
        except ValueError:
            limit = 20
        return JSONResponse({"batches": batches.list(limit=max(1, min(limit, 100)))})

    async def retry_batch(request: Request) -> Response:
        """Create a draft batch from the failed/errored/cancelled cases of one batch."""
        try:
            snapshot = batches.retry(request.path_params["batch_id"])
        except KeyError:
            return JSONResponse(
                {"error": "未知测试批次。", "code": "unknown_batch"},
                status_code=404,
            )
        except ValueError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "batch_not_retryable"},
                status_code=409,
            )
        return JSONResponse(snapshot, status_code=201)

    async def get_batch(request: Request) -> Response:
        """Return the live state of one batch."""
        snapshot = batches.get(request.path_params["batch_id"])
        if snapshot is None:
            return JSONResponse(
                {"error": "未知测试批次。", "code": "unknown_batch"},
                status_code=404,
            )
        return JSONResponse(snapshot)

    async def start_batch(request: Request) -> Response:
        """Start a draft batch."""
        try:
            snapshot = await batches.start(request.path_params["batch_id"])
        except KeyError:
            return JSONResponse(
                {"error": "未知测试批次。", "code": "unknown_batch"},
                status_code=404,
            )
        except ValueError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "batch_not_startable"},
                status_code=409,
            )
        return JSONResponse(snapshot)

    async def cancel_batch(request: Request) -> Response:
        """Cancel a running batch."""
        batch_id = request.path_params["batch_id"]
        if batches.get(batch_id) is None:
            return JSONResponse(
                {"error": "未知测试批次。", "code": "unknown_batch"},
                status_code=404,
            )
        cancelled = await batches.cancel(batch_id)
        if not cancelled:
            return JSONResponse(
                {"error": "该批次不在执行中。", "code": "batch_not_running"},
                status_code=409,
            )
        return JSONResponse({"accepted": True, "batchId": batch_id})

    async def export_results(request: Request) -> Response:
        """Download the current results as an ``.xlsx`` workbook."""
        batch_id = request.path_params["batch_id"]
        snapshot = batches.get(batch_id)
        if snapshot is None:
            return JSONResponse(
                {"error": "未知测试批次。", "code": "unknown_batch"},
                status_code=404,
            )
        return Response(
            write_results(snapshot),
            media_type=XLSX_MEDIA_TYPE,
            headers={"Content-Disposition": (f'attachment; filename="a2a-test-results-{batch_id}.xlsx"')},
        )

    routes = [
        Route("/tester", page),
        Route("/tester/app.css", stylesheet),
        Route("/tester/main.js", script),
        Route("/api/tester/meta", meta),
        Route("/api/tester/template.xlsx", template),
        Route("/api/tester/import", import_workbook, methods=["POST"]),
        Route("/api/tester/batches", list_batches),
        Route("/api/tester/batches/{batch_id}", get_batch),
        Route("/api/tester/batches/{batch_id}/start", start_batch, methods=["POST"]),
        Route("/api/tester/batches/{batch_id}/cancel", cancel_batch, methods=["POST"]),
        Route("/api/tester/batches/{batch_id}/retry", retry_batch, methods=["POST"]),
        Route("/api/tester/batches/{batch_id}/export.xlsx", export_results),
    ]
    return routes, batches


__all__ = ["MAX_UPLOAD_BYTES", "TESTER_DIR", "build_tester_routes"]
