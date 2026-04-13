from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.config.models import SDKConfig
from a2a_t.server.prompt_compliance.config import PromptComplianceConfig
from a2a_t.server.prompt_compliance.errors import PromptComplianceError
from a2a_t.server.prompt_compliance.models import (
    GuardrailDecision,
    GuardrailProviderConfig,
    GuardrailRequest,
    GuardrailResult,
    PromptComplianceResult,
    PromptIdentity,
    SlotExtractionConfig,
    SlotExtractionResult,
    SlotSchemaConfig,
)


class PromptComplianceModelsTest(unittest.TestCase):
    def test_sdk_config_from_dict_builds_prompt_compliance_config(self) -> None:
        config = SDKConfig.from_dict(
            {
                "prompt_compliance": {
                    "enabled": True,
                    "guardrail": {
                        "provider": "custom_guardrail",
                        "timeout": 11,
                        "config": {"endpoint": "https://guardrail.example"},
                    },
                    "slot_extraction": {
                        "provider": "openai",
                        "model": "gpt-4.1",
                        "timeout": 22,
                        "temperature": 0.1,
                        "max_retries": 4,
                    },
                    "slot_schema": {
                        "root_dir": "./slots",
                        "slot_root_name": "slots",
                        "file_name": "slot.json",
                        "not_found_policy": "skip",
                    },
                    "providers": {
                        "openai": {"api_key": "openai-key"},
                        "anthropic": {"api_key": "anthropic-key"},
                    },
                }
            }
        )

        self.assertTrue(config.prompt_compliance.enabled)
        self.assertEqual(config.prompt_compliance.guardrail.provider, "custom_guardrail")
        self.assertEqual(config.prompt_compliance.guardrail.timeout, 11)
        self.assertEqual(config.prompt_compliance.slot_extraction.provider, "openai")
        self.assertEqual(config.prompt_compliance.slot_extraction.model, "gpt-4.1")
        self.assertEqual(config.prompt_compliance.slot_schema.not_found_policy, "skip")
        self.assertEqual(config.prompt_compliance.providers["openai"]["api_key"], "openai-key")

    def test_sdk_config_from_dict_uses_prompt_compliance_defaults(self) -> None:
        config = SDKConfig.from_dict({})

        self.assertIsInstance(config.prompt_compliance, PromptComplianceConfig)
        self.assertFalse(config.prompt_compliance.enabled)
        self.assertEqual(config.prompt_compliance.guardrail.provider, "")
        self.assertEqual(config.prompt_compliance.slot_extraction.max_retries, 2)
        self.assertEqual(config.prompt_compliance.slot_schema.root_dir, "./slots")
        self.assertEqual(config.prompt_compliance.slot_schema.file_name, "slot.json")
        self.assertEqual(config.prompt_compliance.providers, {})

    def test_prompt_compliance_config_from_mapping_is_available_from_config_module(self) -> None:
        config = PromptComplianceConfig.from_mapping({})

        self.assertEqual(config.guardrail.provider, "noop")
        self.assertEqual(config.slot_schema.root_dir, "./slots")
        self.assertEqual(config.slot_schema.file_name, "slot.json")
        self.assertEqual(config.slot_schema.not_found_policy, "strict")

    def test_prompt_compliance_domain_models_expose_expected_defaults(self) -> None:
        identity = PromptIdentity(name="network_device_query", language="zh-CN", version="1.0.0")
        guardrail_request = GuardrailRequest(text="processed prompt")
        guardrail_result = GuardrailResult(passed=True)
        extraction_result = SlotExtractionResult(slots={"device_type": "router"}, notes=["from prompt"])
        compliance_result = PromptComplianceResult(passed=False, stage="slot_validation")

        self.assertEqual(identity.name, "network_device_query")
        self.assertEqual(identity.scenario_code, "network_device_query")
        self.assertEqual(guardrail_request.text, "processed prompt")
        self.assertIsNone(guardrail_request.policy_id)
        self.assertEqual(guardrail_result.decision, GuardrailDecision.ALLOW)
        self.assertIsNone(guardrail_result.reason)
        self.assertIsNone(guardrail_result.provider)
        self.assertIsNone(guardrail_result.policy_id)
        self.assertIsNone(extraction_result.confidence)
        self.assertIsNone(extraction_result.raw_response)
        self.assertIsNone(compliance_result.error_code)
        self.assertIsNone(compliance_result.extracted_slots)

    def test_prompt_compliance_error_keeps_machine_readable_context(self) -> None:
        error = PromptComplianceError("compliance failed", stage="slot_validation", code="missing_slot")

        self.assertEqual(str(error), "compliance failed")
        self.assertEqual(error.context, {"stage": "slot_validation", "code": "missing_slot"})

    def test_prompt_compliance_config_models_can_be_constructed_directly(self) -> None:
        config = PromptComplianceConfig(
            enabled=True,
            guardrail=GuardrailProviderConfig(
                provider="guardrail-a",
                timeout=5.0,
                policy_id="policy-a",
                endpoint="https://guardrail.example",
                region="us-central1",
                credentials_ref="GOOGLE_APPLICATION_CREDENTIALS",
                config={"mode": "strict"},
            ),
            slot_extraction=SlotExtractionConfig(provider="google", model="gemini-2.5-pro"),
            slot_schema=SlotSchemaConfig(root_dir="./cache", slot_root_name="slots"),
            providers={"google": {"api_key": "secret"}},
        )

        self.assertTrue(config.enabled)
        self.assertEqual(config.guardrail.config["mode"], "strict")
        self.assertEqual(config.guardrail.policy_id, "policy-a")
        self.assertEqual(config.guardrail.endpoint, "https://guardrail.example")
        self.assertEqual(config.guardrail.region, "us-central1")
        self.assertEqual(config.guardrail.credentials_ref, "GOOGLE_APPLICATION_CREDENTIALS")
        self.assertEqual(config.slot_extraction.provider, "google")
        self.assertEqual(config.slot_schema.root_dir, "./cache")
        self.assertEqual(config.providers["google"]["api_key"], "secret")

    def test_old_prompt_compliance_provider_config_is_not_exported(self) -> None:
        import a2a_t.server.prompt_compliance as prompt_compliance

        self.assertTrue(hasattr(prompt_compliance, "GuardrailProviderConfig"))
        self.assertFalse(hasattr(prompt_compliance, "PromptComplianceProviderConfig"))


if __name__ == "__main__":
    unittest.main()
