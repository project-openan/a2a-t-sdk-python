"""Prompt compliance runtime for server-side validation."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "PromptComplianceConfig",
    "PromptComplianceOrchestratorBuilder",
    "PromptComplianceOrchestrator",
    "PromptComplianceResult",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "PromptComplianceConfig": ("a2a_t.config.models", "PromptComplianceConfig"),
    "PromptComplianceOrchestratorBuilder": (
        "a2a_t.server.prompt_compliance.prompt_compliance_orchestrator_builder",
        "PromptComplianceOrchestratorBuilder",
    ),
    "PromptComplianceOrchestrator": (
        "a2a_t.server.prompt_compliance.prompt_compliance_orchestrator",
        "PromptComplianceOrchestrator",
    ),
    "PromptComplianceResult": ("a2a_t.server.prompt_compliance.result", "PromptComplianceResult"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(f"module 'a2a_t.server.prompt_compliance' has no attribute {name!r}") from error

    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
