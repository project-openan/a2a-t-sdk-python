from __future__ import annotations

from pathlib import Path
from typing import Any

from a2a_t.config.models import A2ATConfig
from a2a_t.llm.config_loader import LLMConfigLoader
from a2a_t.llm.factory import LLMClientFactory
from a2a_t.negotiation.common.models import ContinueNegotiationInput, StartNegotiationInput

from .negotiation.negotiation_orchestrator_builder import ClientNegotiationOrchestratorBuilder
from .prompt_generation.models import PromptGenerationResult
from .prompt_generation.prompt_generation_orchestrator_builder import PromptGenerationOrchestratorBuilder


def _default_env_path() -> Path:
    """Return the default .env path used by the high-level client."""
    return Path(__file__).resolve().parents[3] / "package_data" / ".env"


class A2ATClient:
    """Expose the client-side prompt generation and negotiation APIs."""

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
        self._prompt_generation_orchestrator = PromptGenerationOrchestratorBuilder().build(
            config=config,
            llm_client=llm_client,
            logger=logger,
        )
        self._negotiation_orchestrator = ClientNegotiationOrchestratorBuilder().build(
            env_path=resolved_env_path,
            logger=logger,
        )

    def generate_task_prompt(self, user_input: str | dict[str, object]) -> PromptGenerationResult:
        """Generate a processed task prompt from user input."""
        return self._prompt_generation_orchestrator.generate(user_input)

    def start_negotiation(self, input: StartNegotiationInput) -> dict[str, object]:
        """Start a client-side negotiation round."""
        return self._negotiation_orchestrator.start_negotiation(input)

    def receive_negotiation(self, message: str, context: dict[str, object]) -> dict[str, object]:
        """Process a negotiation message received from the remote peer."""
        return self._negotiation_orchestrator.receive_negotiation(message, context)

    def continue_negotiation(self, input: ContinueNegotiationInput) -> dict[str, object]:
        """Continue an existing negotiation with a local response."""
        return self._negotiation_orchestrator.continue_negotiation(input)
