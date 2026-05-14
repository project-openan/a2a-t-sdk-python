from __future__ import annotations

import sys
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class ReleaseVersionTest(unittest.TestCase):
    def test_package_version_matches_release_target(self) -> None:
        import a2a_t

        self.assertEqual(a2a_t.__version__, "0.1.5")


if __name__ == "__main__":
    unittest.main()
