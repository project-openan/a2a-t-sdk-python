"""Business evaluation of the sample's counter-proposed energy-saving plans.

The protocol policy only knows that a counter proposal may carry a plan; this evaluator decides
whether that plan is feasible against the demonstration dataset and how to revise it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from protocol.negotiation_policy import PlanAssessment, SampleProposal

from demo import ran_data

#: Fallback plan used when a counter proposal carries no usable sample plan.
DEFAULT_PLAN: Final[SampleProposal] = SampleProposal(
    cell_id="cell-001", window="00:00~06:00", target_rate="30"
)

#: Preset revisions when the counter proposal carries no sample plan.
PRESET_REVISIONS: Final[dict[int, SampleProposal]] = {
    2: DEFAULT_PLAN,
    3: SampleProposal(cell_id="cell-001", window="23:00~24:00", target_rate="30"),
}


@dataclass(frozen=True)
class RanDataPlanEvaluator:
    """Evaluate plans against the in-memory RAN dataset of the demonstration."""

    def evaluate(self, plan: SampleProposal) -> PlanAssessment:
        """Return whether ``plan`` is feasible and the server's revised proposal."""
        try:
            evaluation = ran_data.evaluate_plan(plan.cell_id, plan.window, plan.target_rate)
        except ran_data.RanDataError as exc:
            return PlanAssessment(feasible=False, reasons=str(exc), revised=_revise_plan(plan))
        if evaluation.feasible:
            return PlanAssessment(feasible=True, reasons="", revised=plan)
        reasons = "; ".join(evaluation.reasons) or "the dataset rejected the plan"
        return PlanAssessment(feasible=False, reasons=reasons, revised=_revise_plan(plan))

    def preset(self, round_number: int) -> SampleProposal:
        """Return the preset plan of one round."""
        return PRESET_REVISIONS.get(round_number, DEFAULT_PLAN)


def _is_feasible(plan: SampleProposal) -> bool:
    """Evaluate one sample plan against the RAN dataset; malformed plans are infeasible."""
    try:
        return ran_data.evaluate_plan(plan.cell_id, plan.window, plan.target_rate).feasible
    except ran_data.RanDataError:
        return False


def _revise_plan(plan: SampleProposal) -> SampleProposal:
    """Return a feasible plan derived from an infeasible counter proposal.

    The revision keeps the requested cell and target rate where possible, but moves the plan to the
    cell's first available window and caps the rate so the cell's maximum saving rate and SLA floor
    both hold.
    """
    cell = ran_data.get_cell(plan.cell_id)
    if cell is None:
        return DEFAULT_PLAN
    try:
        requested_rate = ran_data.parse_target_rate(plan.target_rate)
    except ran_data.InvalidPlanError:
        requested_rate = float(DEFAULT_PLAN.target_rate)
    sla_rate_cap = 100.0 * (
        1.0 - cell.capacity.sla_min_throughput_mbps / cell.capacity.max_throughput_mbps
    )
    rate = min(requested_rate, cell.max_saving_rate, sla_rate_cap)
    if rate <= 0:
        return DEFAULT_PLAN
    rendered_rate = f"{rate:g}"
    for window in cell.available_windows:
        candidate = SampleProposal(
            cell_id=cell.cell_id, window=window, target_rate=rendered_rate
        )
        if _is_feasible(candidate):
            return candidate
    return DEFAULT_PLAN
