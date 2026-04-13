from __future__ import annotations

import importlib
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class PromptCommonRuntimeModuleTest(unittest.TestCase):
    def test_common_runtime_modules_are_importable(self) -> None:
        module_names = [
            "a2a_t.prompt.common",
            "a2a_t.prompt.common.errors",
            "a2a_t.prompt.common.models",
            "a2a_t.prompt.common.a2a_t_task_prompt",
        ]

        for module_name in module_names:
            module = importlib.import_module(module_name)
            self.assertEqual(module.__name__, module_name)

    def test_common_constants_module_is_not_importable(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("a2a_t.prompt.common.constants")

    def test_legacy_common_config_module_is_not_importable(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("a2a_t.prompt.common.config")

    def test_legacy_common_shim_modules_are_not_importable(self) -> None:
        module_names = [
            "a2a_t.prompt.config",
            "a2a_t.prompt.errors",
            "a2a_t.prompt.models",
        ]

        for module_name in module_names:
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(module_name)


if __name__ == "__main__":
    unittest.main()
