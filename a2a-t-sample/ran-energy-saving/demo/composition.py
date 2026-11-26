"""Composition root helpers that wire the demo assets into the core runtime.

Only this module (and the entry points that import it) may combine ``protocol`` with demo assets.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from protocol.client_runtime import MODE_CONFIRM, MODE_MANUAL, build_client_runtime
from protocol.pre_task_negotiation import TaskTPrecheck
from shared.config import DISCOVERY_NMS, INTENT_MODE_PROCESSED, SampleConfig
from shared.run_constants import PROTOCOL_A2A_T, PROTOCOL_PURE

_LOGGER = logging.getLogger("ran_energy_saving.composition")


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
    prepared_task_t: str | None = None,
    prepared_filled: Mapping[str, object] | None = None,
    pure_reply_source: str = "",
) -> Any:
    """Build one client runtime with the demo catalog."""
    from protocol.client_policy import ClientPolicy

    from demo.agents.client_agent.agent import build_client_agent
    from demo.catalog import DemoRunCatalog
    from demo.intent import build_demo_intent_processor

    resolved_config = replace(config, discovery=discovery)
    policy = ClientPolicy.from_config(config)

    def client_agent_factory(model: Any) -> Any:
        """Build the decision agent; the basis and policy are stated in the prompt."""
        return build_client_agent(model)

    if protocol == PROTOCOL_PURE:
        from demo.pure_confirmation import PureConfirmationAnswer

        if mode in (MODE_CONFIRM, MODE_MANUAL):
            # The pure hop has no protocol negotiation: the server asks with a plain text message
            # and the operator confirms the answer in the same card the A2A-T form uses.
            pure_answer = PureConfirmationAnswer(
                mode=mode,
                timeout_seconds=config.manual_decision_timeout_seconds,
            )
        elif pure_reply_source == "agent" or discovery == DISCOVERY_NMS:
            # The direct vendor hop has no OSS Agent to draft a recommendation, so automatic mode
            # negotiates itself: a deterministic plan when the request implies the value, otherwise
            # the decision agent, with the confirmation card as the operator fallback. A tester
            # agent run asks for the same agency on every direct hop, so the A2A and A2A-T rows
            # take their decisions from the same client without the process policy in between.
            from protocol.decision_provider import AdkDecisionProvider
            from shared.model_factory import build_traced_adk_model

            from demo.pure_auto import build_auto_pure_answer

            run_spec = DemoRunCatalog.from_config(config).spec(
                scenario, language=config.language
            )
            provider = AdkDecisionProvider(
                model=build_traced_adk_model(config),
                user_request=run_spec.user_request,
                session_db_path=config.client_db_path,
                agent_factory=client_agent_factory,
                policy=policy,
            )
            # Automatic mode has no operator to hand the question to. A value the client cannot
            # ground is asked of the NMS (which owns the inventory) and refused after three
            # unanswered questions; the pure answer object owns that budget.
            pure_answer = build_auto_pure_answer(
                provider,
                policy,
                request_text=run_spec.user_request,
                apply_client_policy=pure_reply_source != "agent",
            )
        else:
            from demo.pure_policy import pure_answer_from_recommendation

            def pure_answer_from_policy(event: dict[str, object]) -> str:
                return pure_answer_from_recommendation(event, config.language)

            pure_answer = pure_answer_from_policy
    else:
        pure_answer = None

    # The display-only intent layer is opt-in for A2A-T; in raw mode skip building its ADK model
    # entirely so the run pays neither the model turn nor the object construction.
    intent_processor_factory = (
        build_demo_intent_processor if config.intent_mode == INTENT_MODE_PROCESSED else None
    )

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
        client_agent_factory=client_agent_factory,
        pure_answer=pure_answer,
        intent_processor_factory=intent_processor_factory,
        prepared_task_t=prepared_task_t,
        prepared_filled=prepared_filled,
    )
