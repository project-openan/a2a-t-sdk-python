"""SDK runtime assembly of the corpus (port of the Java ``SdkRuntimeAssembler``).

Every component is produced by the production builders; the sole test seam is the recording client
injected through the ``llm_client`` parameter. The unified ``A2ATConfig`` is constructed directly
from the corpus test configuration (packaged zh-CN prompt resources, corpus attempt limit), so the
module never reads the production ``.env`` loading paths. A separate runtime is provided for each
-T extension the corpus exercises (Task-T / Negotiation-T / …).
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.client_apis import register_client_task_apis
from engine.config import CorpusEnvConfig
from engine.negotiation_apis import register_negotiation_apis
from engine.recorder import RecordingMeteredLlmClient
from engine.registry import ApiRegistry
from engine.server_apis import register_server_task_apis

from a2a_t.client.prompt_generation.prompt_generation_orchestrator import PromptGenerationOrchestrator
from a2a_t.client.prompt_generation.prompt_generation_orchestrator_builder import PromptGenerationOrchestratorBuilder
from a2a_t.config.models import (
    A2ATConfig,
    LlmRuntimeConfig,
    PromptRuntimeConfig,
)
from a2a_t.core.errors.input_limit import InputLimitConfig
from a2a_t.llm.factory import LLMClientFactory
from a2a_t.llm.models import LLMClientConfig
from a2a_t.negotiation.generation.builder import NegotiationGenerationOrchestratorBuilder
from a2a_t.negotiation.generation.orchestrator import NegotiationGenerationOrchestrator
from a2a_t.server.prompt_compliance.content_validator import InputLimitedContentValidator, build_content_validator

__all__ = [
    "NegotiationRuntime",
    "Runtime",
    "build_negotiation_registry",
    "build_task_registry",
    "negotiation_runtime",
    "task_runtime",
]

#: Prompt language fixed by the corpus (the packaged zh-CN resources serve the business payloads).
_LANGUAGE = "zh-CN"

#: Fixed LLM client-config entries the corpus bridge does not override.
_HISTORY_WINDOW = 10
_SESSION_MAX_TOTAL = 300
_SESSION_MAX_PER_PROVIDER = 100


def _new_recorder(env: CorpusEnvConfig) -> RecordingMeteredLlmClient:
    """Create the shared recording LLM client from the corpus config."""
    llm_client_config = LLMClientConfig(
        provider=env.provider,
        model=env.model,
        api_key=env.api_key,
        base_url=env.base_url,
        history_window=_HISTORY_WINDOW,
        max_tokens=env.max_tokens,
        temperature=env.temperature,
        timeout_seconds=env.timeout_seconds,
        session_max_total=_SESSION_MAX_TOTAL,
        session_max_per_provider=_SESSION_MAX_PER_PROVIDER,
    )
    real_client = LLMClientFactory.create(env.provider, llm_client_config)
    return RecordingMeteredLlmClient(real_client, env.model)


@dataclass(slots=True)
class Runtime:
    """Fully assembled Task-T runtime with the single shared recording LLM client."""

    recorder: RecordingMeteredLlmClient
    client_generation: PromptGenerationOrchestrator
    task_validator: InputLimitedContentValidator


def task_runtime() -> Runtime:
    """Assemble the Task-T phase-1 runtime against the real LLM endpoint of the corpus config."""
    env = CorpusEnvConfig.load()
    recorder = _new_recorder(env)

    config = A2ATConfig(
        prompt=PromptRuntimeConfig(language=_LANGUAGE, source_type="packaged"),
        input_limits=InputLimitConfig(),
        llm=LlmRuntimeConfig(max_attempts=env.max_attempts),
    )

    client_generation = PromptGenerationOrchestratorBuilder().build(config=config, llm_client=recorder)
    task_validator = build_content_validator(extension_name="Task-T", config=config, llm_client=recorder)

    return Runtime(recorder=recorder, client_generation=client_generation, task_validator=task_validator)


def build_task_registry(runtime: Runtime) -> ApiRegistry:
    """Register the Task-T phase-1 APIs into a fresh registry."""
    registry = ApiRegistry()
    register_client_task_apis(registry, runtime.client_generation)
    register_server_task_apis(registry, runtime.task_validator)
    return registry


@dataclass(slots=True)
class NegotiationRuntime:
    """Fully assembled Negotiation-T runtime with the single shared recording LLM client."""

    recorder: RecordingMeteredLlmClient
    orchestrator: NegotiationGenerationOrchestrator


def negotiation_runtime() -> NegotiationRuntime:
    """Assemble the Negotiation-T runtime against the real LLM endpoint of the corpus config."""
    env = CorpusEnvConfig.load()
    recorder = _new_recorder(env)

    orchestrator = NegotiationGenerationOrchestratorBuilder(
        language=_LANGUAGE,
        llm_client=recorder,
        max_attempts=env.max_attempts,
    ).build()

    return NegotiationRuntime(recorder=recorder, orchestrator=orchestrator)


def build_negotiation_registry(runtime: NegotiationRuntime) -> ApiRegistry:
    """Register the Negotiation-T phase-1 APIs into a fresh registry."""
    registry = ApiRegistry()
    register_negotiation_apis(registry, runtime.orchestrator)
    return registry
