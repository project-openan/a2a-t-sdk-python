from __future__ import annotations

import importlib
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class PromptComplianceCleanupContractTest(unittest.TestCase):
    def test_public_api_does_not_export_obsolete_runtime_types(self) -> None:
        package = importlib.import_module("a2a_t.server.prompt_compliance")

        self.assertFalse(hasattr(package, "PromptOriginResolver"))
        self.assertFalse(hasattr(package, "SlotSchema"))
        self.assertFalse(hasattr(package, "SlotSchemaBuilder"))
        self.assertFalse(hasattr(package, "SlotSchemaResolver"))
        self.assertFalse(hasattr(package, "SlotValidator"))

    def test_obsolete_internal_modules_are_not_importable(self) -> None:
        module_names = [
            "a2a_t.server.prompt_compliance.origin_resolver",
            "a2a_t.server.prompt_compliance.schema_builder",
            "a2a_t.server.prompt_compliance.slot_schema",
            "a2a_t.server.prompt_compliance.validator",
        ]

        for module_name in module_names:
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(module_name)


if __name__ == "__main__":
    unittest.main()
