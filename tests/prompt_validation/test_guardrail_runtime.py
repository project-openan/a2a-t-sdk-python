from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class PromptValidationGuardrailRuntimeTest(unittest.TestCase):
    def test_shared_guardrail_runtime_is_available_under_validation_package(self) -> None:
        from a2a_t.prompt.validation import SafetyGuardrailFactory
        from a2a_t.prompt.validation.guardrails import AdapterSafetyGuardrail
        from a2a_t.prompt.validation.guardrails import SafetyGuardrailFactory as SharedSafetyGuardrailFactory
        from a2a_t.server.prompt_compliance.guardrails import AdapterSafetyGuardrail as LegacyAdapterSafetyGuardrail
        from a2a_t.server.prompt_compliance.guardrails import SafetyGuardrailFactory as LegacySafetyGuardrailFactory

        self.assertIs(SafetyGuardrailFactory, SharedSafetyGuardrailFactory)
        self.assertIs(LegacyAdapterSafetyGuardrail, AdapterSafetyGuardrail)
        self.assertIs(LegacySafetyGuardrailFactory, SharedSafetyGuardrailFactory)

    def test_shared_guardrail_provider_runtime_is_available_under_validation_package(self) -> None:
        from a2a_t.prompt.validation.guardrail_providers import GoogleModelArmorGateway
        from a2a_t.prompt.validation.guardrail_providers import (
            GoogleModelArmorGuardrailAdapter as SharedGoogleModelArmorGuardrailAdapter,
        )
        from a2a_t.server.prompt_compliance.guardrail_providers import GoogleModelArmorGateway as LegacyGoogleModelArmorGateway
        from a2a_t.server.prompt_compliance.guardrail_providers import (
            GoogleModelArmorGuardrailAdapter as LegacyGoogleModelArmorGuardrailAdapter,
        )

        self.assertIs(LegacyGoogleModelArmorGateway, GoogleModelArmorGateway)
        self.assertIs(LegacyGoogleModelArmorGuardrailAdapter, SharedGoogleModelArmorGuardrailAdapter)


if __name__ == "__main__":
    unittest.main()
