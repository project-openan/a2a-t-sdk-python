from __future__ import annotations

import importlib
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class PromptResourceRuntimeModuleTest(unittest.TestCase):
    def test_resource_runtime_modules_are_importable(self) -> None:
        module_names = [
            "a2a_t.prompt.resources",
            "a2a_t.prompt.resources.cache",
            "a2a_t.prompt.resources.catalog",
            "a2a_t.prompt.resources.catalog_registry",
            "a2a_t.prompt.resources.parser",
            "a2a_t.prompt.resources.providers",
        ]

        for module_name in module_names:
            module = importlib.import_module(module_name)
            self.assertEqual(module.__name__, module_name)

    def test_legacy_resource_shim_modules_are_not_importable(self) -> None:
        module_names = [
            "a2a_t.prompt.cache",
            "a2a_t.prompt.catalog",
            "a2a_t.prompt.catalog_registry",
            "a2a_t.prompt.parser",
            "a2a_t.prompt.providers",
        ]

        for module_name in module_names:
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(module_name)


if __name__ == "__main__":
    unittest.main()
