from __future__ import annotations

import importlib
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class PackageSkeletonTest(unittest.TestCase):
    def test_a2at_config_is_exported_from_config_package(self) -> None:
        package = importlib.import_module("a2a_t.config")

        self.assertTrue(hasattr(package, "A2ATConfig"))
        self.assertTrue(hasattr(package, "PromptRuntimeConfig"))
        self.assertTrue(hasattr(package, "PromptComplianceConfig"))
        self.assertTrue(hasattr(package, "GuardrailProviderConfig"))
        self.assertTrue(hasattr(package, "ConfigFileNotFoundError"))

    def test_prompt_package_is_exported_from_a2a_t(self) -> None:
        package = importlib.import_module("a2a_t")

        self.assertTrue(hasattr(package, "prompt"))
        self.assertIn("prompt", getattr(package, "__all__", []))

    def test_prompt_package_is_importable_from_a2a_t(self) -> None:
        package = importlib.import_module("a2a_t.prompt")

        self.assertEqual(package.__name__, "a2a_t.prompt")


if __name__ == "__main__":
    unittest.main()
