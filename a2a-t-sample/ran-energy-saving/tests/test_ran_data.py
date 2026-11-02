"""Tests for the in-memory RAN dataset and the process-local plan store."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from demo import ran_data


@pytest.fixture(autouse=True)
def _reset_plan_store() -> Iterator[None]:
    """Keep every test independent of the plan store state."""
    ran_data.reset()
    yield
    ran_data.reset()


def test_dataset_has_at_least_two_cells_with_complete_curves() -> None:
    cells = ran_data.load_cells()

    assert len(cells) >= 2
    for cell in cells:
        assert len(cell.hourly) == ran_data.HOURS_PER_DAY
        assert [sample.hour for sample in cell.hourly] == list(range(ran_data.HOURS_PER_DAY))
        assert cell.available_windows
        assert cell.max_saving_rate > 0
        assert cell.capacity.max_throughput_mbps > 0
        assert cell.capacity.sla_min_throughput_mbps > 0


def test_dataset_date_matches_the_fake_model_contract() -> None:
    assert ran_data.dataset_date() == "2026-10-22"


def test_get_cell_returns_the_dataset_cell() -> None:
    cell = ran_data.get_cell("cell-001")

    assert cell is not None
    assert cell.cell_id == "cell-001"
    assert ran_data.get_cell("cell-999") is None


def test_unknown_cell_raises_a_typed_error() -> None:
    with pytest.raises(ran_data.UnknownCellError, match="cell-999"):
        ran_data.get_cell_or_raise("cell-999")


@pytest.mark.parametrize(
    ("window", "canonical", "hours"),
    [
        ("00:00~06:00", "00:00~06:00", (0, 1, 2, 3, 4, 5)),
        ("23:00~24:00", "23:00~24:00", (23,)),
        ("1:00~5:00", "01:00~05:00", (1, 2, 3, 4)),
    ],
)
def test_parse_window_normalizes_and_expands_hours(
    window: str, canonical: str, hours: tuple[int, ...]
) -> None:
    parsed_canonical, parsed_hours = ran_data.parse_window(window)

    assert parsed_canonical == canonical
    assert parsed_hours == hours


@pytest.mark.parametrize(
    "window",
    ["00:00-06:00", "06:00~00:00", "00:30~06:30", "24:00~24:00", "00:00~06:00~07:00"],
)
def test_parse_window_rejects_malformed_windows(window: str) -> None:
    with pytest.raises(ran_data.InvalidPlanError):
        ran_data.parse_window(window)


def test_evaluate_feasible_plan_estimates_the_saving() -> None:
    evaluation = ran_data.evaluate_plan("cell-001", "00:00~06:00", "30")

    assert evaluation.feasible
    assert evaluation.reasons == ()
    assert evaluation.recommendation is None
    assert evaluation.baseline_energy_kwh == pytest.approx(108.3)
    assert evaluation.estimated_saving_kwh == pytest.approx(32.49)
    assert evaluation.estimated_saving_percent == pytest.approx(30.0)
    assert evaluation.kpi_impact["affected_hours"] == 6
    assert evaluation.kpi_impact["meets_sla"] is True


def test_evaluate_rejects_an_unavailable_window() -> None:
    evaluation = ran_data.evaluate_plan("cell-001", "12:00~14:00", "30")

    assert not evaluation.feasible
    assert any("not available" in reason for reason in evaluation.reasons)
    assert evaluation.recommendation is not None
    assert "00:00~06:00" in evaluation.recommendation


def test_evaluate_rejects_a_rate_above_the_cell_maximum() -> None:
    evaluation = ran_data.evaluate_plan("cell-001", "00:00~06:00", "40")

    assert not evaluation.feasible
    assert any("maximum" in reason for reason in evaluation.reasons)


def test_apply_plan_writes_state_and_sequences_plan_ids() -> None:
    first = ran_data.apply_plan("cell-001", "00:00~06:00", "30")
    second = ran_data.apply_plan("cell-001", "00:00~06:00", "30")

    assert first.plan_id == "plan-cell-001-001"
    assert second.plan_id == "plan-cell-001-002"
    assert ran_data.get_plan(first.plan_id) == first
    assert ran_data.list_plan_ids() == (first.plan_id, second.plan_id)
    assert first.status == ran_data.PLAN_STATUS_APPLIED
    assert first.remaining_energy_kwh == pytest.approx(108.3 - 32.49)


def test_modify_plan_updates_the_same_plan_in_place() -> None:
    plan = ran_data.apply_plan("cell-002", "01:00~05:00", "10")

    updated = ran_data.modify_plan(plan.plan_id, "cell-002", "01:00~05:00", "20")

    assert updated.plan_id == plan.plan_id
    assert updated.status == ran_data.PLAN_STATUS_MODIFIED
    assert updated.target_rate == 20
    assert ran_data.get_plan(plan.plan_id) == updated
    assert ran_data.list_plan_ids() == (plan.plan_id,)


def test_modify_infeasible_plan_leaves_the_stored_plan_unchanged() -> None:
    plan = ran_data.apply_plan("cell-002", "01:00~05:00", "10")

    with pytest.raises(ran_data.PlanInfeasibleError):
        ran_data.modify_plan(plan.plan_id, "cell-002", "01:00~05:00", "30")

    assert ran_data.get_plan(plan.plan_id) == plan


def test_apply_infeasible_plan_raises_and_writes_nothing() -> None:
    with pytest.raises(ran_data.PlanInfeasibleError) as error:
        ran_data.apply_plan("cell-001", "12:00~14:00", "30")

    assert error.value.evaluation.feasible is False
    assert ran_data.list_plans() == ()


def test_get_plan_or_raise_rejects_unknown_plan() -> None:
    with pytest.raises(ran_data.UnknownPlanError, match="plan-cell-001-001"):
        ran_data.get_plan_or_raise("plan-cell-001-001")


def test_reset_clears_plans_and_sequences() -> None:
    ran_data.apply_plan("cell-001", "00:00~06:00", "30")

    ran_data.reset()

    assert ran_data.list_plans() == ()
    replay = ran_data.apply_plan("cell-001", "00:00~06:00", "30")
    assert replay.plan_id == "plan-cell-001-001"


def test_evaluation_and_plan_are_json_serializable() -> None:
    evaluation = ran_data.evaluate_plan("cell-002", "01:00~05:00", "20")
    plan = ran_data.apply_plan("cell-002", "01:00~05:00", "20")

    json.dumps(evaluation.to_dict())
    json.dumps(plan.to_dict())
