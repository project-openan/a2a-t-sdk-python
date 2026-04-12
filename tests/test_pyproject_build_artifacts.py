from __future__ import annotations

import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_pyproject() -> dict:
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_wheel_data_directory_contains_packaged_resources() -> None:
    assert (PROJECT_ROOT / "package_data" / ".env").is_file()
    assert (PROJECT_ROOT / "package_data" / "env.example").is_file()
    assert (PROJECT_ROOT / "package_data" / "prompts" / ".gitkeep").is_file()
    assert (PROJECT_ROOT / "package_data" / "slots" / ".gitkeep").is_file()

def test_uv_build_uses_default_source_include_behavior() -> None:
    pyproject = load_pyproject()
    build_backend = pyproject["tool"]["uv"]["build-backend"]

    assert "source-include" not in build_backend


def test_sdist_excludes_gitignore() -> None:
    pyproject = load_pyproject()
    sdist = pyproject["tool"]["uv"]["build-backend"]
    excludes = set(sdist["source-exclude"])

    assert ".gitignore" in excludes


def test_build_backend_switches_to_uv_build() -> None:
    pyproject = load_pyproject()

    build_system = pyproject["build-system"]

    assert build_system["build-backend"] == "uv_build"
    assert any(requirement.startswith("uv_build>=") for requirement in build_system["requires"])


def test_wheel_data_directory_is_configured() -> None:
    pyproject = load_pyproject()
    build_backend = pyproject["tool"]["uv"]["build-backend"]

    assert build_backend["data"]["data"] == "package_data"


def test_project_uses_pep_639_license_metadata() -> None:
    pyproject = load_pyproject()
    project = pyproject["project"]
    classifiers = project["classifiers"]

    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert "License :: OSI Approved :: Apache Software License" not in classifiers
