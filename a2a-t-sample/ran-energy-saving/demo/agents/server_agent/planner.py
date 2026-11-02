"""LangGraph planner model and prompt of the RAN NMS execution agent.

The server executes an accepted Task with a ReAct-style LangGraph: the planner chooses the next
RAN NMS tool (and its parameters), the graph runs it, and the loop repeats until the planner stops. The
tools enforce the business preconditions, so a model cannot apply an unevaluated plan or generate a
report without a plan id even if it ignores the prompt.

Offline mode uses :class:`ScriptedChatModel`, a deterministic chat model that emits the sample's four
tool calls in order. It runs through the **same graph** as the live ``ChatOpenAI`` planner, which
keeps the offline acceptance path deterministic without a second execution engine.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from langchain_core.language_models.base import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import PrivateAttr
from shared.config import SampleConfig

from demo import ran_data


#: Planner configuration error.
class PlannerError(RuntimeError):
    """Raised when the planner model cannot be built or a scripted turn is invalid."""


class ScriptedChatModel(BaseChatModel):
    """Deterministic chat model that emits the sample's four RAN NMS tool calls in order.

    The model is stateless with respect to the graph messages except for its turn counter; the
    ``generate_energy_saving_report`` call reads the ``plan_id`` from the preceding
    ``apply_energy_saving_plan`` tool result, so the script follows the real tool output instead of
    guessing an id.
    """

    dataset_date: str = ""
    _turn: int = PrivateAttr(default=0)
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    @property
    def _llm_type(self) -> str:
        """Return the model family name shown by LangChain."""
        return "scripted-ran-planner"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        """Accept tool binding (the scripted model already knows the four tool names)."""
        del tools, tool_choice, kwargs
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Return the next scripted planner turn."""
        del stop, run_manager, kwargs
        with self._lock:
            turn = self._turn
            self._turn += 1

        cell_id, window, target_rate = self._plan_args(messages)
        if turn == 0:
            message = self._tool_call(
                "get_cell_energy_profile",
                {"cell_id": cell_id, "date": self.dataset_date},
                turn,
            )
        elif turn == 1:
            message = self._tool_call(
                "evaluate_energy_saving_plan",
                {"cell_id": cell_id, "window": window, "target_rate": target_rate},
                turn,
            )
        elif turn == 2:
            message = self._tool_call(
                "apply_energy_saving_plan",
                {"cell_id": cell_id, "window": window, "target_rate": target_rate},
                turn,
            )
        elif turn == 3:
            plan_id = self._find_plan_id(messages)
            if plan_id is None:
                # The plan was not applied (for example the engineer rejected the change at the
                # approval gate), so the run ends without a report.
                message = AIMessage(content="The plan was not applied; no report is generated.")
            else:
                message = self._tool_call(
                    "generate_energy_saving_report",
                    {"plan_id": plan_id},
                    turn,
                )
        else:
            message = AIMessage(content=("The energy-saving plan was applied and the report was generated."))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _plan_args(self, messages: list[BaseMessage]) -> tuple[str, str, str]:
        """Choose the sample plan carried by the accepted Task-T.

        The night-time complete request uses the macro cell's night window. A planned-outage task
        (use case 2) uses the micro cell whose available window covers the outage window, at its
        lower saving rate. A task confirmed for the second served area uses that area's cell. The
        live planner chooses from the same dataset with the same intent.
        """
        # Only the accepted task counts: the system prompt lists every dataset cell, so reading it
        # would make the region check true for every run.
        text = "\n".join(str(message.content) for message in messages if isinstance(message, HumanMessage))
        lower = text.lower()
        if "dalingshan" in lower or "大岭山" in text:
            return "cell-003", "00:00~06:00", "28"
        if "power outage" in lower or "停电" in text:
            return "cell-002", "01:00~05:00", "20"
        return "cell-001", "00:00~06:00", "30"

    def _tool_call(self, name: str, args: dict[str, object], turn: int) -> AIMessage:
        """Build one tool-calling assistant message."""
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": name,
                    "args": args,
                    "id": f"scripted-call-{turn}",
                    "type": "tool_call",
                }
            ],
        )

    def _find_plan_id(self, messages: list[BaseMessage]) -> str | None:
        """Read the applied plan id from the preceding tool result, or ``None`` when it failed."""
        for message in reversed(messages):
            if not isinstance(message, ToolMessage):
                continue
            if message.name != "apply_energy_saving_plan":
                continue
            try:
                payload = json.loads(str(message.content))
            except (TypeError, ValueError):
                return None
            plan_id = payload.get("plan_id")
            return plan_id if isinstance(plan_id, str) and plan_id else None
        return None
        raise PlannerError("The scripted planner reached the report step without an apply result.")


def build_planner_model(config: SampleConfig) -> BaseChatModel:
    """Build the planner model of the configured mode.

    Args:
        config: resolved sample configuration.

    Returns:
        ``ScriptedChatModel`` in offline mode, ``ChatOpenAI`` in live mode.
    """
    if config.is_offline:
        return ScriptedChatModel(dataset_date=ran_data.dataset_date())

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
        "6. The sample default plan is cell-001, window 00:00~06:00, target rate 30%. Prefer a "
        "dataset cell whose region matches the accepted Task-T area. You may choose another "
        "dataset cell/window/rate when the accepted task or tool results justify it; the tools "
        "reject an invalid order or an infeasible plan.\n"
        "7. For a planned-outage task (a site list plus a background event), prefer the cell whose "
        "available window covers the outage and keep its target rate within the cell's maximum "
        "saving rate.\n"
        "8. Do not invent cells, windows, rates, or plan ids.\n"
        "9. When the report is generated, return a short confirmation and stop calling tools."
    )


def build_planner_input(
    task_prompt: str,
    negotiation_context: Mapping[str, object] | None,
) -> str:
    """Build the human message that carries the accepted Task-T into the graph."""
    context_block = ""
    if negotiation_context:
        context_block = (
            "\nNegotiation context (accepted):\n"
            f"{json.dumps(negotiation_context, ensure_ascii=False, sort_keys=True)}\n"
        )
    return (
        "Execute the accepted task.\n"
        f"Accepted Task-T:\n{task_prompt}\n"
        f"{context_block}\n"
        "Follow the tool rules and stop once the report is generated."
    )


__all__ = [
    "PlannerError",
    "ScriptedChatModel",
    "build_planner_input",
    "build_planner_model",
    "build_system_prompt",
    "describe_dataset",
]
