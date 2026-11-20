"""Composition of the demo NMS planner graph for one accepted task."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from protocol.server_agent_spec import ServerAgentSpec
from shared.config import SampleConfig, load_config

from demo import ran_data
from demo.agents.server_agent.graph import build_execution_graph
from demo.agents.server_agent.langgraph_tools import (
    REPORT_TOOL_NAME,
    PlanGuard,
    build_execution_tools,
    tool_result_from_text,
)
from demo.agents.server_agent.planner import (
    build_planner_input,
    build_planner_model,
    build_system_prompt,
)


def build_server_agent(
    config: SampleConfig | None = None,
    *,
    task_prompt: str,
    negotiation_context: Mapping[str, object] | None,
    model: BaseChatModel | None = None,
    approval_gate: Any | None = None,
    operation: str = "Create",
    existing_plan: Mapping[str, object] | None = None,
) -> ServerAgentSpec:
    """Build one planner graph, its tools and the metadata the executor needs."""
    if existing_plan is not None:
        # The Task artifact is the durable source of the baseline plan; the plan store may have
        # been reset (a restart or a test). Rebuild it before the Modify tool reads it.
        ran_data.restore_plan(existing_plan)
    resolved = config or load_config()
    guard = PlanGuard()
    tools = build_execution_tools(guard, approval_gate=approval_gate)
    plan_id = str(existing_plan.get("plan_id") or "") if existing_plan is not None else ""
    planner_model = (
        model
        if model is not None
        else build_planner_model(
            resolved,
            operation=operation,
            existing_plan_id=plan_id,
        )
    )
    system_prompt = build_system_prompt()
    graph = build_execution_graph(
        model=planner_model,
        tools=tools,
        system_prompt=system_prompt,
    )
    user_content = build_planner_input(
        task_prompt,
        negotiation_context,
        operation=operation,
        existing_plan=existing_plan,
    )
    return ServerAgentSpec(
        graph=graph,
        model=planner_model,
        system_prompt=system_prompt,
        report_tool_name=REPORT_TOOL_NAME,
        initial_messages=[HumanMessage(content=user_content)],
        parse_tool_result=tool_result_from_text,
    )


def server_agent_factory(
    *,
    model_factory: Any | None = None,
) -> Any:
    """Return a factory with an optional model override (tests use a scripted model)."""

    def factory(
        config: SampleConfig | None,
        *,
        task_prompt: str,
        negotiation_context: Mapping[str, object] | None,
        approval_gate: Any | None = None,
        operation: str = "Create",
        existing_plan: Mapping[str, object] | None = None,
    ) -> ServerAgentSpec:
        model = model_factory() if model_factory is not None else None
        return build_server_agent(
            config,
            task_prompt=task_prompt,
            negotiation_context=negotiation_context,
            model=model,
            approval_gate=approval_gate,
            operation=operation,
            existing_plan=existing_plan,
        )

    return factory
