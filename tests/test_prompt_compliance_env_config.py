from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.config.env import EnvConfig
from a2a_t.server.prompt_compliance.config import PromptComplianceConfig


def test_prompt_compliance_config_from_env_reads_all_sections() -> None:
    env = EnvConfig(
        values={
            "A2AT_PROMPT_COMPLIANCE_ENABLED": "true",
            "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_PROVIDER": "custom_guardrail",
            "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_TIMEOUT_SECONDS": "11",
            "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_POLICY_ID": "projects/p1/locations/global/templates/template-1",
            "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_ENDPOINT": "modelarmor.googleapis.com",
            "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_REGION": "global",
            "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_CREDENTIALS_REF": "GOOGLE_APPLICATION_CREDENTIALS",
            "A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_PROVIDER": "openai",
            "A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_MODEL": "gpt-4.1",
            "A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_TIMEOUT_SECONDS": "22",
            "A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_TEMPERATURE": "0.1",
            "A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_MAX_RETRIES": "4",
            "A2AT_PROMPT_COMPLIANCE_SLOT_LOCAL_DIR": "./slots-runtime",
            "A2AT_PROMPT_COMPLIANCE_SLOT_FILE_NAME": "slot.json",
            "A2AT_PROMPT_COMPLIANCE_SLOT_NOT_FOUND_POLICY": "skip",
        }
    )

    config = PromptComplianceConfig.from_env(env)

    assert config.enabled is True
    assert config.guardrail.provider == "custom_guardrail"
    assert config.guardrail.timeout == 11.0
    assert config.guardrail.policy_id == "projects/p1/locations/global/templates/template-1"
    assert config.guardrail.endpoint == "modelarmor.googleapis.com"
    assert config.guardrail.region == "global"
    assert config.guardrail.credentials_ref == "GOOGLE_APPLICATION_CREDENTIALS"
    assert config.slot_extraction.provider == "openai"
    assert config.slot_extraction.model == "gpt-4.1"
    assert config.slot_extraction.timeout == 22.0
    assert config.slot_extraction.temperature == 0.1
    assert config.slot_extraction.max_retries == 4
    assert config.slot_schema.root_dir == "./slots-runtime"
    assert config.slot_schema.file_name == "slot.json"
    assert config.slot_schema.not_found_policy == "skip"


def test_prompt_compliance_config_from_env_uses_defaults() -> None:
    env = EnvConfig(values={})

    config = PromptComplianceConfig.from_env(env)

    assert config.enabled is False
    assert config.guardrail.provider == "noop"
    assert config.guardrail.timeout == 10.0
    assert config.guardrail.policy_id == ""
    assert config.guardrail.endpoint == ""
    assert config.guardrail.region == ""
    assert config.guardrail.credentials_ref == ""
    assert config.slot_extraction.provider == ""
    assert config.slot_extraction.model == ""
    assert config.slot_extraction.timeout == 30.0
    assert config.slot_extraction.temperature == 0.0
    assert config.slot_extraction.max_retries == 2
    assert config.slot_schema.root_dir == "./slots"
    assert config.slot_schema.file_name == "slot.json"
    assert config.slot_schema.not_found_policy == "strict"
