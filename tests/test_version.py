from __future__ import annotations

from pathlib import Path

import tomllib

from a2a_t import __version__

EXPECTED_VERSION = "0.1.8"


def test_package_version_matches_release_version() -> None:
    pyproject_data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject_data["project"]["version"] == EXPECTED_VERSION
    assert __version__ == EXPECTED_VERSION
