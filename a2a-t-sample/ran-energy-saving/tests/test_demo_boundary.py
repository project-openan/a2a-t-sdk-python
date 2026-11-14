"""Import-boundary test: the core must not load demo assets at import time."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _child_env() -> dict[str, str]:
    """Return the process environment with the sample root on ``PYTHONPATH``."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    return env


_CORE_MODULES = (
    "protocol.client_runtime",
    "protocol.client_service",
    "protocol.client_gateway",
    "protocol.decision_provider",
    "protocol.pre_task_negotiation",
    "protocol.negotiation_policy",
    "protocol.server_app",
    "protocol.server_executor",
    "shared.config",
    "shared.model_factory",
    "shared.sdk_env",
    "shared.traced_llm",
)


def test_core_imports_do_not_load_the_demo_package() -> None:
    """Production modules may reference demo names lazily, never at module import time."""
    script = """
import importlib
import sys

for name in {modules!r}:
    importlib.import_module(name)

loaded = sorted(module for module in sys.modules if module == "demo" or module.startswith("demo."))
assert not loaded, loaded
print("boundary-ok")
""".format(modules=list(_CORE_MODULES))
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=_child_env(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "boundary-ok" in result.stdout


def test_the_runtime_requires_a_run_catalog() -> None:
    """The core runtime has no default scenario catalog; the composition root supplies one."""
    script = """
import asyncio
from shared.config import load_config
from protocol.client_runtime import build_client_runtime

config = load_config()
try:
    asyncio.run(build_client_runtime(config, "information", "manual", "sse"))
except TypeError as exc:
    assert "catalog" in str(exc), exc
    print("catalog-required")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=_child_env(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "catalog-required" in result.stdout
