from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PromptComplianceProviderConfig:
    """Provider configuration shared by guardrail and provider registry entries."""

    provider: str = ""
    timeout: float = 10.0
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class SlotExtractionConfig:
    """Configuration for LLM-based slot extraction."""

    provider: str = ""
    model: str = ""
    timeout: float = 20.0
    temperature: float = 0.0
    max_retries: int = 2


@dataclass
class SlotSchemaConfig:
    """Configuration for locating slot schema files."""

    root_dir: str = "./cache"
    slot_root_name: str = "slots"
    file_name: str = "slot.yaml"
    not_found_policy: str = "strict"


@dataclass
class PromptComplianceConfig:
    """Top-level configuration for prompt compliance."""

    enabled: bool = False
    guardrail: PromptComplianceProviderConfig = field(default_factory=PromptComplianceProviderConfig)
    slot_extraction: SlotExtractionConfig = field(default_factory=SlotExtractionConfig)
    slot_schema: SlotSchemaConfig = field(default_factory=SlotSchemaConfig)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class PromptIdentity:
    """Identity for an original prompt reference."""

    name: str
    language: str
    version: str


@dataclass
class GuardrailResult:
    """Normalized result returned by safety guardrails."""

    passed: bool
    category: str | None = None
    reason: str | None = None
    raw_response: dict[str, Any] | None = None


@dataclass
class SlotExtractionResult:
    """Structured slot extraction result."""

    slots: dict[str, Any]
    notes: list[str]
    confidence: float | None = None
    raw_response: dict[str, Any] | None = None


@dataclass
class PromptComplianceResult:
    """Unified compliance execution result."""

    passed: bool
    stage: str
    extracted_slots: dict[str, Any] | None = None
    notes: list[str] | None = None
    confidence: float | None = None
    error_code: str | None = None
    error_message: str | None = None
