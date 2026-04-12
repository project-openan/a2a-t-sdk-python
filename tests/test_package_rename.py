"""Tests for the a2a_t package rename migration."""

from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"


def test_a2a_t_package_is_importable() -> None:
    """The new runtime package name should be importable from src/."""
    sys.path.insert(0, str(SRC_ROOT))
    try:
        module = importlib.import_module("a2a_t")
    finally:
        sys.path.pop(0)

    assert module.__name__ == "a2a_t"


def test_a2a_t_sdk_package_is_not_importable() -> None:
    """The legacy runtime package name should no longer resolve."""
    sys.path.insert(0, str(SRC_ROOT))
    try:
        try:
            importlib.import_module("a2a_t_sdk")
        except ModuleNotFoundError:
            return
    finally:
        sys.path.pop(0)

    raise AssertionError("Expected importing a2a_t_sdk to fail")


def test_uv_build_module_name_targets_a2a_t() -> None:
    """The build backend should package the renamed runtime module."""
    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    module_name = pyproject["tool"]["uv"]["build-backend"]["module-name"]

    assert module_name == "a2a_t"


def test_readme_uses_a2a_t_package_name() -> None:
    """The public README should only describe the renamed package path."""
    readme_text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "from a2a_t import ExtendedClient" in readme_text
    assert "src/a2a_t/" in readme_text
    assert "a2a_t_sdk" not in readme_text
