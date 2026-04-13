from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class SharedPromptRuntimePackageTest(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        if "a2a" not in sys.modules:
            a2a_module = types.ModuleType("a2a")
            a2a_types_module = types.ModuleType("a2a.types")

            class AgentCard:  # pragma: no cover
                pass

            a2a_types_module.AgentCard = AgentCard
            a2a_module.types = a2a_types_module
            sys.modules["a2a"] = a2a_module
            sys.modules["a2a.types"] = a2a_types_module

    def test_shared_prompt_subpackages_are_importable(self) -> None:
        module_names = [
            "a2a_t.prompt.common",
            "a2a_t.prompt.resources",
            "a2a_t.prompt.analysis",
            "a2a_t.prompt.validation",
        ]

        for module_name in module_names:
            module = importlib.import_module(module_name)
            self.assertEqual(module.__name__, module_name)


if __name__ == "__main__":
    unittest.main()
