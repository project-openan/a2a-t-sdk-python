"""HTTP routes of the process-wide running-status page.

The tester console and the Run Explorer both hide other browsers' work: a batch belongs to the
anonymous cookie that created it. A tester process, however, has one shared SDK pool and one NMS
quota, so an operator about to start a batch needs to see whether someone else is already running
one. ``GET /status`` is that read-only view; it never mutates a batch or a run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from demo.web.tester.service import EvalBatchManager

#: Static directory of the status page.
STATUS_DIR: Final[Path] = Path(__file__).resolve().parent / "status"

#: Browser cache lifetime of the status assets; the data itself is never cached.
ASSET_CACHE_SECONDS: Final[int] = 60


def build_status_routes(*, batches: EvalBatchManager) -> list[Route]:
    """Build the status page, its assets and the JSON snapshot route."""

    async def page(request: Request) -> Response:
        """Serve the status page shell."""
        del request
        return FileResponse(STATUS_DIR / "index.html")

    async def stylesheet(request: Request) -> Response:
        """Serve the status stylesheet."""
        del request
        return FileResponse(
            STATUS_DIR / "app.css",
            media_type="text/css",
            headers=_asset_headers(),
        )

    async def script(request: Request) -> Response:
        """Serve the status module."""
        del request
        return FileResponse(
            STATUS_DIR / "main.js",
            media_type="text/javascript",
            headers=_asset_headers(),
        )

    async def snapshot(request: Request) -> Response:
        """Return every browser's running work in one JSON document."""
        del request
        return JSONResponse(
            batches.status_snapshot(),
            headers={"Cache-Control": "no-store"},
        )

    return [
        Route("/status", page),
        Route("/status/app.css", stylesheet),
        Route("/status/main.js", script),
        Route("/api/status", snapshot),
    ]


def _asset_headers() -> dict[str, str]:
    """Return the cache headers of one status asset."""
    return {"Cache-Control": f"public, max-age={ASSET_CACHE_SECONDS}"}


__all__ = ["build_status_routes", "STATUS_DIR"]
