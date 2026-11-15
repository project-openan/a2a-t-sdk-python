"""HTTP routes of the expectation annotation console.

The console is a separate page on purpose. ``/tester`` shows the batch a browser started and is
scoped to that browser's cookie; annotation works across every batch of a window, whichever
operator ran it, and its unit of work is one case expectation rather than one execution. The two
pages share the catalog and the run store, not their request model.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import yaml
from eval.osp.catalog import CaseCatalog
from eval.osp.expectations import MESSAGE_DIMENSION, SLOT_DIMENSION, SLOT_MODES
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from demo.web.annotate.apply import CASES_ALL, apply, payload_json, preview
from demo.web.annotate.service import AnnotateService
from demo.web.annotate.store import (
    DIMENSIONS,
    TARGETS,
    AnnotationStore,
    TargetKey,
)

#: Directory of the console page assets.
ANNOTATE_DIR: Final[Path] = Path(__file__).resolve().parent

#: Browser identity cookie shared with the tester console (same operator, same anonymous id).
CLIENT_COOKIE: Final[str] = "a2at_tester_client"

#: Default scan window; the operator may widen it.
DEFAULT_DAYS: Final[float] = 1.0

#: Ceiling of the scan window and of the batches one scan reads.
MAX_DAYS: Final[float] = 30.0
MAX_BATCHES: Final[int] = 500


def build_annotate_routes(
    *,
    catalog: CaseCatalog,
    runs_dir: Path,
    client_db_path: Path | None,
    store_path: Path | None = None,
) -> list[Route]:
    """Build the annotation console routes."""
    resolved_store = AnnotationStore(
        store_path if store_path is not None else Path(runs_dir) / "annotations" / "annotations.sqlite3"
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
                "dimensions": list(DIMENSIONS),
                "statuses": list(TARGETS),
                "slotModes": list(SLOT_MODES),
                "runsDir": str(runs_dir),
                "clientDb": str(client_db_path) if client_db_path is not None else None,
                "store": str(resolved_store.path),
                "casesAll": str(CASES_ALL),
                "casesAllPresent": CASES_ALL.is_file(),
                "defaultDays": DEFAULT_DAYS,
                "maxDays": MAX_DAYS,
                "maxBatches": MAX_BATCHES,
            }
        )

    async def targets(request: Request) -> Response:
        """Return the target space with its evidence and confirmation state."""
        rows = await asyncio.to_thread(service.targets)
        progress = await asyncio.to_thread(service.progress)
        return JSONResponse({"targets": rows, **progress})

    async def scan(request: Request) -> Response:
        """Collect the candidates of the recent tester batches."""
        days = min(MAX_DAYS, max(0.05, _float_param(request, "days", DEFAULT_DAYS)))
        limit = max(1, min(MAX_BATCHES, _int_param(request, "maxBatches", MAX_BATCHES)))
        summary = await asyncio.to_thread(service.scan, days=days, max_batches=limit)
        progress = await asyncio.to_thread(service.progress)
        return JSONResponse({"scan": summary.to_dict(), **progress})

    async def target_detail(request: Request) -> Response:
        """Return the candidates and the choices of one target."""
        key = _target_key(request)
        if key is None:
            return JSONResponse({"error": "未知的标注目标。"}, status_code=404)
        detail = await asyncio.to_thread(
            service.detail, key.case_id, key.protocol, key.dimension
        )
        return JSONResponse(detail)

    async def choice(request: Request) -> Response:
        """Accept, edit or reject one candidate."""
        key = _target_key(request)
        if key is None:
            return JSONResponse({"error": "未知的标注目标。"}, status_code=404)
        body = await _json_body(request)
        observation_id = body.get("observationId")
        if not isinstance(observation_id, int) or isinstance(observation_id, bool):
            return JSONResponse({"error": "observationId 必须是整数。"}, status_code=400)
        accepted = bool(body.get("accepted"))
        edited = body.get("editedText")
        await asyncio.to_thread(
            resolved_store.set_choice,
            key,
            observation_id,
            accepted=accepted,
            edited_text=edited if isinstance(edited, str) else None,
            actor=_actor(request),
        )
        return JSONResponse({"ok": True})

    async def mode(request: Request) -> Response:
        """Set the matching mode of a slot-error target."""
        key = _target_key(request)
        if key is None or key.dimension != SLOT_DIMENSION:
            return JSONResponse({"error": "未知的 slot_errors 目标。"}, status_code=404)
        body = await _json_body(request)
        value = str(body.get("mode") or "")
        if value not in SLOT_MODES:
            return JSONResponse({"error": f"mode 只能是 {SLOT_MODES}。"}, status_code=400)
        await asyncio.to_thread(resolved_store.set_mode, key, value, actor=_actor(request))
        return JSONResponse({"ok": True})

    async def note(request: Request) -> Response:
        """Record a free-form note on one target."""
        key = _target_key(request)
        if key is None:
            return JSONResponse({"error": "未知的标注目标。"}, status_code=404)
        body = await _json_body(request)
        await asyncio.to_thread(
            resolved_store.set_note, key, str(body.get("note") or ""), actor=_actor(request)
        )
        return JSONResponse({"ok": True})

    async def confirm(request: Request) -> Response:
        """Confirm the accepted choices of one target."""
        key = _target_key(request)
        if key is None:
            return JSONResponse({"error": "未知的标注目标。"}, status_code=404)
        result = await asyncio.to_thread(resolved_store.confirm, key, actor=_actor(request))
        return JSONResponse({"ok": True, **result})

    async def draft(request: Request) -> Response:
        """Return the corpus patch and its review diff for the confirmed targets."""
        payload = await asyncio.to_thread(resolved_store.export_payload)
        result = await asyncio.to_thread(preview, payload)
        return JSONResponse(
            {
                "ok": result.ok,
                "message": result.message,
                "diff": result.diff,
                "changedCases": list(result.changed_cases),
                "errors": list(result.errors),
                "payload": payload,
            }
        )

    async def apply_annotations(request: Request) -> Response:
        """Write the confirmed targets into the corpus source and regenerate the cases."""
        payload = await asyncio.to_thread(resolved_store.export_payload)
        result = await asyncio.to_thread(apply, payload)
        if result.ok and result.changed_cases:
            keys = _payload_keys(payload)
            await asyncio.to_thread(resolved_store.mark_applied, keys, actor=_actor(request))
            await asyncio.to_thread(catalog.reload)
        return JSONResponse(
            {
                "ok": result.ok,
                "message": result.message,
                "diff": result.diff,
                "changedCases": list(result.changed_cases),
                "written": list(result.written),
                "errors": list(result.errors),
            }
        )

    async def export_yaml(request: Request) -> Response:
        """Return the case-level annotation data as YAML (portable across environments)."""
        del request
        payload = await asyncio.to_thread(resolved_store.export_payload)
        body = yaml.safe_dump(
            payload, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000
        )
        return Response(
            body,
            media_type="application/yaml; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="osp-annotations.yaml"'},
        )

    async def export_json(request: Request) -> Response:
        """Return the same payload as JSON for a diff/merge workflow."""
        del request
        payload = await asyncio.to_thread(resolved_store.export_payload)
        return Response(payload_json(payload), media_type="application/json; charset=utf-8")

    return [
        Route("/annotate", page),
        Route("/annotate/app.css", stylesheet),
        Route("/annotate/main.js", script),
        Route("/api/annotate/meta", meta),
        Route("/api/annotate/targets", targets),
        Route("/api/annotate/scan", scan, methods=["POST"]),
        Route("/api/annotate/target/{case_id}/{protocol}/{dimension}", target_detail),
        Route(
            "/api/annotate/target/{case_id}/{protocol}/{dimension}/choice",
            choice,
            methods=["POST"],
        ),
        Route(
            "/api/annotate/target/{case_id}/{protocol}/{dimension}/mode",
            mode,
            methods=["POST"],
        ),
        Route(
            "/api/annotate/target/{case_id}/{protocol}/{dimension}/note",
            note,
            methods=["POST"],
        ),
        Route(
            "/api/annotate/target/{case_id}/{protocol}/{dimension}/confirm",
            confirm,
            methods=["POST"],
        ),
        Route("/api/annotate/draft", draft),
        Route("/api/annotate/apply", apply_annotations, methods=["POST"]),
        Route("/api/annotate/export.yaml", export_yaml),
        Route("/api/annotate/export.json", export_json),
    ]


def _target_key(request: Request) -> TargetKey | None:
    """Return the target named by the path, or ``None`` when it is not a known dimension."""
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


def _payload_keys(payload: Mapping[str, object]) -> list[TargetKey]:
    """Return the target keys of one export payload."""
    entries = payload.get("cases")
    if not isinstance(entries, list):
        return []
    keys: list[TargetKey] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        case_id = entry.get("id")
        if not isinstance(case_id, int) or isinstance(case_id, bool):
            continue
        for protocol, dimension in _target_dimensions(entry):
            keys.append(TargetKey(case_id, protocol, dimension))
    return keys


def _target_dimensions(entry: Mapping[str, object]) -> list[tuple[str, str]]:
    """Return the ``(protocol, dimension)`` pairs one export payload entry carries."""
    pairs: list[tuple[str, str]] = []
    for protocol, block in entry.items():
        if not isinstance(block, Mapping):
            continue
        if SLOT_DIMENSION in block:
            pairs.append((str(protocol), SLOT_DIMENSION))
        if MESSAGE_DIMENSION in block:
            pairs.append((str(protocol), MESSAGE_DIMENSION))
    return pairs


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
