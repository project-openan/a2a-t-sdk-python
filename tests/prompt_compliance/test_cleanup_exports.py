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

        self.assertFalse(hasattr(package, "A2ATTaskPromptParseError"))
        self.assertFalse(hasattr(package, "A2ATTaskPromptParser"))
        self.assertFalse(hasattr(package, "GuardrailDecision"))
        self.assertFalse(hasattr(package, "GuardrailProviderConfig"))
        self.assertFalse(hasattr(package, "GuardrailRequest"))
        self.assertFalse(hasattr(package, "GuardrailResult"))
        self.assertFalse(hasattr(package, "AdapterSafetyGuardrail"))
        self.assertFalse(hasattr(package, "GoogleModelArmorGateway"))
        self.assertFalse(hasattr(package, "GoogleModelArmorGuardrailAdapter"))
        self.assertFalse(hasattr(package, "GuardrailAdapter"))
        self.assertFalse(hasattr(package, "PromptOriginResolver"))
        self.assertFalse(hasattr(package, "PromptIdentity"))
        self.assertFalse(hasattr(package, "PromptSlotExtractor"))
        self.assertFalse(hasattr(package, "SafetyGuardrail"))
        self.assertFalse(hasattr(package, "SafetyGuardrailFactory"))
        self.assertFalse(hasattr(package, "SlotExtractionConfig"))
        self.assertFalse(hasattr(package, "SlotExtractionError"))
        self.assertFalse(hasattr(package, "SlotExtractionResult"))
        self.assertFalse(hasattr(package, "SlotSchema"))
        self.assertFalse(hasattr(package, "SlotSchemaLoadError"))
        self.assertFalse(hasattr(package, "SlotSchemaBuilder"))
        self.assertFalse(hasattr(package, "SlotSchemaResolver"))
        self.assertFalse(hasattr(package, "SlotSchemaValidationError"))
        self.assertFalse(hasattr(package, "SlotValidationError"))
        self.assertFalse(hasattr(package, "SlotValidator"))
        self.assertFalse(hasattr(package, "SlotValidationResult"))

    def test_server_runtime_modules_do_not_define_legacy_slot_runtime_types(self) -> None:
        result_module = importlib.import_module("a2a_t.server.prompt_compliance.result")

        self.assertFalse(hasattr(result_module, "GuardrailDecision"))
        self.assertFalse(hasattr(result_module, "GuardrailProviderConfig"))
        self.assertFalse(hasattr(result_module, "GuardrailRequest"))
        self.assertFalse(hasattr(result_module, "GuardrailResult"))
        self.assertFalse(hasattr(result_module, "SlotExtractionResult"))
        self.assertFalse(hasattr(result_module, "SlotValidationResult"))

    def test_obsolete_internal_modules_are_not_importable(self) -> None:
        module_names = [
            "a2a_t.server.prompt_compliance.config",
            "a2a_t.server.prompt_compliance.errors",
            "a2a_t.server.prompt_compliance.extractor",
            "a2a_t.server.prompt_compliance.origin_resolver",
            "a2a_t.server.prompt_compliance.parser",
            "a2a_t.server.prompt_compliance.schema_builder",
            "a2a_t.server.prompt_compliance.service",
            "a2a_t.server.prompt_compliance.slot_schema",
            "a2a_t.server.prompt_compliance.validator",
            "a2a_t.server.prompt_compliance.models",
        ]

        for module_name in module_names:
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(module_name)


if __name__ == "__main__":
    unittest.main()
