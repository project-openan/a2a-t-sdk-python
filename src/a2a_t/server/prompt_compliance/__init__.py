"""服务端 Prompt 遵从校验相关模型与异常 / Prompt compliance models and errors for server-side validation."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "AdapterSafetyGuardrail",
    "GoogleModelArmorGateway",
    "GoogleModelArmorGuardrailAdapter",
    "GuardrailAdapter",
    "GuardrailDecision",
    "GuardrailExecutionError",
    "GuardrailProviderConfig",
    "GuardrailRequest",
    "GuardrailResult",
    "ProcessedPromptParseError",
    "ProcessedPromptParser",
    "PromptComplianceConfig",
    "PromptComplianceError",
    "PromptComplianceResult",
    "PromptIdentity",
    "PromptSlotExtractor",
    "SafetyGuardrail",
    "SafetyGuardrailFactory",
    "SlotExtractionConfig",
    "SlotExtractionError",
    "SlotExtractionResult",
    "SlotSchemaConfig",
    "SlotSchemaLoadError",
    "SlotSchemaValidationError",
    "SlotValidationError",
    "SlotValidationResult",
    "PromptComplianceService",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "AdapterSafetyGuardrail": ("a2a_t.prompt.validation.guardrails", "AdapterSafetyGuardrail"),
    "GoogleModelArmorGateway": ("a2a_t.prompt.validation.guardrail_providers", "GoogleModelArmorGateway"),
    "GoogleModelArmorGuardrailAdapter": (
        "a2a_t.prompt.validation.guardrail_providers",
        "GoogleModelArmorGuardrailAdapter",
    ),
    "GuardrailAdapter": ("a2a_t.prompt.validation.guardrails", "GuardrailAdapter"),
    "GuardrailDecision": ("a2a_t.server.prompt_compliance.models", "GuardrailDecision"),
    "GuardrailExecutionError": ("a2a_t.server.prompt_compliance.errors", "GuardrailExecutionError"),
    "GuardrailProviderConfig": ("a2a_t.server.prompt_compliance.config", "GuardrailProviderConfig"),
    "GuardrailRequest": ("a2a_t.server.prompt_compliance.models", "GuardrailRequest"),
    "GuardrailResult": ("a2a_t.server.prompt_compliance.models", "GuardrailResult"),
    "ProcessedPromptParseError": ("a2a_t.server.prompt_compliance.errors", "ProcessedPromptParseError"),
    "ProcessedPromptParser": ("a2a_t.server.prompt_compliance.parser", "ProcessedPromptParser"),
    "PromptComplianceConfig": ("a2a_t.server.prompt_compliance.config", "PromptComplianceConfig"),
    "PromptComplianceError": ("a2a_t.server.prompt_compliance.errors", "PromptComplianceError"),
    "PromptComplianceResult": ("a2a_t.server.prompt_compliance.models", "PromptComplianceResult"),
    "PromptComplianceService": ("a2a_t.server.prompt_compliance.service", "PromptComplianceService"),
    "PromptIdentity": ("a2a_t.server.prompt_compliance.models", "PromptIdentity"),
    "PromptSlotExtractor": ("a2a_t.server.prompt_compliance.extractor", "PromptSlotExtractor"),
    "SafetyGuardrail": ("a2a_t.prompt.validation.guardrails", "SafetyGuardrail"),
    "SafetyGuardrailFactory": ("a2a_t.prompt.validation.guardrails", "SafetyGuardrailFactory"),
    "SlotExtractionConfig": ("a2a_t.server.prompt_compliance.config", "SlotExtractionConfig"),
    "SlotExtractionError": ("a2a_t.server.prompt_compliance.errors", "SlotExtractionError"),
    "SlotExtractionResult": ("a2a_t.server.prompt_compliance.models", "SlotExtractionResult"),
    "SlotSchemaConfig": ("a2a_t.server.prompt_compliance.config", "SlotSchemaConfig"),
    "SlotSchemaLoadError": ("a2a_t.server.prompt_compliance.errors", "SlotSchemaLoadError"),
    "SlotSchemaValidationError": ("a2a_t.server.prompt_compliance.errors", "SlotSchemaValidationError"),
    "SlotValidationError": ("a2a_t.server.prompt_compliance.errors", "SlotValidationError"),
    "SlotValidationResult": ("a2a_t.server.prompt_compliance.models", "SlotValidationResult"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(f"module 'a2a_t.server.prompt_compliance' has no attribute {name!r}") from error

    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
