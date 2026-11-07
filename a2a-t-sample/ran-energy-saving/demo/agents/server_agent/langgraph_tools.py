"""LangChain tools and the business guard of the LangGraph RAN NMS execution agent.

The pure tool functions live in :mod:`agents.server_agent.tools`; this module adapts them to
LangChain tools and enforces the order the sample's plan requires. The guard is per execution:
each accepted Task gets a fresh ``PlanGuard`` and a fresh set of tool closures, so concurrent Tasks
cannot observe each other's profile/evaluation/apply state.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool, tool

from demo.agents.server_agent.tools import (
    apply_energy_saving_plan,
    evaluate_energy_saving_plan,
    generate_energy_saving_report,
    get_cell_energy_profile,
    modify_energy_saving_plan,
)

#: Structured error code of a tool called out of order.
TOOL_ORDER_VIOLATION: str = "tool_order_violation"

#: Structured error returned when the engineer rejects a plan at the approval gate.
APPROVAL_DENIED: str = "approval_denied"

#: Gate signature: cell, window, rate, evaluation -> approved?
ApprovalGate = Callable[[str, str, str, Mapping[str, object] | None], Awaitable[bool]]

#: Report tool name.
REPORT_TOOL_NAME: str = "generate_energy_saving_report"

#: Tool that updates an existing plan in place for a Modify Task.
MODIFY_TOOL_NAME: str = "modify_energy_saving_plan"


@dataclass
class PlanGuard:
    """Per-execution state used to enforce the plan order.

    The execution covers a cluster, not a single cell: a planner may read several cell profiles and
    evaluate more than one candidate while it chooses the plan. Profiles and evaluations are
    therefore kept per cell / per plan; remembering only the last one would reject a valid
    evaluation or application of a plan that another candidate was inspected after (the live
    planner's exploration of ``cell-001`` and ``cell-002``).
    """

    profiles: dict[str, dict[str, object]] = field(default_factory=dict)
    evaluations: dict[tuple[str, str, str], dict[str, object]] = field(default_factory=dict)
    applied: dict[str, object] | None = None
    report: dict[str, object] | None = None
    tool_calls: list[str] = field(default_factory=list)
    #: One task applies one plan. Keeping this separate from ``applied`` means a profile re-read
    #: cannot silently open a second apply (and a second human approval) in the same task.
    applied_any: bool = False

    def record(self, name: str) -> None:
        """Record one tool call name (diagnostics and tests)."""
        self.tool_calls.append(name)


def _json(result: dict[str, object]) -> str:
    """Serialize one tool result (LangChain tools return text)."""
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


def _violation(name: str, message: str, **details: object) -> str:
    """Return one structured tool-order error."""
    return _json(
        {
            "ok": False,
            "error_code": TOOL_ORDER_VIOLATION,
            "error_message": message,
            **details,
        }
    )


def _rate_key(value: object) -> str:
    """Normalize one rate value for comparison (``30``, ``30%`` and ``30.0`` match)."""
    text = str(value).strip().removesuffix("%").strip()
    try:
        return f"{float(text):g}"
    except ValueError:
        return text


def build_execution_tools(
    guard: PlanGuard,
    *,
    approval_gate: ApprovalGate | None = None,
) -> list[BaseTool]:
    """Build the four guarded RAN NMS tools of one execution.

    Args:
        guard: per-execution order state.
        approval_gate: optional async human gate called before a plan is applied. A rejection
            returns the structured ``approval_denied`` error and writes nothing.

    Returns:
        The tools in the order the planner is expected to call them.
    """

    @tool("get_cell_energy_profile")
    def get_cell_energy_profile_tool(cell_id: str, date: str) -> str:
        """Read the 24-hour load and energy profile of one radio cell.

        Call this first, before evaluating any plan.
        """
        guard.record("get_cell_energy_profile")
        result = get_cell_energy_profile(cell_id, date)
        if result.get("ok") is True:
            cell_key = str(cell_id)
            guard.profiles[cell_key] = result
            # Re-reading a cell invalidates the evaluations and the applied plan derived from
            # older data of that cell. Reading another cell must not: the planner compares cells
            # before it evaluates or applies one of them.
            guard.evaluations = {
                key: value for key, value in guard.evaluations.items() if key[0] != cell_key
            }
            if (
                not guard.applied_any
                and guard.applied is not None
                and str(guard.applied.get("cell_id")) == cell_key
            ):
                guard.applied = None
                guard.report = None
        return _json(result)

    @tool("evaluate_energy_saving_plan")
    def evaluate_energy_saving_plan_tool(cell_id: str, window: str, target_rate: str) -> str:
        """Evaluate an energy-saving plan without applying it.

        Requires the profile of the same cell to have been read first.
        """
        guard.record("evaluate_energy_saving_plan")
        if str(cell_id) not in guard.profiles:
            return _violation(
                "evaluate_energy_saving_plan",
                "Read get_cell_energy_profile for this cell before evaluating a plan.",
                cell_id=str(cell_id),
            )
        result = evaluate_energy_saving_plan(cell_id, window, target_rate)
        if result.get("ok") is True:
            guard.evaluations[(str(cell_id), str(window), _rate_key(target_rate))] = result
        return _json(result)

    @tool("apply_energy_saving_plan")
    async def apply_energy_saving_plan_tool(cell_id: str, window: str, target_rate: str) -> str:
        """Apply a feasible energy-saving plan and return its plan_id.

        Requires the same plan to have been evaluated successfully first, and, when the deployment
        configures a human gate, the engineer's approval before anything is written.
        """
        guard.record("apply_energy_saving_plan")
        if guard.applied_any:
            # A live planner sometimes explores several candidates and tries to apply another
            # plan after the first one. That would open a second approval gate and delay the
            # report; the task contract is one applied plan followed by its report.
            return _violation(
                "apply_energy_saving_plan",
                "A plan is already applied in this task; generate its report instead.",
                plan_id=str((guard.applied or {}).get("plan_id") or ""),
            )
        evaluation = guard.evaluations.get((str(cell_id), str(window), _rate_key(target_rate)))
        if evaluation is None:
            return _violation(
                "apply_energy_saving_plan",
                "Evaluate this exact cell/window/rate successfully before applying it.",
                cell_id=str(cell_id),
                window=str(window),
                target_rate=str(target_rate),
            )
        if approval_gate is not None:
            approved = await approval_gate(cell_id, window, target_rate, evaluation)
            if not approved:
                return _json(
                    {
                        "ok": False,
                        "error_code": APPROVAL_DENIED,
                        "message": "The engineer did not approve the change; nothing was applied.",
                        "cell_id": str(cell_id),
                        "window": str(window),
                        "target_rate": str(target_rate),
                    }
                )
        result = apply_energy_saving_plan(cell_id, window, target_rate)
        if result.get("ok") is True:
            guard.applied = result
            guard.applied_any = True
            guard.report = None
        return _json(result)

    @tool("modify_energy_saving_plan")
    async def modify_energy_saving_plan_tool(
        plan_id: str,
        cell_id: str,
        window: str,
        target_rate: str,
    ) -> str:
        """Update an existing energy-saving plan in place and return its plan_id.

        Requires the same cell/window/rate to have been evaluated successfully first, and, when the
        deployment configures a human gate, the engineer's approval before anything is written.
        """
        guard.record("modify_energy_saving_plan")
        if guard.applied_any:
            return _violation(
                "modify_energy_saving_plan",
                "A plan is already applied or modified in this task; generate its report instead.",
                plan_id=str((guard.applied or {}).get("plan_id") or ""),
            )
        if str(cell_id) not in guard.profiles:
            return _violation(
                "modify_energy_saving_plan",
                "Read get_cell_energy_profile for this cell before modifying a plan.",
                cell_id=str(cell_id),
            )
        evaluation = guard.evaluations.get((str(cell_id), str(window), _rate_key(target_rate)))
        if evaluation is None:
            return _violation(
                "modify_energy_saving_plan",
                "Evaluate this exact cell/window/rate successfully before modifying it.",
                cell_id=str(cell_id),
                window=str(window),
                target_rate=str(target_rate),
            )
        if approval_gate is not None:
            approved = await approval_gate(cell_id, window, target_rate, evaluation)
            if not approved:
                return _json(
                    {
                        "ok": False,
                        "error_code": APPROVAL_DENIED,
                        "message": "The engineer did not approve the change; nothing was modified.",
                        "plan_id": str(plan_id),
                        "cell_id": str(cell_id),
                        "window": str(window),
                        "target_rate": str(target_rate),
                    }
                )
        result = modify_energy_saving_plan(plan_id, cell_id, window, target_rate)
        if result.get("ok") is True:
            guard.applied = result
            guard.applied_any = True
            guard.report = None
        return _json(result)

    @tool("generate_energy_saving_report")
    def generate_energy_saving_report_tool(plan_id: str) -> str:
        """Generate the final report of an applied plan.

        Requires the exact plan_id returned by apply_energy_saving_plan.
        """
        guard.record("generate_energy_saving_report")
        if guard.applied is None or str(guard.applied.get("plan_id")) != str(plan_id):
            return _violation(
                "generate_energy_saving_report",
                "Apply the plan first and use the exact plan_id returned by apply.",
                plan_id=str(plan_id),
            )
        result = generate_energy_saving_report(plan_id)
        if result.get("ok") is True:
            guard.report = result
        return _json(result)

    return [
        get_cell_energy_profile_tool,
        evaluate_energy_saving_plan_tool,
        apply_energy_saving_plan_tool,
        modify_energy_saving_plan_tool,
        generate_energy_saving_report_tool,
    ]


def tool_result_from_text(text: Any) -> dict[str, object]:
    """Parse one tool result string into a dict (``{}`` for malformed content)."""
    try:
        parsed = json.loads(str(text))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


__all__ = [
    "APPROVAL_DENIED",
    "ApprovalGate",
    "PlanGuard",
    "REPORT_TOOL_NAME",
    "TOOL_ORDER_VIOLATION",
    "build_execution_tools",
    "tool_result_from_text",
]
