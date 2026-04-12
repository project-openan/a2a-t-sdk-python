from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.config.models import SDKConfig
from a2a_t.server.prompt_compliance.errors import PromptComplianceError
from a2a_t.config.env import EnvConfig
from a2a_t.server.prompt_compliance.config import PromptComplianceConfig
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


def test_sdk_config_from_dict_builds_prompt_compliance_config() -> None:
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

    assert config.prompt_compliance.enabled is True
    assert config.prompt_compliance.guardrail.provider == "custom_guardrail"
    assert config.prompt_compliance.guardrail.timeout == 11
    assert config.prompt_compliance.slot_extraction.provider == "openai"
    assert config.prompt_compliance.slot_extraction.model == "gpt-4.1"
    assert config.prompt_compliance.slot_schema.not_found_policy == "skip"
    assert config.prompt_compliance.providers["openai"]["api_key"] == "openai-key"


def test_sdk_config_from_dict_uses_prompt_compliance_defaults() -> None:
    config = SDKConfig.from_dict({})

    assert isinstance(config.prompt_compliance, PromptComplianceConfig)
    assert config.prompt_compliance.enabled is False
    assert config.prompt_compliance.guardrail.provider == ""
    assert config.prompt_compliance.slot_extraction.max_retries == 2
    assert config.prompt_compliance.slot_schema.root_dir == "./slots"
    assert config.prompt_compliance.slot_schema.file_name == "slot.json"
    assert config.prompt_compliance.providers == {}


def test_prompt_compliance_config_from_env_is_available_from_config_module() -> None:
    config = PromptComplianceConfig.from_env(EnvConfig(values={}))

    assert config.guardrail.provider == "noop"
    assert config.slot_schema.root_dir == "./slots"
    assert config.slot_schema.file_name == "slot.json"
    assert config.slot_schema.not_found_policy == "strict"


def test_prompt_compliance_domain_models_expose_expected_defaults() -> None:
    identity = PromptIdentity(name="network_device_query", language="zh-CN", version="1.0.0")
    guardrail_request = GuardrailRequest(text="processed prompt")
    guardrail_result = GuardrailResult(passed=True)
    extraction_result = SlotExtractionResult(slots={"device_type": "router"}, notes=["from prompt"])
    compliance_result = PromptComplianceResult(passed=False, stage="slot_validation")

    assert identity.name == "network_device_query"
    assert guardrail_request.text == "processed prompt"
    assert guardrail_request.policy_id is None
    assert guardrail_result.decision == GuardrailDecision.ALLOW
    assert guardrail_result.reason is None
    assert guardrail_result.provider is None
    assert guardrail_result.policy_id is None
    assert extraction_result.confidence is None
    assert extraction_result.raw_response is None
    assert compliance_result.error_code is None
    assert compliance_result.extracted_slots is None


def test_prompt_compliance_error_keeps_machine_readable_context() -> None:
    error = PromptComplianceError("compliance failed", stage="slot_validation", code="missing_slot")

    assert str(error) == "compliance failed"
    assert error.context == {"stage": "slot_validation", "code": "missing_slot"}


def test_prompt_compliance_config_models_can_be_constructed_directly() -> None:
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

    assert config.enabled is True
    assert config.guardrail.config["mode"] == "strict"
    assert config.guardrail.policy_id == "policy-a"
    assert config.guardrail.endpoint == "https://guardrail.example"
    assert config.guardrail.region == "us-central1"
    assert config.guardrail.credentials_ref == "GOOGLE_APPLICATION_CREDENTIALS"
    assert config.slot_extraction.provider == "google"
    assert config.slot_schema.root_dir == "./cache"
    assert config.providers["google"]["api_key"] == "secret"


def test_old_prompt_compliance_provider_config_is_not_exported() -> None:
    import a2a_t.server.prompt_compliance as prompt_compliance

    assert hasattr(prompt_compliance, "GuardrailProviderConfig")
    assert not hasattr(prompt_compliance, "PromptComplianceProviderConfig")
