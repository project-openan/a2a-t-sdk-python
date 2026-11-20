"""Internal run console: persisted run list, run detail and request-origin diagnostics."""

from __future__ import annotations

from demo.web.runs_console.routes import RUN_CONSOLE_DIR, build_run_console_routes

__all__ = ["RUN_CONSOLE_DIR", "build_run_console_routes"]
