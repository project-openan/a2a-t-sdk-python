"""Tests of the LangGraph planner, its guarded tools and the graph loop."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from demo import ran_data
from demo.agents.server_agent.graph import build_execution_graph
from demo.agents.server_agent.langgraph_tools import (
    PlanGuard,
    build_execution_tools,
    tool_result_from_text,
)
from demo.agents.server_agent.planner import (
    ScriptedChatModel,
    build_planner_input,
    build_planner_model,
    build_system_prompt,
    describe_dataset,
)
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from shared.config import MODEL_MODE_LIVE, SampleConfig


async def _run_graph(config: SampleConfig | None = None) -> tuple[PlanGuard, list[object]]:
    """Run the scripted planner over the real guarded tools."""
    guard = PlanGuard()
    model = ScriptedChatModel(dataset_date=ran_data.dataset_date())
    graph = build_execution_graph(
        model=model,
        tools=build_execution_tools(guard),
        system_prompt=build_system_prompt(),
    )
    messages: list[object] = []
    async for update in graph.astream(
        {"messages": [HumanMessage(content=build_planner_input("Create a task.", None))]},
        config={"recursion_limit": 12},
        stream_mode="updates",
    ):
        for node_update in update.values():
            messages.extend(node_update.get("messages", []))
    del config
    return guard, messages


async def test_scripted_planner_runs_the_four_tools_in_order() -> None:
    ran_data.reset()
    try:
        guard, messages = await _run_graph()
    finally:
        ran_data.reset()

    assert guard.tool_calls == [
        "get_cell_energy_profile",
        "evaluate_energy_saving_plan",
        "apply_energy_saving_plan",
        "generate_energy_saving_report",
    ]
    assert guard.report is not None
    assert "summary" in guard.report
    # 4 tool turns + 1 final answer.
    assert len([message for message in messages if type(message).__name__ == "AIMessage"]) == 5


async def test_tool_guard_rejects_evaluate_before_profile() -> None:
    guard = PlanGuard()
    tools = {tool.name: tool for tool in build_execution_tools(guard)}
    result = tool_result_from_text(
        await tools["evaluate_energy_saving_plan"].ainvoke(
            {"cell_id": "cell-001", "window": "00:00~06:00", "target_rate": "30"}
        )
    )
    assert result["ok"] is False
    assert result["error_code"] == "tool_order_violation"


async def test_tool_guard_rejects_apply_before_evaluation() -> None:
    guard = PlanGuard()
    tools = {tool.name: tool for tool in build_execution_tools(guard)}
    await tools["get_cell_energy_profile"].ainvoke(
        {"cell_id": "cell-001", "date": ran_data.dataset_date()}
    )
    result = tool_result_from_text(
        await tools["apply_energy_saving_plan"].ainvoke(
            {"cell_id": "cell-001", "window": "00:00~06:00", "target_rate": "30"}
        )
    )
    assert result["ok"] is False
    assert result["error_code"] == "tool_order_violation"


async def test_tool_guard_rejects_report_before_apply() -> None:
    guard = PlanGuard()
    tools = {tool.name: tool for tool in build_execution_tools(guard)}
    await tools["get_cell_energy_profile"].ainvoke(
        {"cell_id": "cell-001", "date": ran_data.dataset_date()}
    )
    await tools["evaluate_energy_saving_plan"].ainvoke(
        {"cell_id": "cell-001", "window": "00:00~06:00", "target_rate": "30"}
    )
    result = tool_result_from_text(
        await tools["generate_energy_saving_report"].ainvoke(
            {"plan_id": "plan-cell-001-001"}
        )
    )
    assert result["ok"] is False
    assert result["error_code"] == "tool_order_violation"


async def test_tool_guard_accepts_the_full_sequence() -> None:
    ran_data.reset()
    try:
        guard = PlanGuard()
        tools = {tool.name: tool for tool in build_execution_tools(guard)}
        profile = tool_result_from_text(
            await tools["get_cell_energy_profile"].ainvoke(
                {"cell_id": "cell-001", "date": ran_data.dataset_date()}
            )
        )
        assert profile["ok"] is True
        evaluation = tool_result_from_text(
            await tools["evaluate_energy_saving_plan"].ainvoke(
                {"cell_id": "cell-001", "window": "00:00~06:00", "target_rate": "30%"}
            )
        )
        assert evaluation["ok"] is True
        applied = tool_result_from_text(
            await tools["apply_energy_saving_plan"].ainvoke(
                {"cell_id": "cell-001", "window": "00:00~06:00", "target_rate": "30"}
            )
        )
        assert applied["ok"] is True
        report = tool_result_from_text(
            await tools["generate_energy_saving_report"].ainvoke(
                {"plan_id": applied["plan_id"]}
            )
        )
        assert report["ok"] is True
        assert guard.applied is not None
        assert guard.report is not None
    finally:
        ran_data.reset()


async def test_tool_guard_allows_one_apply_per_task() -> None:
    """A live planner cannot open a second approval gate by exploring another plan."""
    ran_data.reset()
    try:
        guard = PlanGuard()
        gates: list[tuple[str, str, str]] = []

        async def approval_gate(
            cell_id: str,
            window: str,
            target_rate: str,
            evaluation: dict[str, object] | None,
        ) -> bool:
            del evaluation
            gates.append((cell_id, window, target_rate))
            return True

        tools = {tool.name: tool for tool in build_execution_tools(guard, approval_gate=approval_gate)}
        for call, args in (
            ("get_cell_energy_profile", {"cell_id": "cell-001", "date": ran_data.dataset_date()}),
            ("evaluate_energy_saving_plan", {"cell_id": "cell-001", "window": "00:00~06:00", "target_rate": "30"}),
            ("apply_energy_saving_plan", {"cell_id": "cell-001", "window": "00:00~06:00", "target_rate": "30"}),
        ):
            result = tool_result_from_text(await tools[call].ainvoke(args))
            assert result["ok"] is True
        assert len(gates) == 1

        # The planner re-reads the same cell and evaluates a second candidate. Applying it must be
        # rejected before the human gate; the first plan is still the one to report.
        for call, args in (
            ("get_cell_energy_profile", {"cell_id": "cell-001", "date": ran_data.dataset_date()}),
            ("evaluate_energy_saving_plan", {"cell_id": "cell-001", "window": "00:00~06:00", "target_rate": "25"}),
        ):
            assert tool_result_from_text(await tools[call].ainvoke(args))["ok"] is True
        second = tool_result_from_text(
            await tools["apply_energy_saving_plan"].ainvoke(
                {"cell_id": "cell-001", "window": "00:00~06:00", "target_rate": "25"}
            )
        )
        assert second["ok"] is False
        assert second["error_code"] == "tool_order_violation"
        assert len(gates) == 1
    finally:
        ran_data.reset()


async def test_tool_guard_keeps_a_profile_per_cell() -> None:
    """Reading another cell does not invalidate the cell being evaluated.

    The live planner reads every dataset cell before it evaluates one of them. Keeping only the
    last profile made ``evaluate`` reject a valid cell-001 plan after cell-002 was read, so the
    planner retried until it hit the step limit.
    """
    ran_data.reset()
    try:
        guard = PlanGuard()
        tools = {tool.name: tool for tool in build_execution_tools(guard)}
        date = ran_data.dataset_date()
        for cell_id in ("cell-001", "cell-002"):
            profile = tool_result_from_text(
                await tools["get_cell_energy_profile"].ainvoke({"cell_id": cell_id, "date": date})
            )
            assert profile["ok"] is True
        for cell_id, window, rate in (
            ("cell-001", "00:00~06:00", "30%"),
            ("cell-002", "01:00~05:00", "20%"),
        ):
            evaluation = tool_result_from_text(
                await tools["evaluate_energy_saving_plan"].ainvoke(
                    {"cell_id": cell_id, "window": window, "target_rate": rate}
                )
            )
            assert evaluation["ok"] is True
        # The candidate evaluated last is cell-002; applying the first one must still work.
        applied = tool_result_from_text(
            await tools["apply_energy_saving_plan"].ainvoke(
                {"cell_id": "cell-001", "window": "00:00~06:00", "target_rate": "30"}
            )
        )
        assert applied["ok"] is True
    finally:
        ran_data.reset()


def test_build_planner_model_offline_returns_the_scripted_model(
    offline_config: SampleConfig,
) -> None:
    model = build_planner_model(offline_config)
    assert isinstance(model, ScriptedChatModel)
    assert model.dataset_date == ran_data.dataset_date()


def test_build_planner_model_live_builds_chat_openai(offline_config: SampleConfig) -> None:
    config = replace(
        offline_config,
        model_mode=MODEL_MODE_LIVE,
        planner_model="mass/volcengine/deepseek-v4.1-flash",
        planner_api_base="http://127.0.0.1:31943/v1",
        planner_api_key="not-needed",
    )
    model = build_planner_model(config)
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "mass/volcengine/deepseek-v4.1-flash"


def test_system_prompt_describes_the_dataset_and_tool_rules(offline_config: SampleConfig) -> None:
    del offline_config
    catalog = describe_dataset()
    prompt = build_system_prompt(dataset_catalog=catalog)
    assert "cell-001" in prompt
    assert "cell-002" in prompt
    assert "get_cell_energy_profile" in prompt
    assert "generate_energy_saving_report" in prompt
    assert "plan_id" in prompt


def test_scripted_model_reads_the_plan_id_from_the_apply_result() -> None:
    model = ScriptedChatModel(dataset_date=ran_data.dataset_date())
    assert asyncio.run(_turn_count(model)) == 5


async def _turn_count(model: ScriptedChatModel) -> int:
    """Smoke the scripted model through one graph and return its tool-call count."""
    guard = PlanGuard()
    graph = build_execution_graph(
        model=model,
        tools=build_execution_tools(guard),
        system_prompt=build_system_prompt(),
    )
    count = 0
    async for update in graph.astream(
        {"messages": [HumanMessage(content=build_planner_input("Create a task.", None))]},
        config={"recursion_limit": 12},
        stream_mode="updates",
    ):
        for node_update in update.values():
            for message in node_update.get("messages", []):
                if type(message).__name__ == "AIMessage":
                    count += 1
    assert guard.report is not None
    return count
