from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.config.models import PromptComplianceConfig


def test_prompt_compliance_config_from_mapping_reads_all_sections() -> None:
    values = {
        "A2AT_PROMPT_COMPLIANCE_ENABLED": "true",
        "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_PROVIDER": "custom_guardrail",
        "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_TIMEOUT_SECONDS": "11",
        "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_POLICY_ID": "projects/p1/locations/global/templates/template-1",
        "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_ENDPOINT": "modelarmor.googleapis.com",
        "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_REGION": "global",
        "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_CREDENTIALS_REF": "GOOGLE_APPLICATION_CREDENTIALS",
    }

    config = PromptComplianceConfig.from_mapping(values)

    assert config.enabled is True
    assert config.guardrail.provider == "custom_guardrail"
    assert config.guardrail.timeout == 11.0
    assert config.guardrail.policy_id == "projects/p1/locations/global/templates/template-1"
    assert config.guardrail.endpoint == "modelarmor.googleapis.com"
    assert config.guardrail.region == "global"
    assert config.guardrail.credentials_ref == "GOOGLE_APPLICATION_CREDENTIALS"
    assert not hasattr(config, "slot_schema")
    assert config.providers == {}


def test_prompt_compliance_config_from_mapping_uses_defaults() -> None:
    values: dict[str, str] = {}

    config = PromptComplianceConfig.from_mapping(values)

    assert config.enabled is False
    assert config.guardrail.provider == "noop"
    assert config.guardrail.timeout == 10.0
    assert config.guardrail.policy_id == ""
    assert config.guardrail.endpoint == ""
    assert config.guardrail.region == ""
    assert config.guardrail.credentials_ref == ""
    assert not hasattr(config, "slot_schema")
    assert config.providers == {}
