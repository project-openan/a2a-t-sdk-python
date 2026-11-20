"""ADK intent agent of the RAN energy-saving sample.

``root_agent`` is loadable by ``adk run`` / ``adk web``. The model comes from
:func:`shared.model_factory.build_adk_model`, so the agent is deterministic and offline by default
and uses the configured OpenAI-compatible endpoint in ``live`` mode.

The structured output is :class:`BusinessIntent`: ADK parses the model response into that schema,
so the sample never reads business fields out of free text.
"""

from __future__ import annotations

from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from shared.model_factory import build_adk_model

from demo.adk_fake_model import build_fake_model
from demo.agents.intent_agent.schema import BusinessIntent

#: Agent name; it is also the marker the deterministic model scripts are keyed on.
INTENT_AGENT_NAME = "ran_energy_intent_agent"

#: Location of the agent instruction.
INSTRUCTION_PATH = Path(__file__).resolve().parent / "prompts" / "intent_instruction.md"

#: Instruction served to the ADK model.
INTENT_INSTRUCTION = INSTRUCTION_PATH.read_text(encoding="utf-8")


def build_intent_agent(model: BaseLlm | None = None) -> LlmAgent:
    """Build the RAN energy-saving intent agent.

    Args:
        model: Model to use; defaults to :func:`shared.model_factory.build_adk_model` (the fake
            model in offline mode, ``LiteLlm`` in live mode).
    """
    return LlmAgent(
        name=INTENT_AGENT_NAME,
        model=(
            model
            if model is not None
            else build_adk_model(offline_model_factory=build_fake_model)
        ),
        description="Reads one operator request into the RAN energy-saving business fields.",
        instruction=INTENT_INSTRUCTION,
        output_schema=BusinessIntent,
    )


def __getattr__(name: str) -> LlmAgent:
    """Build ``root_agent`` lazily for ``adk run`` / ``adk web``."""
    if name != "root_agent":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    agent = build_intent_agent()
    globals()["root_agent"] = agent
    return agent
