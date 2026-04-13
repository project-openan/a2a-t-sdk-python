from __future__ import annotations

import importlib
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class PromptValidationRuntimeModuleTest(unittest.TestCase):
    def test_validation_guardrail_runtime_modules_are_importable(self) -> None:
        module_names = [
            "a2a_t.prompt.validation.constants",
            "a2a_t.prompt.validation.errors",
            "a2a_t.prompt.validation.guardrails",
            "a2a_t.prompt.validation.guardrail_providers",
        ]

        for module_name in module_names:
            module = importlib.import_module(module_name)
            self.assertEqual(module.__name__, module_name)

    def test_server_guardrail_shim_modules_are_not_importable(self) -> None:
        module_names = [
            "a2a_t.prompt.validation.config",
            "a2a_t.server.prompt_compliance.guardrails",
            "a2a_t.server.prompt_compliance.guardrail_providers",
        ]

        for module_name in module_names:
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(module_name)


if __name__ == "__main__":
    unittest.main()
