"""Test-only deterministic planner model for the RAN execution graph."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.language_models.base import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import PrivateAttr


class PlannerError(RuntimeError):
    """Raised when a scripted turn is invalid."""

class ScriptedChatModel(BaseChatModel):
    """Deterministic chat model that emits the sample's four RAN NMS tool calls in order.

    The model is stateless with respect to the graph messages except for its turn counter; the
    ``generate_energy_saving_report`` call reads the ``plan_id`` from the preceding
    ``apply_energy_saving_plan`` tool result, so the script follows the real tool output instead of
    guessing an id.
    """

    dataset_date: str = ""
    #: Task-T operation type of this execution (``Create`` or ``Modify``).
    operation: str = "Create"
    #: Plan id a Modify execution updates in place.
    existing_plan_id: str = ""
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
        modify = self.operation.strip().lower() == "modify"
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
            if modify:
                message = self._tool_call(
                    "modify_energy_saving_plan",
                    {
                        "plan_id": self.existing_plan_id,
                        "cell_id": cell_id,
                        "window": window,
                        "target_rate": target_rate,
                    },
                    turn,
                )
            else:
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
                message = AIMessage(
                    content=(
                        "The plan was not modified; no report is generated."
                        if modify
                        else "The plan was not applied; no report is generated."
                    )
                )
            else:
                message = self._tool_call(
                    "generate_energy_saving_report",
                    {"plan_id": plan_id},
                    turn,
                )
        else:
            message = AIMessage(
                content=(
                    "The energy-saving plan was modified and the report was generated."
                    if modify
                    else "The energy-saving plan was applied and the report was generated."
                )
            )
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
        if "region b" in lower or "区域 b" in lower:
            return "cell-003", "00:00-06:00", "28"
        if "power outage" in lower or "停电" in text:
            return "cell-002", "01:00-05:00", "20"
        if (
            "site 1" in lower
            or "site 2" in lower
            or "站点1" in text
            or "站点2" in text
            or "nres_xcs_6102937" in lower
            or "lte_26441_lnr_ocl" in lower
        ):
            # The two-site baseline is the micro cell's normal plan; the planned-outage Modify
            # raises the same plan to the cell's maximum saving rate during the outage window.
            return "cell-002", "01:00-05:00", "10"
        return "cell-001", "00:00-06:00", "30"

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
            if message.name not in ("apply_energy_saving_plan", "modify_energy_saving_plan"):
                continue
            try:
                payload = json.loads(str(message.content))
            except (TypeError, ValueError):
                return None
            plan_id = payload.get("plan_id")
            return plan_id if isinstance(plan_id, str) and plan_id else None
        return None
        raise PlannerError("The scripted planner reached the report step without an apply result.")
