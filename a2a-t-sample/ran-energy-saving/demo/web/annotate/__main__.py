"""Read-only annotation console server, pointed at any instance's run data.

The annotation console only reads persisted artifacts: the tester batch snapshots and the client
store's negotiation events. It never starts a run, never answers an approval and never touches the
run state. That is why it gets its own entry point instead of a second full Web UI: the full app
runs :meth:`RunManager.recover_runs` at startup, which would mark the unfinished runs of the live
instance as interrupted — exactly what "I cannot restart my yellow-zone instance" forbids.

Usage::

    python -m demo.web.annotate --runs-dir /data/live/runs --store /tmp/annotate/annotations.sqlite3

The source directory is opened read-only. The annotation database is the only file written, so point
``--store`` elsewhere when the source directory belongs to a running instance.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from eval.osp.catalog import CaseCatalog
from shared.http_security import install_health_routes
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from demo.web.annotate.routes import build_annotate_routes

#: The sample root (``a2a-t-sample/ran-energy-saving``).
SAMPLE_ROOT: Final[Path] = Path(__file__).resolve().parents[3]

#: Loopback port of the standalone console; distinct from the Web UI (8080) and the agents.
DEFAULT_PORT: Final[int] = 8090

#: File the annotation console only ever reads, relative to the source runs directory.
CLIENT_DB_NAME: Final[str] = "client-state.sqlite3"


def build_app(
    *,
    runs_dir: Path,
    client_db_path: Path | None = None,
    store_path: Path | None = None,
    cases_dir: Path | None = None,
) -> Starlette:
    """Build the read-only annotation application.

    Args:
        runs_dir: directory holding ``tester/*.json`` (and the client store by default).
        client_db_path: client store to read the negotiation messages from; ``None`` or a missing
            file leaves only the slot-error candidates.
        store_path: annotation database; defaults to ``<runs_dir>/annotations/annotations.sqlite3``.
        cases_dir: case corpus to annotate; defaults to the packaged ``eval/osp/cases``.
    """
    resolved_runs = Path(runs_dir)
    resolved_client_db = (
        Path(client_db_path) if client_db_path is not None else resolved_runs / CLIENT_DB_NAME
    )
    resolved_store = (
        Path(store_path)
        if store_path is not None
        else resolved_runs / "annotations" / "annotations.sqlite3"
    )
    catalog = CaseCatalog(cases_dir) if cases_dir is not None else CaseCatalog()
    routes = build_annotate_routes(
        catalog=catalog,
        runs_dir=resolved_runs,
        client_db_path=resolved_client_db,
        store_path=resolved_store,
    )

    async def index(request: Request) -> Response:
        """Send the root path to the console."""
        del request
        return RedirectResponse("/annotate")

    app = Starlette(routes=[Route("/", index), *routes])
    install_health_routes(
        app,
        service="ran-energy-saving-annotate",
        checks={
            "cases": lambda: catalog.count > 0,
            "store": lambda: resolved_store.parent.is_dir(),
        },
    )
    return app


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser of the standalone console."""
    parser = argparse.ArgumentParser(
        prog="python -m demo.web.annotate",
        description=(
            "Read-only expectation annotation console. It reads another instance's tester batches "
            "and run store; it never starts or recovers runs."
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
        help="annotation database; default: <runs-dir>/annotations/annotations.sqlite3",
    )
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=None,
        help="case corpus to annotate; default: the packaged eval/osp/cases",
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
    client_db = (
        Path(args.client_db).expanduser().resolve()
        if args.client_db is not None
        else runs_dir / CLIENT_DB_NAME
    )
    store = (
        Path(args.store).expanduser().resolve()
        if args.store is not None
        else runs_dir / "annotations" / "annotations.sqlite3"
    )
    cases_dir = Path(args.cases_dir).expanduser().resolve() if args.cases_dir is not None else None
    print(f"[annotate] source batches : {runs_dir / 'tester'}")
    print(f"[annotate] source run store: {client_db}")
    print(f"[annotate] annotation store: {store}")
    print(f"[annotate] case corpus     : {cases_dir or 'packaged eval/osp/cases'}")
    if not client_db.is_file():
        print(
            "[annotate] WARNING: the client store is missing; only slot-error candidates will be "
            "collected (server messages come from it).",
            file=sys.stderr,
        )
    if store.is_relative_to(runs_dir):
        print(
            "[annotate] note: the annotation database lives inside the source directory "
            "(annotations/ only); pass --store elsewhere to keep the source strictly untouched.",
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
    )
    print(f"[annotate] serving on http://{args.host}:{args.port}/annotate (read-only)")
    print("[annotate] this process does not start runs and does not recover run state")
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
