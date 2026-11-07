"""ADK client decision agent of the RAN energy-saving sample.

``root_agent`` is loadable by ``adk run`` / ``adk web``. The model comes from
:func:`shared.model_factory.build_adk_model`, so the agent is deterministic and offline by default
and uses the configured OpenAI-compatible endpoint in ``live`` mode.

The agent's structured output is :class:`shared.models.NegotiationDecision`: ADK parses the model
response into that schema, so the deterministic client flow never parses free text.
"""

from __future__ import annotations

from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from shared.model_factory import build_adk_model
from shared.models import NegotiationDecision

from demo.adk_fake_model import build_fake_model
from demo.agents.client_agent.tools import evaluate_proposal_impact, get_sla_policy

#: Agent name; the offline fake model keys its client script on this name.
AGENT_NAME = "ran_energy_request_agent"

#: Location of the agent instruction.
INSTRUCTION_PATH = Path(__file__).resolve().parent / "prompts" / "request_instruction.md"

#: Instruction served to the ADK model.
REQUEST_INSTRUCTION = INSTRUCTION_PATH.read_text(encoding="utf-8")


def build_client_agent(model: BaseLlm | None = None) -> LlmAgent:
    """Build the RAN energy-saving client decision agent.

    Args:
        model: Model to use; defaults to :func:`shared.model_factory.build_adk_model` (the fake
            model in offline mode, ``LiteLlm`` in live mode).
    """
    return LlmAgent(
        name=AGENT_NAME,
        model=(
            model
            if model is not None
            else build_adk_model(offline_model_factory=build_fake_model)
        ),
        description=(
            "Decides how the client answers a RAN energy-saving negotiation proposal: accept, "
            "reject, abort, or counter-propose a plan that keeps the SLA."
        ),
        instruction=REQUEST_INSTRUCTION,
        tools=[get_sla_policy, evaluate_proposal_impact],
        output_schema=NegotiationDecision,
    )


def __getattr__(name: str) -> LlmAgent:
    """Build ``root_agent`` lazily for ``adk run`` / ``adk web``.

    Importing this module must not create an ADK model: the client runtime builds its own traced
    model, and in live mode the eager top-level construction read the ambient ``.env`` for a second,
    unused ``LiteLlm``. ADK's loader only needs the attribute when it actually loads the agent, so
    module ``__getattr__`` (PEP 562) keeps both entry points working; the value is cached.
    """
    if name != "root_agent":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    agent = build_client_agent()
    globals()["root_agent"] = agent
    return agent
