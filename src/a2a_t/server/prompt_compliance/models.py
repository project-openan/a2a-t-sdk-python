from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field
from typing import Any

from a2a_t.server.prompt_compliance.config import (
    GuardrailProviderConfig,
    PromptComplianceConfig,
    SlotExtractionConfig,
    SlotSchemaConfig,
)


@dataclass
class PromptIdentity:
    """原始 Prompt 引用身份 / Identity for an original prompt reference."""

    name: str
    language: str
    version: str


class GuardrailDecision(str, Enum):
    """统一的护栏决策语义 / Unified guardrail decision semantics."""

    ALLOW = "allow"
    BLOCK = "block"
    MASK = "mask"
    REVIEW = "review"


@dataclass
class GuardrailRequest:
    """归一化后的护栏输入请求 / Normalized guardrail input request."""

    text: str
    metadata: dict[str, object] | None = None
    policy_id: str | None = None


@dataclass
class GuardrailResult:
    """安全护栏返回的归一化结果 / Normalized result returned by safety guardrails."""

    passed: bool
    decision: GuardrailDecision = GuardrailDecision.ALLOW
    category: str | None = None
    reason: str | None = None
    raw_response: dict[str, Any] | None = None
    provider: str | None = None
    policy_id: str | None = None


@dataclass
class SlotExtractionResult:
    """结构化槽位提取结果 / Structured slot extraction result."""

    slots: dict[str, Any]
    notes: list[str]
    confidence: float | None = None
    raw_response: dict[str, Any] | None = None


@dataclass
class PromptComplianceResult:
    """统一的遵从校验执行结果 / Unified compliance execution result."""

    passed: bool
    stage: str
    extracted_slots: dict[str, Any] | None = None
    notes: list[str] | None = None
    confidence: float | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class SlotValidationResult:
    """提取槽位的运行时校验结果 / Runtime validation result for extracted slots."""

    valid: bool
    errors: list[str] = field(default_factory=list)
