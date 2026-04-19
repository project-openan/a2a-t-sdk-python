from __future__ import annotations

from pathlib import Path
from typing import Any

from a2a_t.config.models import A2ATConfig
from a2a_t.llm.client import LLMClient
from a2a_t.negotiation.common.models import ContinueNegotiationInput, StartNegotiationInput

from .negotiation.negotiation_orchestrator_builder import ClientNegotiationOrchestratorBuilder
from .prompt_generation.prompt_generation_orchestrator_builder import PromptGenerationOrchestratorBuilder


def _default_env_path() -> Path:
    return Path(__file__).resolve().parents[3] / "package_data" / ".env"


class A2ATClient:
    def __init__(
        self,
        *,
        env_path: Path | None = None,
        logger: Any | None = None,
    ) -> None:
        resolved_env_path = env_path or _default_env_path()
        config = A2ATConfig.load(resolved_env_path)
        llm_client = LLMClient(env_path=resolved_env_path)
        self._prompt_generation_orchestrator = PromptGenerationOrchestratorBuilder().build(
            config=config,
            llm_client=llm_client,
            logger=logger,
        )
        self._negotiation_orchestrator = ClientNegotiationOrchestratorBuilder().build(
            env_path=resolved_env_path,
            logger=logger,
        )

    def generate_task_prompt(self, user_input: str | dict[str, object]) -> object:
        return self._prompt_generation_orchestrator.generate(user_input)

    def start_negotiation(self, input: StartNegotiationInput) -> dict[str, object]:
        return self._negotiation_orchestrator.start_negotiation(input)

    def receive_negotiation(self, message: str, context: dict[str, object]) -> dict[str, object]:
        return self._negotiation_orchestrator.receive_negotiation(message, context)

    def continue_negotiation(self, input: ContinueNegotiationInput) -> dict[str, object]:
        return self._negotiation_orchestrator.continue_negotiation(input)
