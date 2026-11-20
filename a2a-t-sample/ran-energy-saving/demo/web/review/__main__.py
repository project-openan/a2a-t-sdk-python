"""Read-only acceptance review console, pointed at any instance's run data.

The review console only reads persisted artifacts: the tester batch snapshots and the client
store's negotiation events. It never starts a run, never answers an approval and never touches run
state. Like the annotation console, it gets its own entry point instead of a second full Web UI:
the full application runs :meth:`RunManager.recover_runs` at startup, which would mark the
unfinished runs of a live instance as interrupted.

Usage::

    python -m demo.web.review --runs-dir /data/live/runs --store /tmp/review/review.sqlite3

The source directory is opened read-only. The review database is the only file written, so point
``--store`` elsewhere when the source directory belongs to a running instance.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from eval.osp.catalog import DEFAULT_OVERLAY_PATH, CaseCatalog
from eval.osp.runner import DEFAULT_CASES_DIR
from shared.http_security import install_health_routes
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from demo.web.annotate.overlay import confirmed_overlay, overlay_token
from demo.web.review.routes import build_review_routes
from demo.web.review.traces import TraceStore

#: The sample root (``a2a-t-sample/ran-energy-saving``).
SAMPLE_ROOT: Final[Path] = Path(__file__).resolve().parents[3]

#: Loopback port; distinct from the Web UI (8080) and the annotation console (8090).
DEFAULT_PORT: Final[int] = 8091

#: File the review console only ever reads, relative to the source runs directory.
CLIENT_DB_NAME: Final[str] = "client-state.sqlite3"


def build_app(
    *,
    runs_dir: Path,
    client_db_path: Path | None = None,
    store_path: Path | None = None,
    cases_dir: Path | None = None,
    overlay_path: Path | None = None,
    annotations_path: Path | None = None,
    trace_store_path: Path | None = None,
    fetch_traces: bool = False,
) -> Starlette:
    """Build the read-only review application.

    ``annotations_path`` is the annotation console's database. Its confirmed values are merged by
    case id at catalog load time, so the reviewer judges against the latest annotations without an
    export step.
    """
    resolved_runs = Path(runs_dir)
    resolved_client_db = Path(client_db_path) if client_db_path is not None else resolved_runs / CLIENT_DB_NAME
    resolved_store = Path(store_path) if store_path is not None else resolved_runs / "review" / "review.sqlite3"
    resolved_annotations = (
        Path(annotations_path)
        if annotations_path is not None
        else resolved_runs / "annotations" / "annotations.sqlite3"
    )
    catalog = CaseCatalog(
        cases_dir if cases_dir is not None else DEFAULT_CASES_DIR,
        overlay_path=overlay_path,
        annotation_overlay=lambda: confirmed_overlay(resolved_annotations),
        annotation_token=lambda: overlay_token(resolved_annotations),
    )
    routes = build_review_routes(
        catalog=catalog,
        runs_dir=resolved_runs,
        client_db_path=resolved_client_db,
        store_path=resolved_store,
        trace_store=TraceStore(resolved_runs, trace_store_path),
        trace_fetch_enabled=fetch_traces,
    )

    async def index(request: Request) -> Response:
        """Send the root path to the console."""
        del request
        return RedirectResponse("/review")

    app = Starlette(routes=[Route("/", index), *routes])
    install_health_routes(
        app,
        service="ran-energy-saving-review",
        checks={
            "cases": lambda: catalog.count > 0,
            "store": lambda: resolved_store.parent.is_dir(),
        },
    )
    return app


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser of the standalone console."""
    parser = argparse.ArgumentParser(
        prog="python -m demo.web.review",
        description=(
            "Read-only acceptance review console. It reads another instance's tester batches and "
            "run store; it never starts or recovers runs."
        ),
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=SAMPLE_ROOT / "runs",
        help="source runs directory (batches in <runs-dir>/tester); default: <sample>/runs",
    )
    parser.add_argument(
        "--client-db",
        type=Path,
        default=None,
        help=f"client store to read; default: <runs-dir>/{CLIENT_DB_NAME}",
    )
    parser.add_argument(
        "--store",
        type=Path,
        default=None,
        help="review database; default: <runs-dir>/review/review.sqlite3",
    )
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=None,
        help="case corpus to review against; default: the packaged eval/osp/cases",
    )
    parser.add_argument(
        "--overlay",
        type=Path,
        default=None,
        help=(f"hand-authored expectation overlay merged into the catalog; default: {DEFAULT_OVERLAY_PATH}"),
    )
    parser.add_argument(
        "--annotations",
        type=Path,
        default=None,
        help=(
            "annotation database whose confirmed values are merged by case id; "
            "default: <runs-dir>/annotations/annotations.sqlite3"
        ),
    )
    parser.add_argument(
        "--trace-store",
        type=Path,
        default=None,
        help=("directory of the local Langfuse snapshots; default: <runs-dir>/review/langfuse"),
    )
    parser.add_argument(
        "--fetch-traces",
        action="store_true",
        help=(
            "allow the page to fetch a trace from Langfuse (read-only instances leave this off; "
            "requires LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY)"
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default: loopback)")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"bind port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="print the resolved paths and exit instead of serving",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the arguments and serve the console, or print the resolved paths with ``--check``."""
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    runs_dir = Path(args.runs_dir).expanduser().resolve()
    client_db = Path(args.client_db).expanduser().resolve() if args.client_db is not None else runs_dir / CLIENT_DB_NAME
    store = (
        Path(args.store).expanduser().resolve() if args.store is not None else runs_dir / "review" / "review.sqlite3"
    )
    cases_dir = Path(args.cases_dir).expanduser().resolve() if args.cases_dir is not None else None
    overlay = Path(args.overlay).expanduser().resolve() if args.overlay is not None else None
    annotations = (
        Path(args.annotations).expanduser().resolve()
        if args.annotations is not None
        else runs_dir / "annotations" / "annotations.sqlite3"
    )
    trace_store = (
        Path(args.trace_store).expanduser().resolve()
        if args.trace_store is not None
        else runs_dir / "review" / "langfuse"
    )
    print(f"[review] source batches : {runs_dir / 'tester'}")
    print(f"[review] source run store: {client_db}")
    print(f"[review] review store   : {store}")
    print(f"[review] annotations    : {annotations}")
    print(f"[review] trace snapshots: {trace_store}{'' if args.fetch_traces else ' (fetch disabled)'}")
    print(f"[review] case corpus    : {cases_dir or 'packaged eval/osp/cases'}")
    print(f"[review] expectation overlay: {overlay or DEFAULT_OVERLAY_PATH}")
    if not client_db.is_file():
        print(
            "[review] WARNING: the client store is missing; negotiation-message evidence will be "
            "unavailable for batches that did not store it (they will be marked partial).",
            file=sys.stderr,
        )
    if store.is_relative_to(runs_dir):
        print(
            "[review] note: the review database lives inside the source directory "
            "(review/ only); pass --store elsewhere to keep the source strictly untouched.",
            file=sys.stderr,
        )
    if not args.fetch_traces:
        print(
            "[review] note: trace fetching is off, so the page can only read snapshots that "
            "already exist here. Add --fetch-traces (and LANGFUSE_PUBLIC_KEY / "
            "LANGFUSE_SECRET_KEY in the environment or .env) to load them from the page.",
            file=sys.stderr,
        )
    if args.check:
        return 0

    import uvicorn

    app = build_app(
        runs_dir=runs_dir,
        client_db_path=client_db,
        store_path=store,
        cases_dir=cases_dir,
        overlay_path=overlay,
        annotations_path=annotations,
        trace_store_path=trace_store,
        fetch_traces=bool(args.fetch_traces),
    )
    print(f"[review] serving on http://{args.host}:{args.port}/review (read-only)")
    print("[review] this process does not start runs and does not recover run state")
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
