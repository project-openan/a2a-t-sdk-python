from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.test_support import ManagedTempDirTestCase


class ManagedTempDirTestCaseTest(unittest.TestCase):
    def test_cleanup_temp_dir_removes_case_and_root_directories(self) -> None:
        case = ManagedTempDirTestCase(methodName="runTest")
        temp_dir = case.make_temp_dir("managed-temp")
        root_dir = temp_dir.parent

        self.assertTrue(temp_dir.exists())
        self.assertTrue(root_dir.exists())

        case.cleanup_temp_dirs()

        self.assertFalse(temp_dir.exists())
        self.assertFalse(root_dir.exists())


if __name__ == "__main__":
    unittest.main()
