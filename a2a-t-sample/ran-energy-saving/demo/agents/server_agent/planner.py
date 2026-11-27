"""LangGraph planner model and prompt of the RAN NMS execution agent.

The server executes an accepted Task with a ReAct-style LangGraph: the planner chooses the next
RAN NMS tool (and its parameters), the graph runs it, and the loop repeats until the planner stops. The
tools enforce the business preconditions, so a model cannot apply an unevaluated plan or generate a
report without a plan id even if it ignores the prompt.

The planner is the live ``ChatOpenAI`` model; the tools enforce the same order for every model.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from shared.config import SampleConfig

from demo import ran_data


#: Planner configuration error.
class PlannerError(RuntimeError):
    """Raised when the planner model cannot be built or a planner turn is invalid."""


def build_planner_model(
    config: SampleConfig,
    *,
    operation: str = "Create",
    existing_plan_id: str = "",
) -> BaseChatModel:
    """Build the planner model of the configured endpoint.

    Args:
        config: resolved sample configuration.

    Returns:
        The configured ``ChatOpenAI`` planner model.
    """
    from langchain_openai import ChatOpenAI

    if not config.planner_model:
        raise PlannerError("A2AT_SAMPLE_PLANNER_MODEL must be non-empty in live mode")
    kwargs: dict[str, Any] = {
        "model": config.planner_model,
        "timeout": config.planner_timeout_seconds,
        "max_retries": 0,
        "temperature": 0,
    }
    if config.planner_api_base:
        kwargs["base_url"] = config.planner_api_base
    if config.planner_api_key:
        kwargs["api_key"] = config.planner_api_key
    return ChatOpenAI(**kwargs)


def describe_dataset() -> str:
    """Return a compact catalog of the dataset cells for the planner prompt."""
    lines: list[str] = []
    for cell in ran_data.load_cells():
        windows = ", ".join(cell.available_windows)
        lines.append(
            f"- {cell.cell_id} ({cell.name}, {cell.region}): "
            f"max_saving_rate={cell.max_saving_rate:g}%, windows=[{windows}], "
            f"SLA floor={cell.capacity.sla_min_throughput_mbps:g} Mbps"
        )
    return "\n".join(lines)


def build_system_prompt(*, dataset_catalog: str | None = None) -> str:
    """Build the system prompt of the RAN NMS execution planner."""
    catalog = dataset_catalog if dataset_catalog is not None else describe_dataset()
    return (
        "You are the RAN NMS (network management system) execution agent. Execute the "
        "accepted energy-saving task using the "
        "provided tools.\n\n"
        "Dataset cells:\n"
        f"{catalog}\n\n"
        "Rules:\n"
        "1. Start with get_cell_energy_profile for the target cell.\n"
        "2. Call evaluate_energy_saving_plan before applying a plan; never apply an infeasible "
        "plan.\n"
        "3. Call apply_energy_saving_plan only for a plan that was evaluated successfully.\n"
        "4. Call generate_energy_saving_report last, with the exact plan_id returned by apply.\n"
        "5. If a tool returns ok=false, adjust the plan within the dataset limits and retry at "
        "most twice; if it still fails, stop and explain.\n"
        "6. The sample default plan is cell-001, window 00:00-06:00, target rate 30%. Prefer a "
        "dataset cell whose region matches the accepted Task-T area. You may choose another "
        "dataset cell/window/rate when the accepted task or tool results justify it; the tools "
        "reject an invalid order or an infeasible plan.\n"
        "7. For a planned-outage task (a site list plus a background event), prefer the cell whose "
        "available window covers the outage and keep its target rate within the cell's maximum "
        "saving rate.\n"
        "8. Do not invent cells, windows, rates, or plan ids.\n"
        "9. When the report is generated, return a short confirmation and stop calling tools.\n"
        "10. When the accepted Task-T operation type is Modify and an existing plan is provided, "
        "update that plan in place with modify_energy_saving_plan and keep its plan_id. Never call "
        "apply_energy_saving_plan for a Modify task and never allocate a second plan."
    )


def build_planner_input(
    task_prompt: str,
    negotiation_context: Mapping[str, object] | None,
    *,
    operation: str = "Create",
    existing_plan: Mapping[str, object] | None = None,
) -> str:
    """Build the human message that carries the accepted Task-T into the graph."""
    context_block = ""
    if negotiation_context:
        context_block = (
            "\nNegotiation context (accepted):\n"
            f"{json.dumps(negotiation_context, ensure_ascii=False, sort_keys=True)}\n"
        )
    modify_block = ""
    if operation.strip().lower() == "modify" and existing_plan is not None:
        modify_block = (
            "\nOperation: Modify. Update the existing plan in place and keep its plan_id; do not "
            "allocate a second plan.\n"
            "Existing plan to update:\n"
            f"{json.dumps(dict(existing_plan), ensure_ascii=False, sort_keys=True)}\n"
        )
    return (
        "Execute the accepted task.\n"
        f"Accepted Task-T:\n{task_prompt}\n"
        f"{context_block}{modify_block}\n"
        "Follow the tool rules and stop once the report is generated."
    )


__all__ = [
    "PlannerError",
    "build_planner_input",
    "build_planner_model",
    "build_system_prompt",
    "describe_dataset",
]
