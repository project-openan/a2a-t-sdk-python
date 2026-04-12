from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


def _parse_bool(raw_value: str | None, default: bool) -> bool:
    if raw_value is None or not raw_value.strip():
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_float(raw_value: str | None, default: float) -> float:
    if raw_value is None or not raw_value.strip():
        return default
    return float(raw_value)


def _parse_int(raw_value: str | None, default: int) -> int:
    if raw_value is None or not raw_value.strip():
        return default
    return int(raw_value)


@dataclass
class GuardrailProviderConfig:
    """安全护栏适配器的 provider 配置 / Provider configuration for safety guardrail adapters."""

    provider: str = ""
    timeout: float = 10.0
    policy_id: str = ""
    endpoint: str = ""
    region: str = ""
    credentials_ref: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class SlotExtractionConfig:
    """基于 LLM 的槽位提取配置 / Configuration for LLM-based slot extraction."""

    provider: str = ""
    model: str = ""
    timeout: float = 20.0
    temperature: float = 0.0
    max_retries: int = 2


@dataclass
class SlotSchemaConfig:
    """槽位 schema 文件定位配置 / Configuration for locating slot schema files."""

    root_dir: str = "./slots"
    slot_root_name: str = "slots"
    file_name: str = "slot.json"
    not_found_policy: str = "strict"


@dataclass
class PromptComplianceConfig:
    """Prompt 遵从校验顶层配置 / Top-level configuration for prompt compliance."""

    enabled: bool = False
    guardrail: GuardrailProviderConfig = field(default_factory=GuardrailProviderConfig)
    slot_extraction: SlotExtractionConfig = field(default_factory=SlotExtractionConfig)
    slot_schema: SlotSchemaConfig = field(default_factory=SlotSchemaConfig)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "PromptComplianceConfig":
        return cls(
            enabled=_parse_bool(values.get("A2AT_PROMPT_COMPLIANCE_ENABLED"), False),
            guardrail=GuardrailProviderConfig(
                provider=values.get("A2AT_PROMPT_COMPLIANCE_GUARDRAIL_PROVIDER", "noop") or "noop",
                timeout=_parse_float(
                    values.get("A2AT_PROMPT_COMPLIANCE_GUARDRAIL_TIMEOUT_SECONDS"),
                    10.0,
                ),
                policy_id=values.get("A2AT_PROMPT_COMPLIANCE_GUARDRAIL_POLICY_ID", "") or "",
                endpoint=values.get("A2AT_PROMPT_COMPLIANCE_GUARDRAIL_ENDPOINT", "") or "",
                region=values.get("A2AT_PROMPT_COMPLIANCE_GUARDRAIL_REGION", "") or "",
                credentials_ref=values.get("A2AT_PROMPT_COMPLIANCE_GUARDRAIL_CREDENTIALS_REF", "") or "",
            ),
            slot_extraction=SlotExtractionConfig(
                provider=values.get("A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_PROVIDER", "") or "",
                model=values.get("A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_MODEL", "") or "",
                timeout=_parse_float(
                    values.get("A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_TIMEOUT_SECONDS"),
                    30.0,
                ),
                temperature=_parse_float(
                    values.get("A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_TEMPERATURE"),
                    0.0,
                ),
                max_retries=_parse_int(
                    values.get("A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_MAX_RETRIES"),
                    2,
                ),
            ),
            slot_schema=SlotSchemaConfig(
                root_dir=values.get("A2AT_PROMPT_COMPLIANCE_SLOT_LOCAL_DIR", "./slots") or "./slots",
                slot_root_name="slots",
                file_name=values.get("A2AT_PROMPT_COMPLIANCE_SLOT_FILE_NAME", "slot.json") or "slot.json",
                not_found_policy=(
                    values.get("A2AT_PROMPT_COMPLIANCE_SLOT_NOT_FOUND_POLICY", "strict") or "strict"
                ),
            ),
        )
