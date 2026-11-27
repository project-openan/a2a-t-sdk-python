"""HTTP routes of the redesigned tester console.

The console is now catalog-driven: it lists the OSP YAML cases, starts one batch item per
``(case, protocol)`` and runs the requested stage chain. Excel is an export format only.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any, Final

from eval.osp.catalog import PROTOCOL_A2A, PROTOCOL_A2A_T, CaseCatalog, CatalogReloadError
from langfuse_export.config import Config as LangfuseConfig
from protocol.client_service import RunManager
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from demo.web.request_source import request_source
from demo.web.tester.langfuse_columns import TRACE_COLUMN_HEADERS, build_trace_columns
from demo.web.tester.report import write_report
from demo.web.tester.service import (
    ALL_STAGES,
    DECISION_MODES,
    DEFAULT_CONCURRENCY,
    DEFAULT_ROUNDS,
    DEFAULT_WORKING_TIMEOUT_SECONDS,
    MAX_CONCURRENCY,
    MAX_ROUNDS,
    NEGOTIATION_SOURCES,
    EvalBatchManager,
    default_decision_mode,
    default_negotiation_source,
    validate_concurrency,
    validate_rounds,
)

#: Directory of the tester page assets.
TESTER_DIR: Final[Path] = Path(__file__).resolve().parent

#: Media type of an ``.xlsx`` workbook.
XLSX_MEDIA_TYPE: Final[str] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Anonymous cookie that keeps each browser's batches apart; set when the page is served.
CLIENT_COOKIE: Final[str] = "a2at_tester_client"

#: Lifetime of the browser identity cookie, in seconds.
CLIENT_COOKIE_MAX_AGE: Final[int] = 365 * 24 * 60 * 60

#: Concurrent Langfuse trace exports of one report. Langfuse admits ~30 requests/minute and one
#: trace is 4-6 HTTP calls, so two workers keep a batch under the limit; runs finished before the
#: export are already served by their per-run cache.
TRACE_FETCH_WORKERS: Final[int] = 2
#: Per-trace budget of ``export_full`` (fetch observations + derive metrics + write the export
#: dir). This is not the whole-batch budget; a slow batch may take several rounds of it.
TRACE_FETCH_TIMEOUT_SECONDS: Final[float] = 30.0


def build_tester_routes(
    *,
    runs: RunManager,
    protocols: Sequence[str],
    protocol_defaults: Mapping[str, Mapping[str, str]],
    server_url: str | None,
    server_auth_token: str | None,
    runs_dir: Path | None = None,
    nms_enabled: bool = False,
    language: str = "",
    model_mode: str = "",
    model_label: str = "",
    metrics_grace_seconds: float = 0.0,
    catalog: CaseCatalog | None = None,
    prompt_client: Any | None = None,
    env_path: Path | None = None,
    scenarios_by_protocol: Mapping[str, Sequence[str]] | None = None,
    default_language: str = "",
    judge: Any | None = None,
    judge_model_label: str = "",
    pure_hop: str = "",
    trace_config: LangfuseConfig | None = None,
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
        nms=nms_enabled,
        language=language,
        model_mode=model_mode,
        model_label=model_label,
        metrics_grace_seconds=metrics_grace_seconds,
        prompt_client=prompt_client,
        env_path=env_path,
        judge=judge,
        judge_model_label=judge_model_label,
        pure_hop=pure_hop,
        trace_config=trace_config,
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
                "decisionModes": list(DECISION_MODES),
                "negotiationSources": list(NEGOTIATION_SOURCES),
                "transport": defaults.get("transport", "sse"),
                "discovery": defaults.get("discovery", "direct"),
                "defaults": {
                    "stopAfter": "accepted",
                    "timeoutSeconds": None,
                    "workingTimeoutSeconds": DEFAULT_WORKING_TIMEOUT_SECONDS,
                    "decisionMode": default_decision_mode(model_mode),
                    "negotiationSource": default_negotiation_source(model_mode),
                    "concurrency": DEFAULT_CONCURRENCY,
                    "rounds": DEFAULT_ROUNDS,
                },
                "maxConcurrency": MAX_CONCURRENCY,
                "maxRounds": MAX_ROUNDS,
                "runnerDefaultsIndicateCase": True,
                "catalogCount": resolved_catalog.count,
                "modelMode": model_mode,
                "nmsEnabled": nms_enabled,
                "advertisedProtocols": list(protocols),
            }
        )

    async def catalog_rows(request: Request) -> Response:
        """Return the whole case catalog; the page filters it client-side."""
        del request
        return JSONResponse({"cases": batches.catalog(), "count": resolved_catalog.count})

    async def catalog_reload(request: Request) -> Response:
        """Re-read the case YAML directory without restarting the UI process.

        A malformed file rejects the whole reload: the catalog keeps serving the previous
        snapshot, so one broken edit cannot empty the console mid-acceptance.
        """
        del request
        try:
            outcome = await asyncio.to_thread(resolved_catalog.reload)
        except CatalogReloadError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "catalog_reload_failed"}, status_code=422
            )
        return JSONResponse(outcome.to_dict())

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
                # A test stops once the Task is accepted: the vendor pushes no final state (or
                # pushes it minutes later), so waiting for it only turns a completed negotiation
                # into a timeout. A caller that wants the final state asks for it explicitly.
                stop_after=str(body.get("stopAfter") or "accepted"),
                timeout_seconds=_optional_float(body.get("timeoutSeconds")),
                working_timeout_seconds=_optional_float(body.get("workingTimeoutSeconds")),
                decision_mode=_optional_str(body.get("decisionMode")),
                negotiation_source=_optional_str(body.get("negotiationSource")),
                principles_version=_optional_str(body.get("principlesVersion")),
                nms_version=_optional_str(body.get("nmsVersion")),
                keep_tasks=bool(body.get("keepTasks")),
                concurrency=_concurrency(body.get("concurrency")),
                rounds=_rounds(body.get("rounds")),
                owner=_client_id(request),
                owner_source=request_source(request, "tester"),
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
                timeout_seconds=_optional_float(body.get("timeoutSeconds")),
                decision_mode=_optional_str(body.get("decisionMode")),
                negotiation_source=_optional_str(body.get("negotiationSource")),
                keep_tasks=bool(body.get("keepTasks")),
                owner=_client_id(request),
                owner_source=request_source(request, "tester"),
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
            snapshot = await batches.retry(
                request.path_params["batch_id"], owner=_client_id(request)
            )
        except KeyError:
            return JSONResponse({"error": "未知测试批次。", "code": "unknown_batch"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "code": "batch_not_retryable"}, status_code=409)
        return JSONResponse(snapshot, status_code=201)

    async def cancel_item(request: Request) -> Response:
        """Cancel one item of a running batch, leaving the other items running."""
        try:
            index = int(request.path_params["index"])
        except ValueError:
            return JSONResponse({"error": "用例下标无效。", "code": "invalid_index"}, status_code=404)
        try:
            payload = await batches.cancel_item(
                request.path_params["batch_id"], index, owner=_client_id(request)
            )
        except KeyError:
            return JSONResponse({"error": "未知测试批次。", "code": "unknown_batch"}, status_code=404)
        except IndexError:
            return JSONResponse({"error": "用例下标无效。", "code": "invalid_index"}, status_code=404)
        except ValueError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "item_not_cancellable"}, status_code=409
            )
        return JSONResponse(payload)

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
        """Download the batch as an XLSX report.

        ``langfuseColumns`` is ``auto`` (fetch when a project is configured), ``0`` (never fetch)
        or ``1`` (fetch and report per-run failures); any other value is a 400, never a silent
        fallback.
        """
        batch_id = request.path_params["batch_id"]
        snapshot = batches.get(batch_id, owner=_client_id(request))
        if snapshot is None:
            return JSONResponse({"error": "未知测试批次。", "code": "unknown_batch"}, status_code=404)
        langfuse_columns = request.query_params.get("langfuseColumns", "auto")
        if langfuse_columns not in {"auto", "0", "1"}:
            return JSONResponse(
                {
                    "error": "langfuseColumns 只接受 auto、0 或 1。",
                    "code": "invalid_langfuse_columns",
                },
                status_code=400,
            )
        trace_columns = None
        if langfuse_columns == "1" or (langfuse_columns == "auto" and trace_config is not None):
            trace_columns = await asyncio.to_thread(_trace_columns, snapshot, trace_config, runs_dir)
        content = await asyncio.to_thread(
            write_report,
            snapshot,
            runs_dir=runs_dir,
            trace_columns=trace_columns,
        )
        return Response(
            content,
            media_type=XLSX_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="osp-tester-{batch_id}.xlsx"'},
        )

    routes = [
        Route("/tester", page),
        Route("/tester/app.css", stylesheet),
        Route("/tester/main.js", script),
        Route("/api/tester/meta", meta),
        Route("/api/tester/catalog", catalog_rows),
        # Must precede the ``{case_id}`` route: Starlette matches in declaration order and would
        # otherwise read "reload" as a case id.
        Route("/api/tester/catalog/reload", catalog_reload, methods=["POST"]),
        Route("/api/tester/catalog/{case_id}", catalog_detail),
        Route("/api/tester/batches", list_batches),
        Route("/api/tester/batches", create_batch, methods=["POST"]),
        Route("/api/tester/scratch", create_scratch, methods=["POST"]),
        Route("/api/tester/batches/{batch_id}", get_batch),
        Route("/api/tester/batches/{batch_id}/start", start_batch, methods=["POST"]),
        Route("/api/tester/batches/{batch_id}/cancel", cancel_batch, methods=["POST"]),
        Route("/api/tester/batches/{batch_id}/retry", retry_batch, methods=["POST"]),
        Route(
            "/api/tester/batches/{batch_id}/items/{index}/cancel",
            cancel_item,
            methods=["POST"],
        ),
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


def _concurrency(value: object) -> int:
    """Return the requested item concurrency; an absent value keeps the default."""
    if value is None or value == "":
        return DEFAULT_CONCURRENCY
    return validate_concurrency(value)


def _rounds(value: object) -> int:
    """Return the requested batch rounds; an absent value keeps the default."""
    if value is None or value == "":
        return DEFAULT_ROUNDS
    return validate_rounds(value)


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


def _trace_columns(
    snapshot: Mapping[str, Any],
    config: LangfuseConfig | None,
    runs_dir: Path | None,
) -> dict[str, tuple[list[object], str]]:
    """Fetch the Langfuse cells of every distinct run in one batch snapshot.

    Runs are de-duplicated by ``runId`` (a multi-round batch repeats one run on several rows).
    Every per-trace failure stays on that row; a failed fetch never aborts the workbook.
    """
    targets: dict[str, str] = {}
    items = snapshot.get("items")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        run_id = str(item.get("runId") or "")
        trace_id = str(item.get("langfuseTraceId") or "")
        if run_id and run_id not in targets:
            targets[run_id] = trace_id
    if not targets:
        return {}
    if runs_dir is None:
        empty = [""] * len(TRACE_COLUMN_HEADERS)
        note = "Langfuse 指标未取到：导出时未提供 run 目录"
        return {run_id: (list(empty), note) for run_id in targets}
    with ThreadPoolExecutor(max_workers=min(TRACE_FETCH_WORKERS, len(targets))) as pool:
        futures = {
            pool.submit(
                _trace_columns_with_timeout,
                config,
                run_id,
                trace_id,
                runs_dir / run_id / "langfuse-export",
            ): run_id
            for run_id, trace_id in targets.items()
        }
        collected: dict[str, tuple[list[object], str]] = {}
        for future, run_id in futures.items():
            try:
                collected[run_id] = future.result()
            except Exception as exc:  # noqa: BLE001 - one bad trace must not fail the report
                collected[run_id] = (
                    [""] * len(TRACE_COLUMN_HEADERS),
                    f"Langfuse 指标未取到：{type(exc).__name__}: {exc}",
                )
        return collected


def _trace_columns_with_timeout(
    config: LangfuseConfig | None,
    run_id: str,
    trace_id: str,
    out_root: Path,
) -> tuple[list[object], str]:
    """Run one export under the per-trace budget, leaving a note instead of raising."""
    empty: list[object] = [""] * len(TRACE_COLUMN_HEADERS)
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(
        build_trace_columns,
        config,
        run_id=run_id,
        trace_id=trace_id,
        out_root=out_root,
    )
    try:
        return future.result(timeout=TRACE_FETCH_TIMEOUT_SECONDS)
    except FuturesTimeoutError:
        future.cancel()
        return empty, f"Langfuse 指标未取到：单条 trace {TRACE_FETCH_TIMEOUT_SECONDS:g} 秒内未完成"
    except Exception as exc:  # noqa: BLE001 - one bad trace must not fail the report
        return empty, f"Langfuse 指标未取到：{type(exc).__name__}: {exc}"
    finally:
        # The export may still be blocked on a socket; do not hold the batch for it.
        pool.shutdown(wait=False, cancel_futures=True)


__all__ = ["TESTER_DIR", "build_tester_routes"]
