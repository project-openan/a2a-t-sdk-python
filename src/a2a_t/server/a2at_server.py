from __future__ import annotations

from pathlib import Path
from typing import Any

from a2a_t.config.models import A2ATConfig
from a2a_t.llm.config_loader import LLMConfigLoader
from a2a_t.llm.factory import LLMClientFactory
from a2a_t.negotiation.common.models import ContinueNegotiationInput, StartNegotiationInput

from .negotiation.negotiation_orchestrator_builder import ServerNegotiationOrchestratorBuilder
from .prompt_compliance.prompt_compliance_orchestrator_builder import PromptComplianceOrchestratorBuilder


def _default_env_path() -> Path:
    """Return the default .env path used by the high-level server."""
    return Path(__file__).resolve().parents[3] / "package_data" / ".env"


class A2ATServer:
    """Expose the server-side prompt compliance and negotiation APIs."""

    def __init__(
        self,
        *,
        env_path: Path | None = None,
        logger: Any | None = None,
    ) -> None:
        resolved_env_path = env_path or _default_env_path()
        config = A2ATConfig.load(resolved_env_path)
        llm_config = LLMConfigLoader.load(resolved_env_path)
        llm_client = LLMClientFactory.create(llm_config.provider, llm_config, logger=logger)
        self._prompt_compliance_orchestrator = PromptComplianceOrchestratorBuilder().build(
            config=config,
            llm_client=llm_client,
            logger=logger,
        )
        self._negotiation_orchestrator = ServerNegotiationOrchestratorBuilder().build(
            config=config,
            llm_client=llm_client,
            env_path=resolved_env_path,
            logger=logger,
        )

    def check_task_prompt(self, *, processed_prompt_text: str) -> dict[str, object]:
        """Validate a processed task prompt and return compliance details."""
        result = self._prompt_compliance_orchestrator.check(
            processed_prompt_text=processed_prompt_text,
        )
        payload: dict[str, object] = {"success": result.success}
        if result.failure is not None:
            payload["failure"] = result.failure
        return payload

    def start_negotiation(self, input: StartNegotiationInput) -> dict[str, object]:
        """Start a server-side negotiation round."""
        return self._negotiation_orchestrator.start_negotiation(input)

    def receive_negotiation(self, message: str, context: dict[str, object]) -> dict[str, object]:
        """Process a negotiation message received from the remote peer."""
        return self._negotiation_orchestrator.receive_negotiation(message, context)

    def continue_negotiation(self, input: ContinueNegotiationInput) -> dict[str, object]:
        """Continue an existing negotiation with a local response."""
        return self._negotiation_orchestrator.continue_negotiation(input)
