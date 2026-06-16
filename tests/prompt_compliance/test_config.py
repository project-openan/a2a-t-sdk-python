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
    }

    config = PromptComplianceConfig.from_mapping(values)

    assert config.enabled is True
    assert config.providers == {}


def test_prompt_compliance_config_from_mapping_uses_defaults() -> None:
    values: dict[str, str] = {}

    config = PromptComplianceConfig.from_mapping(values)

    assert config.enabled is False
    assert config.providers == {}
