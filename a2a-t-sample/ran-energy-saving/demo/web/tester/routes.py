"""HTTP routes of the redesigned tester console.

The console is now catalog-driven: it lists the OSP YAML cases, starts one batch item per
``(case, protocol)`` and runs the requested stage chain. Excel is an export format only.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from eval.osp.catalog import PROTOCOL_A2A, PROTOCOL_A2A_T, CaseCatalog
from protocol.client_service import RunManager
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from demo.web.tester.report import write_report
from demo.web.tester.service import (
    ALL_STAGES,
    DECISION_MODE_SCRIPTED,
    DECISION_MODES,
    DEFAULT_WORKING_TIMEOUT_SECONDS,
    EvalBatchManager,
)

#: Directory of the tester page assets.
TESTER_DIR: Final[Path] = Path(__file__).resolve().parent

#: Media type of an ``.xlsx`` workbook.
XLSX_MEDIA_TYPE: Final[str] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Anonymous cookie that keeps each browser's batches apart; set when the page is served.
CLIENT_COOKIE: Final[str] = "a2at_tester_client"

#: Lifetime of the browser identity cookie, in seconds.
CLIENT_COOKIE_MAX_AGE: Final[int] = 365 * 24 * 60 * 60


def build_tester_routes(
    *,
    runs: RunManager,
    protocols: Sequence[str],
    protocol_defaults: Mapping[str, Mapping[str, str]],
    server_url: str | None,
    server_auth_token: str | None,
    runs_dir: Path | None = None,
    nms_enabled: bool = False,
    nms_sim: bool = False,
    language: str = "",
    model_mode: str = "",
    model_label: str = "",
    metrics_grace_seconds: float = 0.0,
    catalog: CaseCatalog | None = None,
    prompt_client: Any | None = None,
    scenarios_by_protocol: Mapping[str, Sequence[str]] | None = None,
    default_language: str = "",
) -> tuple[list[Route], EvalBatchManager]:
    """Build the tester routes and the process-level batch manager."""
    del scenarios_by_protocol, default_language
    resolved_catalog = catalog or CaseCatalog()
    batches = EvalBatchManager(
        runs=runs,
        catalog=resolved_catalog,
        protocol_defaults=protocol_defaults,
        server_url=server_url,
        server_auth_token=server_auth_token,
        runs_dir=runs_dir,
        nms_sim=nms_sim,
        language=language,
        model_mode=model_mode,
        model_label=model_label,
        metrics_grace_seconds=metrics_grace_seconds,
        prompt_client=prompt_client,
    )

    async def page(request: Request) -> Response:
        """Serve the tester console shell and remember this browser's anonymous identity."""
        response = FileResponse(TESTER_DIR / "index.html")
        if not _client_id(request):
            response.set_cookie(
                CLIENT_COOKIE,
                uuid.uuid4().hex,
                max_age=CLIENT_COOKIE_MAX_AGE,
                path="/",
                httponly=True,
                samesite="lax",
            )
        return response

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
        defaults = protocol_defaults.get(PROTOCOL_A2A_T, {})
        return JSONResponse(
            {
                "protocols": [PROTOCOL_A2A_T, PROTOCOL_A2A],
                "stopAfter": list(ALL_STAGES),
                "stageOrder": list(ALL_STAGES),
                "approvals": ["approve", "reject", "auto"],
                "decisionModes": list(DECISION_MODES),
                "transport": defaults.get("transport", "sse"),
                "discovery": defaults.get("discovery", "direct"),
                "defaults": {
                    "stopAfter": "working",
                    "approval": None,
                    "timeoutSeconds": None,
                    "workingTimeoutSeconds": DEFAULT_WORKING_TIMEOUT_SECONDS,
                    "decisionMode": DECISION_MODE_SCRIPTED,
                },
                "runnerDefaultsIndicateCase": True,
                "catalogCount": resolved_catalog.count,
                "modelMode": model_mode,
                "nmsEnabled": nms_enabled,
                "nmsSim": nms_sim,
                "advertisedProtocols": list(protocols),
            }
        )

    async def catalog_rows(request: Request) -> Response:
        """Return the whole case catalog; the page filters it client-side."""
        del request
        return JSONResponse({"cases": batches.catalog(), "count": resolved_catalog.count})

    async def catalog_detail(request: Request) -> Response:
        """Return one case's summary and its full YAML document (read-only)."""
        try:
            case_id = int(request.path_params["case_id"])
        except ValueError:
            return JSONResponse({"error": "未知用例。", "code": "unknown_case"}, status_code=404)
        case = resolved_catalog.get(case_id)
        summary = next(
            (row for row in resolved_catalog.summaries() if row.id == case_id), None
        )
        if case is None or summary is None:
            return JSONResponse({"error": "未知用例。", "code": "unknown_case"}, status_code=404)
        document = {key: value for key, value in case.items() if key != "_path"}
        return JSONResponse({"summary": summary.to_dict(), "case": document})

    async def create_batch(request: Request) -> Response:
        """Create a draft batch from selected cases and protocols."""
        try:
            body = await _json_body(request)
            snapshot = batches.create(
                case_ids=_int_list(body.get("caseIds"), "caseIds"),
                protocols=_str_list(body.get("protocols"), "protocols"),
                stop_after=str(body.get("stopAfter") or "final"),
                approval=_optional_str(body.get("approval")),
                timeout_seconds=_optional_float(body.get("timeoutSeconds")),
                working_timeout_seconds=_optional_float(body.get("workingTimeoutSeconds")),
                decision_mode=str(body.get("decisionMode") or DECISION_MODE_SCRIPTED),
                owner=_client_id(request),
            )
        except (TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc), "code": "invalid_request"}, status_code=400)
        return JSONResponse(snapshot, status_code=201)

    async def create_scratch(request: Request) -> Response:
        """Create a one-item ad-hoc batch."""
        try:
            body = await _json_body(request)
            snapshot = batches.create_scratch(
                user_input=str(body.get("userInput") or ""),
                protocol=str(body.get("protocol") or PROTOCOL_A2A_T),
                approval=_optional_str(body.get("approval")) or "approve",
                timeout_seconds=_optional_float(body.get("timeoutSeconds")),
                decision_mode=str(body.get("decisionMode") or DECISION_MODE_SCRIPTED),
                owner=_client_id(request),
            )
        except (TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc), "code": "invalid_request"}, status_code=400)
        return JSONResponse(snapshot, status_code=201)

    async def list_batches(request: Request) -> Response:
        """List recent persisted batches (newest first)."""
        try:
            limit = int(request.query_params.get("limit", "20"))
        except ValueError:
            limit = 20
        return JSONResponse(
            {
                "batches": batches.list_batches(
                    owner=_client_id(request), limit=max(1, min(limit, 100))
                )
            }
        )

    async def get_batch(request: Request) -> Response:
        """Return the live state of one batch."""
        snapshot = batches.get(
            request.path_params["batch_id"], owner=_client_id(request)
        )
        if snapshot is None:
            return JSONResponse({"error": "未知测试批次。", "code": "unknown_batch"}, status_code=404)
        return JSONResponse(snapshot)

    async def start_batch(request: Request) -> Response:
        """Start a draft batch."""
        try:
            snapshot = await batches.start(
                request.path_params["batch_id"], owner=_client_id(request)
            )
        except KeyError:
            return JSONResponse({"error": "未知测试批次。", "code": "unknown_batch"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "code": "batch_not_startable"}, status_code=409)
        return JSONResponse(snapshot)

    async def cancel_batch(request: Request) -> Response:
        """Cancel a running batch."""
        batch_id = request.path_params["batch_id"]
        owner = _client_id(request)
        if batches.get(batch_id, owner=owner) is None:
            return JSONResponse({"error": "未知测试批次。", "code": "unknown_batch"}, status_code=404)
        cancelled = await batches.cancel(batch_id, owner=owner)
        if not cancelled:
            return JSONResponse(
                {"error": "该批次不在执行中。", "code": "batch_not_running"}, status_code=409
            )
        return JSONResponse({"accepted": True, "batchId": batch_id})

    async def retry_batch(request: Request) -> Response:
        """Create a draft batch from the failed/errored/cancelled items."""
        try:
            snapshot = batches.retry(
                request.path_params["batch_id"], owner=_client_id(request)
            )
        except KeyError:
            return JSONResponse({"error": "未知测试批次。", "code": "unknown_batch"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "code": "batch_not_retryable"}, status_code=409)
        return JSONResponse(snapshot, status_code=201)

    async def rerun_item(request: Request) -> Response:
        """Rerun one item of a finished batch in place."""
        try:
            index = int(request.path_params["index"])
        except ValueError:
            return JSONResponse({"error": "用例下标无效。", "code": "invalid_index"}, status_code=409)
        try:
            snapshot = await batches.rerun_item(
                request.path_params["batch_id"], index, owner=_client_id(request)
            )
        except KeyError:
            return JSONResponse({"error": "未知测试批次。", "code": "unknown_batch"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "code": "batch_not_rerunnable"}, status_code=409)
        return JSONResponse(snapshot, status_code=202)

    async def export_json(request: Request) -> Response:
        """Download the batch as JSON."""
        batch_id = request.path_params["batch_id"]
        snapshot = batches.get(batch_id, owner=_client_id(request))
        if snapshot is None:
            return JSONResponse({"error": "未知测试批次。", "code": "unknown_batch"}, status_code=404)
        return Response(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="osp-tester-{batch_id}.json"'},
        )

    async def export_xlsx(request: Request) -> Response:
        """Download the batch as an XLSX report."""
        batch_id = request.path_params["batch_id"]
        snapshot = batches.get(batch_id, owner=_client_id(request))
        if snapshot is None:
            return JSONResponse({"error": "未知测试批次。", "code": "unknown_batch"}, status_code=404)
        return Response(
            write_report(snapshot),
            media_type=XLSX_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="osp-tester-{batch_id}.xlsx"'},
        )

    routes = [
        Route("/tester", page),
        Route("/tester/app.css", stylesheet),
        Route("/tester/main.js", script),
        Route("/api/tester/meta", meta),
        Route("/api/tester/catalog", catalog_rows),
        Route("/api/tester/catalog/{case_id}", catalog_detail),
        Route("/api/tester/batches", list_batches),
        Route("/api/tester/batches", create_batch, methods=["POST"]),
        Route("/api/tester/scratch", create_scratch, methods=["POST"]),
        Route("/api/tester/batches/{batch_id}", get_batch),
        Route("/api/tester/batches/{batch_id}/start", start_batch, methods=["POST"]),
        Route("/api/tester/batches/{batch_id}/cancel", cancel_batch, methods=["POST"]),
        Route("/api/tester/batches/{batch_id}/retry", retry_batch, methods=["POST"]),
        Route(
            "/api/tester/batches/{batch_id}/items/{index}/rerun",
            rerun_item,
            methods=["POST"],
        ),
        Route("/api/tester/batches/{batch_id}/export.json", export_json),
        Route("/api/tester/batches/{batch_id}/export.xlsx", export_xlsx),
    ]
    return routes, batches


def _client_id(request: Request) -> str:
    """Return the anonymous browser identity carried by the tester cookie."""
    return (request.cookies.get(CLIENT_COOKIE) or "").strip()


async def _json_body(request: Request) -> Mapping[str, Any]:
    try:
        payload = json.loads((await request.body()) or b"{}")
    except ValueError as exc:
        raise ValueError("请求体不是合法 JSON。") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("请求体必须是 JSON 对象。")
    return payload


def _int_list(value: object, name: str) -> list[int]:
    if not isinstance(value, list):
        raise ValueError(f"{name} 必须是数组。")
    result: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(f"{name} 只能包含整数。")
        result.append(item)
    if not result:
        raise ValueError(f"{name} 不能为空。")
    return result


def _optional_str(value: object) -> str | None:
    """Return a non-empty string value or ``None`` (``None`` means per-case)."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _optional_float(value: object) -> float | None:
    """Return a numeric value or ``None`` (``None`` means per-case)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("数值参数格式不正确。")
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError("数值参数格式不正确。") from exc


def _str_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{name} 必须是数组。")
    result = [str(item) for item in value if isinstance(item, str)]
    if not result:
        raise ValueError(f"{name} 不能为空。")
    return result


__all__ = ["TESTER_DIR", "build_tester_routes"]
