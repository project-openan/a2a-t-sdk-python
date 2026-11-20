"""HTTP routes of the expectation annotation console.

The console is a separate page on purpose. ``/tester`` shows the batch a browser started and is
scoped to that browser's cookie; annotation works across every batch of a window, whichever
operator ran it, and its unit of work is one case expectation rather than one execution. The two
pages share the catalog and the run store, not their request model.

The page never writes the corpus: :mod:`demo.web.annotate.export` renders the confirmed values for
an operator to merge, because the yellow zone hot-replaces the case files.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from eval.osp.catalog import CaseCatalog
from eval.osp.expectations import SLOT_DIMENSION, SLOT_MODES
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from demo.web.annotate.export import case_snippet, payload_json, payload_yaml
from demo.web.annotate.service import PROTOCOLS, AnnotateService
from demo.web.annotate.store import DIMENSIONS, TARGETS, AnnotationStore, TargetKey

#: Directory of the console page assets.
ANNOTATE_DIR: Final[Path] = Path(__file__).resolve().parent

#: Browser identity cookie shared with the tester console (same operator, same anonymous id).
CLIENT_COOKIE: Final[str] = "a2at_tester_client"

#: Default scan window; the operator may widen it.
DEFAULT_DAYS: Final[float] = 1.0

#: Ceiling of the scan window and of the batches one scan reads.
MAX_DAYS: Final[float] = 30.0
MAX_BATCHES: Final[int] = 500

#: Ceiling of one bulk adoption: enough for a whole week of batches, small enough to bound a typo.
MAX_TRACES: Final[int] = 2000
MAX_TRACE_LENGTH: Final[int] = 128

#: A trace list may arrive as free text; any run of separators splits it.
_TRACE_SPLIT: Final[re.Pattern[str]] = re.compile(r"[\s,，;；]+")


def build_annotate_routes(
    *,
    catalog: CaseCatalog,
    runs_dir: Path,
    client_db_path: Path | None,
    store_path: Path | None = None,
) -> list[Route]:
    """Build the annotation console routes."""
    resolved_store = AnnotationStore(
        store_path
        if store_path is not None
        else Path(runs_dir) / "annotations" / "annotations.sqlite3"
    )
    resolved_store.ensure()
    service = AnnotateService(
        store=resolved_store,
        catalog=catalog,
        runs_dir=Path(runs_dir),
        client_db_path=client_db_path,
    )

    async def page(request: Request) -> Response:
        """Serve the console shell."""
        del request
        return FileResponse(ANNOTATE_DIR / "index.html", headers={"Cache-Control": "no-store"})

    async def stylesheet(request: Request) -> Response:
        """Serve the console stylesheet."""
        del request
        return FileResponse(
            ANNOTATE_DIR / "app.css",
            media_type="text/css",
            headers={"Cache-Control": "no-store"},
        )

    async def script(request: Request) -> Response:
        """Serve the console module."""
        del request
        return FileResponse(
            ANNOTATE_DIR / "main.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    async def meta(request: Request) -> Response:
        """Describe what the console may offer."""
        del request
        return JSONResponse(
            {
                "protocols": list(PROTOCOLS),
                "dimensions": list(DIMENSIONS),
                "statuses": list(TARGETS),
                "slotModes": list(SLOT_MODES),
                "runsDir": str(runs_dir),
                "clientDb": str(client_db_path) if client_db_path is not None else None,
                "store": str(resolved_store.path),
                "defaultDays": DEFAULT_DAYS,
                "maxDays": MAX_DAYS,
                "maxBatches": MAX_BATCHES,
            }
        )

    async def cases(request: Request) -> Response:
        """Return every case with its protocol tasks and subtasks."""
        rows = await asyncio.to_thread(service.cases)
        progress = await asyncio.to_thread(service.progress, rows)
        return JSONResponse({"cases": rows, **progress})

    async def scan(request: Request) -> Response:
        """Collect the candidates of the recent tester batches."""
        days = min(MAX_DAYS, max(0.05, _float_param(request, "days", DEFAULT_DAYS)))
        limit = max(1, min(MAX_BATCHES, _int_param(request, "maxBatches", MAX_BATCHES)))
        summary = await asyncio.to_thread(service.scan, days=days, max_batches=limit)
        progress = await asyncio.to_thread(service.progress)
        return JSONResponse({"scan": summary.to_dict(), **progress})

    async def adopt_traces(request: Request) -> Response:
        """Adopt the observations of the supplied traceIds/runIds as annotation values."""
        body = await _json_body(request)
        raw_traces = body.get("traces")
        if raw_traces is None:
            raw_traces = body.get("traceIds")
        tokens = _trace_tokens(raw_traces)
        if not tokens:
            return JSONResponse(
                {"error": "请提供至少一个 traceId 或 runId。"}, status_code=400
            )
        if len(tokens) > MAX_TRACES:
            return JSONResponse(
                {"error": f"一次最多 {MAX_TRACES} 个 traceId/runId。"}, status_code=400
            )
        protocol_ok, protocol = _optional_choice(body.get("protocol"), PROTOCOLS)
        if not protocol_ok:
            return JSONResponse(
                {"error": f"protocol 只能是 {', '.join(PROTOCOLS)}。"}, status_code=400
            )
        dimension_ok, dimension = _optional_choice(body.get("dimension"), DIMENSIONS)
        if not dimension_ok:
            return JSONResponse(
                {"error": f"dimension 只能是 {', '.join(DIMENSIONS)}。"}, status_code=400
            )
        result = await asyncio.to_thread(
            service.adopt_traces,
            tokens,
            protocol=protocol,
            dimension=dimension,
            confirm=_flag(body.get("confirm"), True),
            replace=_flag(body.get("replace"), False),
            actor=_actor(request),
            max_batches=MAX_BATCHES,
        )
        return JSONResponse(result)

    async def subtask_detail(request: Request) -> Response:
        """Return the candidates and the choices of one subtask."""
        key = _target_key(request)
        if key is None:
            return JSONResponse({"error": "未知的标注子任务。"}, status_code=404)
        detail = await asyncio.to_thread(
            service.detail, key.case_id, key.protocol, key.dimension
        )
        return JSONResponse(detail)

    async def choice(request: Request) -> Response:
        """Accept, edit or reject one candidate."""
        key = _target_key(request)
        if key is None:
            return JSONResponse({"error": "未知的标注子任务。"}, status_code=404)
        body = await _json_body(request)
        observation_id = body.get("observationId")
        if not isinstance(observation_id, int) or isinstance(observation_id, bool):
            return JSONResponse({"error": "observationId 必须是整数。"}, status_code=400)
        edited = body.get("editedText")
        await asyncio.to_thread(
            resolved_store.set_choice,
            key,
            observation_id,
            accepted=bool(body.get("accepted")),
            edited_text=edited if isinstance(edited, str) else None,
            actor=_actor(request),
        )
        await asyncio.to_thread(service.reload_catalog)
        return JSONResponse({"ok": True})

    async def apply_case(request: Request) -> Response:
        """Save every subtask of one case+protocol in one request, then confirm the selection."""
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
        raw_targets = body.get("targets")
        if not isinstance(raw_targets, list) or not raw_targets:
            return JSONResponse({"error": "请提供要保存的子任务。"}, status_code=400)
        try:
            details = await asyncio.to_thread(
                service.apply_targets,
                case_id,
                protocol,
                [target for target in raw_targets if isinstance(target, Mapping)],
                confirm=_flag(body.get("confirm"), True),
                actor=_actor(request),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "details": details})

    async def mode(request: Request) -> Response:
        """Set the matching mode of a slot-error subtask."""
        key = _target_key(request)
        if key is None or key.dimension != SLOT_DIMENSION:
            return JSONResponse({"error": "未知的 slot_errors 子任务。"}, status_code=404)
        body = await _json_body(request)
        value = str(body.get("mode") or "")
        if value not in SLOT_MODES:
            return JSONResponse({"error": f"mode 只能是 {SLOT_MODES}。"}, status_code=400)
        await asyncio.to_thread(resolved_store.set_mode, key, value, actor=_actor(request))
        await asyncio.to_thread(service.reload_catalog)
        return JSONResponse({"ok": True})

    async def note(request: Request) -> Response:
        """Record a free-form note on one subtask."""
        key = _target_key(request)
        if key is None:
            return JSONResponse({"error": "未知的标注子任务。"}, status_code=404)
        body = await _json_body(request)
        await asyncio.to_thread(
            resolved_store.set_note, key, str(body.get("note") or ""), actor=_actor(request)
        )
        await asyncio.to_thread(service.reload_catalog)
        return JSONResponse({"ok": True})

    async def confirm(request: Request) -> Response:
        """Confirm the accepted choices of one subtask."""
        key = _target_key(request)
        if key is None:
            return JSONResponse({"error": "未知的标注子任务。"}, status_code=404)
        result = await asyncio.to_thread(resolved_store.confirm, key, actor=_actor(request))
        await asyncio.to_thread(service.reload_catalog)
        return JSONResponse({"ok": True, **result})

    async def export_all_yaml(request: Request) -> Response:
        """Return the case-level annotation data as YAML (portable across environments)."""
        del request
        payload = await asyncio.to_thread(resolved_store.export_payload)
        return Response(
            payload_yaml(payload),
            media_type="application/yaml; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="osp-annotations.yaml"'},
        )

    async def export_all_json(request: Request) -> Response:
        """Return the same payload as JSON for a diff/merge workflow."""
        del request
        payload = await asyncio.to_thread(resolved_store.export_payload)
        return Response(payload_json(payload), media_type="application/json; charset=utf-8")

    async def export_one_case(request: Request) -> Response:
        """Return one case's confirmed expectations as a paste-ready YAML fragment."""
        try:
            case_id = int(request.path_params["case_id"])
        except (KeyError, TypeError, ValueError):
            return JSONResponse({"error": "caseId 必须是整数。"}, status_code=400)
        case = catalog.get(case_id)
        if case is None:
            return JSONResponse({"error": "未知用例。"}, status_code=404)
        payload = await asyncio.to_thread(resolved_store.export_payload)
        body = case_snippet(payload, case_id, case_name=str(case.get("name") or ""))
        return Response(
            body,
            media_type="application/yaml; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="case-{case_id}-expectations.yaml"'
            },
        )

    return [
        Route("/annotate", page),
        Route("/annotate/app.css", stylesheet),
        Route("/annotate/main.js", script),
        Route("/api/annotate/meta", meta),
        Route("/api/annotate/cases", cases),
        Route("/api/annotate/scan", scan, methods=["POST"]),
        Route("/api/annotate/adopt/traces", adopt_traces, methods=["POST"]),
        # ``apply`` must be registered before the ``{dimension}`` route, or Starlette would match
        # the literal path as a dimension and answer 404.
        Route(
            "/api/annotate/case/{case_id}/{protocol}/apply",
            apply_case,
            methods=["POST"],
        ),
        Route("/api/annotate/case/{case_id}/{protocol}/{dimension}", subtask_detail),
        Route(
            "/api/annotate/case/{case_id}/{protocol}/{dimension}/choice",
            choice,
            methods=["POST"],
        ),
        Route(
            "/api/annotate/case/{case_id}/{protocol}/{dimension}/mode",
            mode,
            methods=["POST"],
        ),
        Route(
            "/api/annotate/case/{case_id}/{protocol}/{dimension}/note",
            note,
            methods=["POST"],
        ),
        Route(
            "/api/annotate/case/{case_id}/{protocol}/{dimension}/confirm",
            confirm,
            methods=["POST"],
        ),
        Route("/api/annotate/export.yaml", export_all_yaml),
        Route("/api/annotate/export.json", export_all_json),
        Route("/api/annotate/export/case/{case_id}", export_one_case),
    ]


def _trace_tokens(raw: object) -> list[str]:
    """Return the normalized identifiers of a trace-list request body.

    The field accepts a list of strings or one block of free text: an operator pasting a column
    from the run console should not have to reformat it, and every separator people actually use
    (newline, comma, ideographic comma, semicolon) splits the same way.
    """
    if isinstance(raw, str):
        parts = _TRACE_SPLIT.split(raw)
    elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        parts = [str(item) for item in raw]
    else:
        return []
    tokens: list[str] = []
    seen: set[str] = set()
    for part in parts:
        token = part.strip().lower()
        if not token or len(token) > MAX_TRACE_LENGTH or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _optional_choice(raw: object, allowed: Sequence[str]) -> tuple[bool, str | None]:
    """Return ``(valid, value)`` for an optional enum field of a request body."""
    if raw is None or raw == "":
        return True, None
    text = str(raw)
    if text not in allowed:
        return False, None
    return True, text


def _flag(raw: object, default: bool) -> bool:
    """Return one boolean body field, keeping the default for anything that is not a boolean."""
    return raw if isinstance(raw, bool) else default


def _target_key(request: Request) -> TargetKey | None:
    """Return the subtask named by the path, or ``None`` when it is not a known dimension."""
    dimension = request.path_params["dimension"]
    if dimension not in DIMENSIONS:
        return None
    protocol = request.path_params["protocol"]
    if dimension == SLOT_DIMENSION and protocol != "a2a-t":
        return None
    try:
        case_id = int(request.path_params["case_id"])
    except (KeyError, TypeError, ValueError):
        return None
    return TargetKey(case_id, protocol, dimension)


async def _json_body(request: Request) -> Mapping[str, object]:
    """Return the request body as a mapping."""
    try:
        payload = json.loads((await request.body()) or b"{}")
    except ValueError:
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _actor(request: Request) -> str:
    """Return the operator identity the console records with a change."""
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


__all__ = ["ANNOTATE_DIR", "build_annotate_routes"]
