from __future__ import annotations

from pathlib import Path
from typing import Any

from a2a_t.config.models import A2ATConfig
from a2a_t.llm.client import LLMClient
from a2a_t.negotiation.common.models import ContinueNegotiationInput, StartNegotiationInput

from .negotiation.negotiation_orchestrator_builder import ServerNegotiationOrchestratorBuilder
from .prompt_compliance.prompt_compliance_orchestrator_builder import PromptComplianceOrchestratorBuilder


def _default_env_path() -> Path:
    return Path(__file__).resolve().parents[3] / "package_data" / ".env"


class A2ATServer:
    def __init__(
        self,
        *,
        env_path: Path | None = None,
        logger: Any | None = None,
    ) -> None:
        resolved_env_path = env_path or _default_env_path()
        config = A2ATConfig.load(resolved_env_path)
        llm_client = LLMClient(env_path=resolved_env_path)
        self._prompt_compliance_orchestrator = PromptComplianceOrchestratorBuilder().build(
            config=config,
            llm_client=llm_client,
        )
        self._negotiation_orchestrator = ServerNegotiationOrchestratorBuilder().build(
            config=config,
            llm_client=llm_client,
            env_path=resolved_env_path,
            logger=logger,
        )

    def check_task_prompt(self, *, processed_prompt_text: str) -> dict[str, object]:
        result = self._prompt_compliance_orchestrator.check(
            processed_prompt_text=processed_prompt_text,
            request_metadata=None,
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

    def start_negotiation(self, input: StartNegotiationInput) -> dict[str, object]:
        return self._negotiation_orchestrator.start_negotiation(input)

    def receive_negotiation(self, message: str, conetxt: dict[str, object]) -> dict[str, object]:
        return self._negotiation_orchestrator.receive_negotiation(message, conetxt)

    def continue_negotiation(self, input: ContinueNegotiationInput) -> dict[str, object]:
        return self._negotiation_orchestrator.continue_negotiation(input)
