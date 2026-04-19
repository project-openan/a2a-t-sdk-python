"""Prompt validation middleware for A2A server."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from a2a_t.config.models import A2ATConfig
from a2a_t.llm.client import LLMClient

from .prompt_compliance.prompt_compliance_orchestrator_builder import PromptComplianceOrchestratorBuilder


def _default_env_path() -> Path:
    return Path(__file__).resolve().parents[3] / "package_data" / ".env"


class PromptHandler:
    """Middleware for validating prompt requests."""

    def __init__(
        self,
        validator: Any | None = None,
        *,
        validator_builder: PromptComplianceOrchestratorBuilder | None = None,
        env_path: Path | None = None,
        llm_client: LLMClient | None = None,
        resource_root: str | Path | None = None,
    ) -> None:
        if validator is not None:
            self._validator = validator
            return

        if validator_builder is None and env_path is None and llm_client is None and resource_root is None:
            self._validator = None
            return

        resolved_env_path = env_path or _default_env_path()
        config = A2ATConfig.load(resolved_env_path)
        resolved_llm_client = llm_client or LLMClient(env_path=resolved_env_path)
        resolved_builder = validator_builder or PromptComplianceOrchestratorBuilder()
        self._validator = resolved_builder.build(
            config=config,
            llm_client=resolved_llm_client,
            resource_root=resource_root,
        )

    def check_task_prompt(
        self,
        *,
        task_id: str,
        processed_prompt_text: str,
    ) -> dict[str, Any]:
        """Check task prompt and return semantic validation result."""
        if self._validator is None:
            return {
                "passed": True,
                "need_negotiation": False,
                "negotiation_input": None,
                "stage": "passed",
                "extracted_slots": None,
                "error_code": None,
                "error_message": None,
            }

        result = self._validator.check(
            processed_prompt_text=processed_prompt_text,
            request_metadata={"task_id": task_id},
        )
        return {
            "passed": result.passed,
            "need_negotiation": result.need_negotiation,
            "negotiation_input": result.negotiation_input,
            "stage": result.stage,
            "extracted_slots": result.extracted_slots,
            "error_code": result.error_code,
            "error_message": result.error_message,
        }
