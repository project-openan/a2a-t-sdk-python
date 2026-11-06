"""Composition root helpers that wire the demo assets into the core runtime.

Only this module (and the entry points that import it) may combine ``protocol`` with demo assets.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from protocol.client_runtime import build_client_runtime
from protocol.pre_task_negotiation import TaskTPrecheck
from protocol.run_spec import RunSpec
from shared.config import SampleConfig
from shared.run_constants import PROTOCOL_A2A_T, PROTOCOL_PURE


def offline_model_factory(run_spec: RunSpec) -> Any:
    """Build the deterministic ADK model carrying one run's scripted decisions."""
    from demo.adk_fake_model import build_client_script, build_fake_model

    return build_fake_model((build_client_script(run_spec.scripted_decisions),))


def build_demo_negotiation_policy() -> Any:
    """Return the negotiation policy bound to the demonstration plan evaluator."""
    from functools import partial

    from protocol.negotiation_policy import decide

    from demo.plan_evaluator import RanDataPlanEvaluator

    return partial(decide, evaluator=RanDataPlanEvaluator())


def build_demo_task_t_precheck() -> TaskTPrecheck:
    """Return the demo Task-T precheck injected into the pre-task negotiation handler."""
    from demo.task_t_precheck import RanDataTaskTPrecheck

    return RanDataTaskTPrecheck()


def install_offline_provider() -> None:
    """Install the deterministic SDK structured-call routing used in offline mode."""
    from demo.scripted_llm import register_scripted_provider
    from demo.traced_scripted import register_traced_scripted_provider

    register_scripted_provider()
    register_traced_scripted_provider()


async def build_demo_runtime(
    config: SampleConfig,
    scenario: str,
    mode: str,
    transport: str,
    discovery: str,
    *,
    protocol: str = PROTOCOL_A2A_T,
    run_id: str = "",
    env_path: Path | None = None,
    trace_sink: Callable[[dict[str, object]], None] | None = None,
) -> Any:
    """Build one client runtime with the demo catalog and offline assets."""
    from demo.agents.client_agent.agent import build_client_agent
    from demo.catalog import DemoRunCatalog
    from demo.intent import build_demo_intent_processor
    from demo.pure_policy import pure_answer_from_event

    resolved_config = replace(config, discovery=discovery)
    return await build_client_runtime(
        resolved_config,
        scenario,
        mode,
        transport,
        protocol=protocol,
        # The scenario profile follows the deployment (the .env that selects the vendor NMS), not
        # the engineer client's own discovery: in pure + NMS the UI talks to the local OSS Agent
        # while the OSS Agent talks to the real NMS.
        catalog=DemoRunCatalog.from_config(config),
        run_id=run_id,
        env_path=env_path,
        trace_sink=trace_sink,
        offline_model_factory=offline_model_factory,
        install_offline_provider=install_offline_provider,
        client_agent_factory=build_client_agent,
        pure_answer=(
            (lambda event: pure_answer_from_event(event, config.language)) if protocol == PROTOCOL_PURE else None
        ),
        intent_processor_factory=build_demo_intent_processor,
    )
