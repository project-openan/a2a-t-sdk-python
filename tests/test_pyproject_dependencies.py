from __future__ import annotations

import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_pyproject() -> dict:
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_project_dependencies_include_runtime_packages_in_use() -> None:
    pyproject = load_pyproject()
    dependencies = pyproject["project"]["dependencies"]

    assert any(dependency.startswith("pyyaml>=") for dependency in dependencies)
    assert any(dependency.startswith("httpx>=") for dependency in dependencies)
    assert any(dependency.startswith("pydantic>=") for dependency in dependencies)
    assert any(dependency.startswith("jsonschema>=") for dependency in dependencies)
    assert any(dependency.startswith("python-dotenv>=") for dependency in dependencies)
    assert any(dependency.startswith("google-cloud-modelarmor>=") for dependency in dependencies)


def test_project_defines_dev_extra_for_test_and_quality_tools() -> None:
    pyproject = load_pyproject()
    dev_dependencies = pyproject["project"]["optional-dependencies"]["dev"]

    assert any(dependency.startswith("pytest>=") for dependency in dev_dependencies)
    assert any(dependency.startswith("pytest-asyncio>=") for dependency in dev_dependencies)
    assert any(dependency.startswith("pytest-cov>=") for dependency in dev_dependencies)
    assert any(dependency.startswith("ruff>=") for dependency in dev_dependencies)
    assert any(dependency.startswith("mypy>=") for dependency in dev_dependencies)


def test_project_no_longer_relies_on_requirements_txt() -> None:
    assert not (PROJECT_ROOT / "requirements.txt").exists()


def test_project_no_longer_uses_hatch_metadata_configuration() -> None:
    pyproject = load_pyproject()

    assert "hatch" not in pyproject.get("tool", {})
