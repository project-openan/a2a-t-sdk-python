from __future__ import annotations

import sys
import tomllib
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))


class PytestConfigTest(unittest.TestCase):
    def test_pytest_disables_cacheprovider_by_default(self) -> None:
        pyproject_path = PROJECT_ROOT / "pyproject.toml"
        config = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

        pytest_options = config["tool"]["pytest"]["ini_options"]
        addopts = pytest_options.get("addopts", "")

        self.assertIn("-p no:cacheprovider", addopts)


if __name__ == "__main__":
    unittest.main()
