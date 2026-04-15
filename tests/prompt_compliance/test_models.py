from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.config.models import GuardrailProviderConfig, PromptComplianceConfig, PromptRuntimeConfig, SDKConfig
from a2a_t.prompt.common.models import PromptReference
from a2a_t.prompt.validation.models import GuardrailDecision, GuardrailRequest, GuardrailResult
from a2a_t.server.prompt_compliance.result import (
    PromptComplianceResult,
)


class PromptComplianceResultTest(unittest.TestCase):
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
        self.assertFalse(hasattr(config.prompt_compliance, "slot_schema"))
        self.assertEqual(config.prompt_compliance.providers["openai"]["api_key"], "openai-key")

    def test_sdk_config_from_dict_uses_prompt_compliance_defaults(self) -> None:
        config = SDKConfig.from_dict({})

        self.assertIsInstance(config.prompt_compliance, PromptComplianceConfig)
        self.assertFalse(config.prompt_compliance.enabled)
        self.assertEqual(config.prompt_compliance.guardrail.provider, "noop")
        self.assertFalse(hasattr(config.prompt_compliance, "slot_schema"))
        self.assertEqual(config.prompt_compliance.providers, {})

    def test_prompt_compliance_config_from_mapping_is_available_from_config_module(self) -> None:
        config = PromptComplianceConfig.from_mapping({})

        self.assertEqual(config.guardrail.provider, "noop")
        self.assertFalse(hasattr(config, "slot_schema"))
        self.assertEqual(config.providers, {})

    def test_a2at_runtime_config_is_available_from_config_module(self) -> None:
        runtime = PromptRuntimeConfig()

        self.assertEqual(runtime.language, "en-US")
        self.assertEqual(runtime.prompt_resource_version, "0.0.1")
        self.assertEqual(runtime.source_type, "local_file")
        self.assertEqual(runtime.local_root_dir, "./package_data/prompt_resources")

    def test_prompt_compliance_domain_models_expose_expected_defaults(self) -> None:
        identity = PromptReference(scenario_code="network_device_query", language="zh-CN", version="1.0.0")
        guardrail_request = GuardrailRequest(text="processed prompt")
        guardrail_result = GuardrailResult(passed=True)
        compliance_result = PromptComplianceResult(passed=False, stage="slot_validation")

        self.assertEqual(identity.scenario_code, "network_device_query")
        self.assertEqual(guardrail_request.text, "processed prompt")
        self.assertIsNone(guardrail_request.policy_id)
        self.assertEqual(guardrail_result.decision, GuardrailDecision.ALLOW)
        self.assertIsNone(guardrail_result.reason)
        self.assertIsNone(guardrail_result.provider)
        self.assertIsNone(guardrail_result.policy_id)
        self.assertIsNone(compliance_result.error_code)
        self.assertIsNone(compliance_result.extracted_slots)
        self.assertNotIn("notes", PromptComplianceResult.__dataclass_fields__)
        self.assertNotIn("confidence", PromptComplianceResult.__dataclass_fields__)

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
            providers={"google": {"api_key": "secret"}},
        )

        self.assertTrue(config.enabled)
        self.assertEqual(config.guardrail.config["mode"], "strict")
        self.assertEqual(config.guardrail.policy_id, "policy-a")
        self.assertEqual(config.guardrail.endpoint, "https://guardrail.example")
        self.assertEqual(config.guardrail.region, "us-central1")
        self.assertEqual(config.guardrail.credentials_ref, "GOOGLE_APPLICATION_CREDENTIALS")
        self.assertFalse(hasattr(config, "slot_schema"))
        self.assertEqual(config.providers["google"]["api_key"], "secret")


if __name__ == "__main__":
    unittest.main()
