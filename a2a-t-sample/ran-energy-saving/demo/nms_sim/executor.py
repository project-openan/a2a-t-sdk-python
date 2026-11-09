"""Deterministic business execution of the simulator.

The simulator reuses the sample's RAN dataset and plan tools; it does not run a LangGraph planner.
The report keeps the same shape the real NMS and the sample's RAN Agent publish, so the client's
artifact handling is exercised end to end.
"""

from __future__ import annotations

from typing import Final

from demo import ran_data
from demo.nms_sim.intent import ParsedIntent

#: Simulator cell used for the accepted test region.
SIM_CELL_ID: Final[str] = "cell-001"

#: Energy-saving rate the simulator applies for any accepted throughput target.
SIM_TARGET_RATE: Final[str] = "30"


def structured_output(intent: ParsedIntent) -> dict[str, object]:
    """Build the vendor-style structured Task creation payload."""
    hours = _effective_periods(intent.window)
    return {
        "Operation type": intent.operation,
        "Energy saving region": intent.region,
        "Guaranteed throughput target by zone": [
            {
                "Guaranteed throughput target": f"{intent.rate_mbps:g} Mbps",
                "Effective time period": hours,
            }
        ],
        "Energy saving time": hours,
        "Energy saving cell RAT": "NR",
        "Energy saving mode": "Conservative",
        "Scenario": "coverage",
    }


def apply_intent(intent: ParsedIntent, *, allow_any_window: bool = False) -> dict[str, object]:
    """Apply the intent on the simulator cell and return the report payload.

    Raises:
        demo.ran_data.RanDataError: when the sample dataset rejects the plan. The simulator only
            calls this after its own validation, so a failure indicates a simulator bug.
    """
    plan = ran_data.apply_plan(
        SIM_CELL_ID, intent.window, SIM_TARGET_RATE, allow_any_window=allow_any_window
    )
    return {
        "plan_id": plan.plan_id,
        "cell_id": plan.cell_id,
        "cell_name": plan.cell_name,
        "region": intent.region,
        "date": plan.date,
        "window": plan.window,
        "target_rate": plan.target_rate,
        "status": plan.status,
        "energy": {
            "baseline_kwh": plan.baseline_energy_kwh,
            "saved_kwh": plan.estimated_saving_kwh,
            "remaining_kwh": plan.remaining_energy_kwh,
            "saving_percent": plan.target_rate,
        },
        "kpi_impact": dict(plan.kpi_impact),
        "summary": (
            f"Plan {plan.plan_id} applied to cell {plan.cell_id} ({plan.cell_name}): "
            f"a {plan.target_rate:g}% energy-saving plan during {plan.window} on {plan.date}, "
            f"saving an estimated {plan.estimated_saving_kwh:.2f} kWh of "
            f"{plan.baseline_energy_kwh:.2f} kWh."
        ),
        "highlights": [
            f"Region: {intent.region}.",
            f"Plan {plan.plan_id} is {plan.status} for window {plan.window}.",
            f"Guaranteed throughput: {intent.rate_mbps:g} Mbps.",
            *([f"Sites: {', '.join(intent.sites)}."] if intent.sites else []),
        ],
    }


def report_artifact(task_id: str, report: dict[str, object]) -> dict[str, object]:
    """Build the report artifact of one completed Task."""
    return {
        "artifactId": f"{task_id}-report",
        "name": "energy saving intent report",
        "parts": [
            {"data": report},
            {"text": str(report.get("summary") or "")},
        ],
        "metadata": {"taskId": task_id},
    }


def _effective_periods(window: str) -> str:
    """Render a window as the vendor's hour-by-hour period string."""
    _, hours = ran_data.parse_window(window)
    return "&".join(f"{hour:02d}:00:00-{hour:02d}:59:59" for hour in hours)
